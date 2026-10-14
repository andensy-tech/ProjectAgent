"""
Agent Execution Engine — Single LLM Brain + Tool Calling Loop

Architecture (vs old 3-layer pipeline):

  Old: 消息 → 意图理解(LLM) → 任务规划(LLM) → 动作执行(代码) → 回应生成(LLM)
                                              ↑
                                        ReAct 循环(单独)

  New: 消息 → [上下文注入] → [LLM 单核推理 ←→ 工具执行循环] → 回应

Design principles (from the "单脑+外设" architectural prompt):
  - 没有中间模块。理解、规划、决策、执行、回应，全部在同一个 LLM 推理过程中完成
  - LLM 是"大脑"，代码层只负责提供"工具箱"和"基础设施"
  - 工具执行是循环而不是单次: LLM → 工具 → 结果回来 → LLM 继续推理 → 可能再调工具
"""

import asyncio
import json
import os
import re
import uuid
import logging
import time
from typing import Dict, List, Optional, Any, AsyncGenerator, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.agent.types import AgentResponse
from app.services.agent.executor import action_executor
from app.services.agent.intent import Intent
from app.core.exceptions import MCPException
from app.services.llm import llm_router, TaskType
from app.services.memory import memory_system, search_engine
from app.agent_context import build_system_prompt
from app.mcp.capability import capability
from app.skills.scanner import skill_scanner
from app.tools.confluence_tool import _html_table_to_markdown
from app.services.agent.result_budget import fit_items_to_budget
from app.services.file_outputs import describe_file_outputs

# File extension → read tool mapping for auto-reading uploaded files
# Derived from tools/__init__.py ToolDef input_types
_FILE_EXT_TO_READ_TOOL = {
    "docx": "read_docx", "doc": "read_docx",
    "pdf": "read_pdf",
    "xlsx": "read_xlsx", "xls": "read_xlsx",
    "pptx": "read_pptx", "ppt": "read_pptx",
    "png": "read_image_metadata", "jpg": "read_image_metadata",
    "jpeg": "read_image_metadata", "gif": "read_image_metadata",
    "bmp": "read_image_metadata", "tiff": "read_image_metadata", "tif": "read_image_metadata",
    "eml": "read_eml",
    "html": "read_html", "htm": "read_html",
    "txt": "read_text_file", "log": "read_text_file", "md": "read_text_file",
    "csv": "read_text_file", "json": "read_text_file", "xml": "read_text_file",
    "yaml": "read_text_file", "ini": "read_text_file", "cfg": "read_text_file",
}

logger = logging.getLogger(__name__)


class AgentEngine:
    """Single-brain Agent engine: LLM + Function Calling loop.

    Flow:
      1. Context injection: load IDENTITY.md / SOUL.md / AGENTS.md + memory → system prompt
      2. Build messages (system + history + current user message)
      3. Define tools (skills + MCP + built-in, function calling format)
      4. LLM call with tools
      5. IF tool_calls → execute each → append tool results → goto 4
      6. ELSE → LLM content is the final response
      7. Memory update (async, non-blocking)
    """

    # Maximum tool call rounds to prevent infinite loops
    MAX_TOOL_ROUNDS = 40
    HEARTBEAT_INTERVAL = 8  # seconds between SSE keepalive events during long tool calls
    LONG_RUNNING_SKILLS = frozenset({"jira-confluence-report", "jira-cl-merge-monitor"})

    # Phase 3: 复杂任务最终回答前的自我验证提示（整个请求仅触发一次）
    VERIFY_PROMPT = (
        "在你给出最终回答之前，请先快速自我检查以下 3 点：\n"
        "1. 是否完整覆盖了用户的核心请求？有没有遗漏的方面（如未提取的列、未查询的状态）？\n"
        "2. 涉及的数据是否完整获取？若只能部分获取，是否已明确告知用户「数据不完整/仅获取到 N 条」？\n"
        "3. 回答是否清晰可读（使用表格/列表/要点，而不是冗长段落）？\n"
        "如果发现遗漏或需要补充数据，请继续调用相关工具完善；"
        "只有当你确认回答已完整且准确时，才直接输出最终答案。"
    )

    def __init__(self):
        self.action_executor = action_executor
        self.memory = memory_system
        # Pending confirmations: {execution_id: {"step": ..., "context": ...}}
        self._pending_plans: Dict[str, Dict] = {}
        # Phase 1.5: 单次请求内的工具结果缓存 {tool:args → result}（每次 process_message 重置）
        self._result_cache: Dict[str, Any] = {}

    # 写操作/状态变更工具关键词——这些绝不能缓存
    _WRITE_KEYWORDS = ("submit", "review", "patch", "create", "update", "append",
                       "write_", "upload", "delete", "remove", "set_review")

    def _is_cacheable(self, func_name: str) -> bool:
        """查询类工具可缓存；写操作/状态变更工具不可缓存。"""
        return not any(k in func_name for k in self._WRITE_KEYWORDS)

    async def _execute_with_cache(
        self,
        func_name: str,
        args: Dict[str, Any],
        context: Dict[str, Any],
        db: Optional[AsyncSession] = None,
        user_id: Optional[int] = None,
        default_provider: Optional[str] = None,
    ) -> Any:
        """带会话级缓存的工具执行（同一 tool+args 在本次请求内复用结果）。

        减少重复查询（如 get_page 相同参数反复调用、read_xlsx 反复读同一文件）。
        写操作/状态变更工具不缓存。缓存命中时结果标记 _cached=True 供附注提示。
        """
        cache_key = f"{func_name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        if self._is_cacheable(func_name) and cache_key in self._result_cache:
            cached = self._result_cache[cache_key]
            if isinstance(cached, dict):
                return {**cached, "_cached": True}
            return cached
        result = await self._execute_function_call(
            func_name, args, context, db=db, user_id=user_id, default_provider=default_provider
        )
        if self._is_cacheable(func_name) and result is not None:
            # 只缓存成功的查询结果（失败不缓存，便于下次重试）
            if isinstance(result, dict) and not (result.get("error") or result.get("type") == "error"):
                self._result_cache[cache_key] = result
            elif not isinstance(result, dict):
                self._result_cache[cache_key] = result
        return result

    def _log_stage(self, stage: str, t_start: float):
        """日志打印各阶段耗时"""
        elapsed = time.time() - t_start
        print(f"[PERF] {stage:30s} {elapsed:7.2f}s total", flush=True)

    async def _auto_read_attachments(self, file_attachments: List[Dict]) -> str:
        """Auto-read uploaded file contents using appropriate built-in tools.

        Maps file extension to the matching read tool (e.g., .pdf → read_pdf),
        executes the tool with the file's server path, and returns the content
        as a formatted string for injection into the LLM context.

        This eliminates the need for the LLM to figure out which tool to call
        when files are uploaded — the content is pre-read and available immediately.
        """
        results = []
        for f in file_attachments:
            fpath = f.get("file_path", "")
            fname = f.get("file_name", "unknown")
            if not fpath or not os.path.isfile(fpath):
                results.append(f"### {fname}\n文件不存在或路径无效")
                continue

            _, ext = os.path.splitext(fname.lower())
            tool_name = _FILE_EXT_TO_READ_TOOL.get(ext.lstrip("."))
            if not tool_name:
                results.append(f"### {fname}\n不支持自动读取此类文件（{ext}），请告知用户手动处理")
                continue

            try:
                tool_result = await self.action_executor._execute_tool(
                    tool_name, {"file_path": fpath}, {}
                )
                if isinstance(tool_result, dict):
                    content = tool_result.get("content", str(tool_result))
                else:
                    content = str(tool_result)

                # Full content is injected for uploaded files so the LLM can
                # work with complete data. The file_path is also available for
                # the LLM to re-read via call_builtin_tool if needed.

                results.append(f"### {fname} (通过 {tool_name} 自动读取)\n{content}")
            except Exception as e:
                logger.warning("Auto-read file %s failed: %s", fname, e)
                results.append(f"### {fname}\n自动读取失败: {e}")

        return "\n\n---\n\n".join(results)

    async def process_message(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        user_id: Optional[int] = None,
        db: Optional[AsyncSession] = None,
        file_attachments: List[Dict] = None,
    ) -> AsyncGenerator[AgentResponse, None]:
        """
        Process a user message through the single-brain Agent pipeline.

        No separate intent understanding / task planning modules.
        The LLM handles understanding, planning, tool selection, and
        response generation in a single reasoning loop.

        Yields:
            AgentResponse objects for SSE streaming to the frontend.
        """
        _t0 = time.time()
        execution_id = str(uuid.uuid4())
        memory_context = {}
        # 单次请求内工具结果缓存重置（同一 tool+args 本轮复用，减少重复查询）
        self._result_cache = {}

        # 立即发送 thinking 事件
        import sys; sys.stderr.write("DEBUG: process_message starting\n"); sys.stderr.flush()
        yield AgentResponse(type="thinking", content="正在思考...")
        self._log_stage("立即 thinking yield", _t0)

        # ── 1. Load memory context ──
        try:
            session_id = conversation_id or user_id or "default"
            memory_context = await self.memory.get_context(
                db=db,
                conversation_id=conversation_id,
                user_id=user_id,
                session_id=session_id,
                message=message,
            )
        except Exception:
            logger.warning("Memory context load failed", exc_info=True)
            memory_context = {
                "available_skills": self.memory.system_memory.get_skill_descriptions(),
                "available_tools": self.memory.system_memory.get_tool_descriptions(),
            }
        self._log_stage("memory context loaded", _t0)

        # Inject file attachment info into context for the system prompt
        if file_attachments:
            memory_context["attached_files"] = file_attachments
            file_names = [f.get("file_name", "") for f in file_attachments]
            memory_context["user_message_with_files"] = (
                f"{message}\n\n[附件的文件: {', '.join(file_names)}]"
            )
            # Opt 7: Auto-read file contents so LLM gets content immediately
            try:
                auto_content = await self._auto_read_attachments(file_attachments)
                if auto_content:
                    memory_context["user_message_with_files"] += (
                        f"\n\n### 文件内容预览（自动读取）\n{auto_content}"
                    )
            except Exception:
                logger.warning("Auto-read attachments failed", exc_info=True)

        # ── Auto-RAG: automatically search relevant memories ──
        # Inject relevant memories into the system prompt so the LLM
        # sees them without needing to call memory_search tool explicitly.
        if db and user_id and message.strip():
            try:
                auto_result = await self._search_memory(message, db=db, user_id=user_id)
                entries = auto_result.get("entries", [])
                if entries:
                    memory_lines = ["## 相关记忆（自动检索）"]
                    for entry in entries[:5]:
                        pattern = entry.get("pattern", "")
                        if pattern:
                            if len(pattern) > 200:
                                pattern = pattern[:197] + "..."
                            memory_lines.append(f"- {pattern}")
                    memory_context["auto_memories"] = "\n".join(memory_lines)
            except Exception:
                logger.debug("Auto-RAG search failed (non-critical)", exc_info=True)

        self._log_stage("auto-rag search", _t0)

        # Pass user_id so build_system_prompt can load per-user daily logs
        if user_id is not None:
            memory_context["_user_id"] = user_id
        # 会话标识下传工具：后台任务完成时需把结果写回该会话
        # （长任务耗时可达数十分钟，不能依赖前端轮询存活）
        if conversation_id:
            try:
                memory_context["_conversation_id"] = int(conversation_id)
            except (TypeError, ValueError):
                # 不静默：传了非法会话 id 会导致后台任务完成结果无处可写
                logger.warning(
                    "无法将会话标识 %r 解析为整数，后台任务完成结果将无法写回会话"
                    "（请检查渠道适配器传入的是否为 DB conversation.id）",
                    conversation_id)

        # ── Load per-user LLM config for provider override ──
        llm_override: Dict[str, Any] = {}
        if user_id is not None:
            try:
                from app.services.settings_service import get_llm_config as _get_llm_cfg
                user_llm_cfg = await _get_llm_cfg(user_id)
                default_provider = user_llm_cfg.get("default_llm", "commercial")
                provider_cfg = user_llm_cfg.get(default_provider, {})
                # Unmask api_key: user's saved key is stored as "***" in get_llm_config
                # We need to read it from settings_service raw instead.
                # Re-read the raw user settings to get actual values.
                from app.services.settings_service import load_user_settings
                raw = await load_user_settings(user_id)
                raw_llm = raw.get("llm", {})
                key_prefix = "commercial_" if default_provider == "commercial" else "local_"
                actual_key = raw_llm.get(f"{key_prefix}api_key", "")
                actual_base = raw_llm.get(f"{key_prefix}api_base", "") or provider_cfg.get("api_base", "")
                actual_model = raw_llm.get(f"{key_prefix}model", "") or provider_cfg.get("model", "")
                if actual_key:
                    llm_override = {
                        "default_provider": default_provider,
                        "api_key": actual_key,
                        "api_base": actual_base,
                        "model": actual_model,
                    }
                    print(f"[LLM_CFG] User {user_id} LLM override: provider={default_provider} model={actual_model} base={actual_base[:60] if actual_base else 'N/A'} key={actual_key[:8]}...")
                else:
                    # Provider not configured — fall back to provider-aware routing
                    # Try the other provider
                    other = "local" if default_provider == "commercial" else "commercial"
                    other_prefix = "local_" if default_provider == "commercial" else "commercial_"
                    other_key = raw_llm.get(f"{other_prefix}api_key", "")
                    if other_key:
                        # Notify user about silent fallback
                        yield AgentResponse(
                            type="thinking",
                            content=f"您选择的「{'商用' if default_provider == 'commercial' else '本地'}大模型」尚未配置，已自动切换到「{'本地' if other == 'local' else '商用'}大模型」。如需使用请先在设置中配置。",
                        )
                        llm_override = {
                            "default_provider": other,
                            "api_key": other_key,
                            "api_base": raw_llm.get(f"{other_prefix}api_base", ""),
                            "model": raw_llm.get(f"{other_prefix}model", ""),
                        }
                    else:
                        # Neither configured — user has no LLM config at all
                        # Do NOT fall back to .env defaults (config isolation).
                        # Instead, raise a user-friendly error.
                        print(f"[LLM_CFG] User {user_id}: no LLM config found at all")
                        yield AgentResponse(
                            type="error",
                            content="您还没有配置大模型。请先在「设置 → 大模型配置」中配置商用或本地大模型后再使用聊天功能。",
                        )
                        yield AgentResponse(type="complete")
                        return
            except Exception as exc:
                print(f"[LLM_CFG] Failed to load user LLM config for user {user_id}, using .env defaults: {exc}")

        self._log_stage("load LLM config", _t0)

        # ── 2. Build system prompt from context files + memory ──
        system_prompt = build_system_prompt(extra_context=memory_context)

        # ── Phase 1: 能力预检——告知 LLM 当前可用能力与替代资源 ──
        # 让 LLM 一开始就知道什么能做、什么不能；数据源不可用时按替代链尽力而为，
        # 而不是对未配置的系统瞎调用或直接放弃。
        try:
            configured_servers = await capability.get_servers(user_id)
            if configured_servers is not None:
                cap_desc = ", ".join(
                    f"{n}({'✓' if s.get('url') else '✗'})" for n, s in configured_servers.items()
                ) or "无"
                system_prompt += (
                    f"\n\n[能力状态] 已配置 MCP 服务器: {cap_desc}。"
                    f"\n替代资源: 内置REST工具(confluence_get_page_content, gerrit_submit_change等)、"
                    f"文档/OCR工具(read_pdf, ocr_image等)、长期记忆(memory_search)、用户上传的文件/链接。"
                    f"\n原则：当首选数据源不可用或未配置时，先思考能否用上述替代资源从力所能及处帮助用户"
                    f"（如读用户上传的文件、用记忆中的数据、给出操作步骤），而不是直接放弃。"
                    f"\n[执行原则] 复杂任务（需要多次工具调用/跨系统）请先在心中规划执行顺序"
                    f"（如：先查X → 再查Y → 最后汇总），然后逐步执行，避免盲目连续调用。"
                    f"执行受阻时先反思原因（参数/地址/数据源），再换方案；"
                    f"需要用户提供信息或确认方向时，用 ask_user 提问。"
                )
        except Exception:
            logger.debug("Capability precheck failed", exc_info=True)

        # ── 3. Build messages ──
        messages: List[Dict] = [{"role": "system", "content": system_prompt}]

        # Inject conversation history
        recent = (memory_context or {}).get("recent_messages", [])
        for m in recent[-8:]:
            role = "assistant" if m.get("role") == "assistant" else "user"
            content = m.get("content", "")
            if content:
                messages.append({"role": role, "content": content})

        # User message (with file context if any)
        user_msg = memory_context.get("user_message_with_files", message)
        messages.append({"role": "user", "content": user_msg})

        self._log_stage("build messages", _t0)

        # ── 4. Build tool definitions (function calling format) ──
        tools = await self._build_tool_definitions(memory_context)

        # ── MCP 服务器能力注入（从工具定义推导，热启动反映最新配置）──
        mcp_servers = sorted({
            name.split("__")[0] for t in tools
            for name in [t.get("function", {}).get("name", "")]
            if "__" in name
        })
        # 兜底：若工具发现全部失败（如 MCP 服务器超时），工具定义为空，
        # 此时注入用户已配置的服务器名，避免 LLM 猜测/幻觉不存在的服务器名。
        if not mcp_servers:
            try:
                configured = await capability.get_servers(user_id)
                mcp_servers = sorted(configured.keys())
            except Exception:
                logger.warning("Failed to resolve configured MCP servers for user %s", user_id)
        if mcp_servers:
            memory_context["available_mcp_servers"] = mcp_servers
        self._log_stage("build tools", _t0)

        # ── 5. LLM + Tool calling loop ──
        tool_round = 0
        last_error = None
        # Track LLM tool calls across rounds for memory update (replaces old Intent module)
        tracked_skill = None
        tracked_tool = None
        tracked_entities = {}
        tracked_tool_calls: List[Dict] = []
        # 相同 (tool, args) 的调用次数，用于收敛提示（防 LLM 死循环空转）
        attempted_calls: Dict[str, int] = {}
        # 已回传前端的产出文件路径（同一文件只回传一次，避免重复下载卡片）
        emitted_files: set = set()
        # ── Phase 1: 执行状态追踪（元认知/停滞检测/优雅退出）──
        state: Dict[str, Any] = {
            "done": [],            # 已完成的工具/步骤
            "failed": {},          # 失败工具计数 {call_key: count}
            "stall_count": 0,      # 连续无进展轮次
            "round_has_progress": False,  # 本轮是否有成功结果
            "config_error": None,  # 配置类错误（MCP未配置等），触发提前退出
            "verify_done": False,  # Phase 3: 自我验证是否已触发（每个请求一次）
        }

        # 提前退出条件：配置类错误（无需空转）；严重停滞（连续 8 轮无进展）
        # Phase 1.5: 动态轮次预算——遍历型任务（累计工具调用多且每轮并行）允许上调上限
        max_rounds = self.MAX_TOOL_ROUNDS
        while tool_round < max_rounds and not state["config_error"] and state["stall_count"] < 8:
            tool_round += 1

            # ── 遍历型任务轮次预算上调：累计调用>40 且单轮平均并行>1 时，40→80 ──
            if tool_round > 30 and max_rounds < 80 and len(tracked_tool_calls) > 40:
                max_rounds = 80
                logger.info("遍历型任务：轮次预算 %d→%d（累计工具调用 %d）", self.MAX_TOOL_ROUNDS, max_rounds, len(tracked_tool_calls))

            # ── 停滞检测：上一轮是否有进展 ──
            if state["round_has_progress"]:
                state["stall_count"] = 0
            else:
                state["stall_count"] += 1
            state["round_has_progress"] = False

            try:
                safe_override = {k: v for k, v in llm_override.items() if k != "api_key"}
                print(f"[LLM_CFG] LLM call round {tool_round}: override={safe_override}")
                # ── Phase 1: 元认知注入（不污染 messages 历史，用副本调用）──
                # 关键节点注入（连续无进展 / 预算紧张），避免每轮打扰 LLM 正常探索，
                # 同时降低向 messages 末尾注入 system 消息的 API 兼容风险。
                loop_messages = messages
                meta_status = None
                if state["stall_count"] >= 2 or (max_rounds - tool_round) <= 10:
                    meta_status = self._build_meta_status(tool_round, state, max_rounds)
                    if meta_status:
                        loop_messages = messages + [{"role": "system", "content": meta_status}]
                content, tool_calls = await llm_router.route_and_complete_with_tools(
                    task_type=TaskType.QUESTION_ANSWERING,
                    messages=loop_messages,
                    tools=tools,
                    **llm_override,
                )
            except Exception as e:
                # ── 层 B/C：上下文溢出 → 压缩恢复（reactive，Agent 对窗口无感知）──
                if self._is_context_overflow(e):
                    recovered = False
                    for compact_pass in range(self.COMPACT_MAX_PASSES):
                        keep_recent = self.COMPACT_KEEP_RECENT[compact_pass] \
                            if compact_pass < len(self.COMPACT_KEEP_RECENT) else 0
                        compacted = await self._compact_messages_for_overflow(
                            messages, keep_recent=keep_recent, user_id=user_id,
                            db=db, llm_override=llm_override,
                        )
                        if not compacted:
                            logger.warning(
                                "context overflow but nothing compactable (pass %d, round %d)",
                                compact_pass, tool_round,
                            )
                            break
                        messages = compacted
                        yield AgentResponse(
                            type="thinking",
                            content="上下文已达模型上限，已对早期对话内容做摘要压缩并暂存原始数据，继续处理…",
                        )
                        try:
                            content, tool_calls = await llm_router.route_and_complete_with_tools(
                                task_type=TaskType.QUESTION_ANSWERING,
                                messages=messages,
                                tools=tools,
                                **llm_override,
                            )
                            recovered = True
                            break
                        except Exception as e2:
                            if not self._is_context_overflow(e2):
                                logger.error("LLM retry after compact failed at round %d: %s", tool_round, e2)
                                yield AgentResponse(type="error", content=f"请求处理在第{tool_round}轮LLM调用时出错: {str(e2)}")
                                yield AgentResponse(type="complete")
                                return
                            # 仍溢出 → 下一 pass 加大压缩范围
                    if recovered:
                        pass  # 继续正常循环
                    else:
                        logger.error("context overflow unrecoverable after %d compact passes (round %d)",
                                     self.COMPACT_MAX_PASSES, tool_round)
                        yield AgentResponse(
                            type="error",
                            content="对话上下文超出模型处理能力上限，已尽力压缩仍无法继续。建议新开对话后再试。",
                        )
                        yield AgentResponse(type="complete")
                        return
                else:
                    logger.error("LLM call failed at round %d: %s", tool_round, e)
                    yield AgentResponse(type="error", content=f"请求处理在第{tool_round}轮LLM调用时出错: {str(e)}")
                    yield AgentResponse(type="complete")
                    return

            try:
                # ── LLM decided to respond directly (no more tools needed) ──
                self._log_stage(f"LLM round {tool_round} done", _t0)

                if not tool_calls:
                    # ── Phase 3: 自我验证钩子（复杂任务最终回答前自检一次）──
                    # 仅当本请求已执行过工具调用（多步任务）时触发，避免简单对话的开销。
                    if state.get("done") and tool_round > 1 and not state.get("verify_done"):
                        state["verify_done"] = True
                        verify_messages = messages + [{"role": "system", "content": self.VERIFY_PROMPT}]
                        try:
                            v_content, v_tool_calls = await llm_router.route_and_complete_with_tools(
                                task_type=TaskType.QUESTION_ANSWERING,
                                messages=verify_messages,
                                tools=tools,
                                **llm_override,
                            )
                        except Exception:
                            logger.warning("Self-verify LLM call failed, using original answer")
                            v_content, v_tool_calls = content, None
                        if v_tool_calls:
                            # LLM 自检后认为需要继续补充 → fall-through 到工具执行
                            content = v_content or ""
                            tool_calls = v_tool_calls
                        else:
                            content = v_content or content
                            tool_calls = None

                    if not tool_calls:
                        final_content = content or "已完成。"
                        # Sanitize markdown tables before sending to frontend
                        final_content = self._sanitize_markdown_tables(final_content)
                        yield AgentResponse(type="text", content=final_content)
                        yield AgentResponse(type="complete")

                        # Build Intent from tracked tool calls for memory update
                        intent_obj = Intent(
                            intent_category="execute" if tracked_skill else ("query" if tracked_tool else "other"),
                            target_skill=tracked_skill,
                            target_tool=tracked_tool,
                            entities=tracked_entities,
                        )

                        # Update memory (async, non-blocking)
                        try:
                            await self.memory.update_from_execution(
                                db=db,
                                conversation_id=conversation_id,
                                user_id=user_id,
                                session_id=conversation_id or user_id or "default",
                                message=message,
                                result=final_content,
                                intent=intent_obj,
                            )
                        except Exception:
                            logger.warning("Memory update failed", exc_info=True)

                        # Log conversation turn for memory maintenance
                        if db and user_id:
                            try:
                                await self.memory.log_conversation_turn(
                                    db=db,
                                    user_id=user_id,
                                    session_id=conversation_id or user_id or "default",
                                    conversation_id=conversation_id,
                                    user_message=message,
                                    assistant_response=final_content,
                                    tool_calls=tracked_tool_calls,
                                )
                            except Exception:
                                logger.debug("Conversation logging failed (non-critical)", exc_info=True)

                            # Check auto-distillation trigger (every N turns)
                            if conversation_id and self.memory.should_run_maintenance(conversation_id, user_id):
                                logger.info("Auto-distillation trigger hit for conv=%s, running maintenance", conversation_id)
                                try:
                                    await self.memory.run_maintenance(db, user_id, conversation_id=conversation_id)
                                except Exception:
                                    logger.warning("Auto-distillation failed", exc_info=True)

                        return

                # ── LLM returned reasoning content alongside tool calls — surface as thinking step ──
                if content:
                    yield AgentResponse(type="thinking", content=content,
                                        data={"reasoning_step": True, "step_type": "thought"})

                # ── Append assistant message with tool_calls (required by API spec) ──
                assistant_msg = {"role": "assistant", "content": content or ""}
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": tc.get("type", "function"),
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    }
                    for tc in tool_calls
                ]
                messages.append(assistant_msg)

                # ── Execute tool calls ──
                # Parse all tool call info upfront for parallel execution
                call_infos = []
                for tc in tool_calls:
                    func_name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        args = {}
                    logger.debug("Tool call: %s args=%s", func_name, args)
                    call_infos.append((tc, func_name, args))

                # ── Phase 2: ask_user 协作工具 ──
                # LLM 调用 ask_user → 暂停当前循环，向用户提问，等待答复后继续。
                # assistant(tool_calls) 消息已在上方 append，这里补 tool 占位结果保证消息序列完整。
                ask_infos = [(tc, fn, args) for tc, fn, args in call_infos if fn == "ask_user"]
                if ask_infos:
                    tc, fn, args = ask_infos[0]
                    question = args.get("question") or "请提供更多信息"
                    options = args.get("options") or []
                    yield AgentResponse(
                        type="ask_user",
                        content=question,
                        actions=[{"label": str(o), "value": str(o)} for o in options[:4]],
                    )
                    # tool 结果占位：告知 LLM 已提问等待答复（用户答复作为下一轮 user 消息进入）
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(
                            {"message": "已向用户提问，正在等待用户答复", "success": True},
                            ensure_ascii=False,
                        ),
                    })
                    tracked_tool_calls.append({"name": "ask_user", "arguments": args})
                    yield AgentResponse(type="complete")

                    # 记忆记录本轮提问（用户答复随下一条消息继续）
                    if db and user_id:
                        try:
                            await self.memory.log_conversation_turn(
                                db=db,
                                user_id=user_id,
                                session_id=conversation_id or user_id or "default",
                                conversation_id=conversation_id,
                                user_message=message,
                                assistant_response=f"[询问用户] {question}",
                                tool_calls=tracked_tool_calls,
                            )
                        except Exception:
                            logger.debug("Conversation logging failed (non-critical)", exc_info=True)
                    return

                n = len(call_infos)
                if n == 1:
                    # Single tool: sequential flow with per-call heartbeat
                    tc, func_name, args = call_infos[0]
                    yield AgentResponse(type="thinking", content=f"正在执行 {func_name}...")

                    result = None
                    async for item in self._execute_with_heartbeat(
                        func_name, args, memory_context, db=db, user_id=user_id,
                        default_provider=llm_override.get("default_provider"),
                    ):
                        if item == "heartbeat":
                            yield AgentResponse(type="thinking", content=f"正在执行 {func_name}...（持续进行中）")
                        else:
                            result = item
                    tool_exec_results = [((tc, func_name, args), result)]
                else:
                    # Multiple tools: parallel execution with asyncio.gather
                    names = ", ".join(fn for _, fn, _ in call_infos)
                    yield AgentResponse(type="thinking", content=f"正在并行执行 {n} 个工具调用: {names}")

                    exec_tasks = [
                        asyncio.create_task(
                            self._execute_with_cache(fn, a, memory_context, db=db, user_id=user_id,
                                                      default_provider=llm_override.get("default_provider"))
                        )
                        for _, fn, a in call_infos
                    ]

                    remaining = set(range(n))
                    while remaining:
                        done_now = set(i for i in remaining if exec_tasks[i].done())
                        remaining -= done_now
                        if remaining:
                            yield AgentResponse(type="thinking",
                                content=f"正在并行执行 {len(remaining)}/{n} 个工具调用...（持续进行中）")
                            await asyncio.wait(
                                [exec_tasks[i] for i in remaining],
                                timeout=self.HEARTBEAT_INTERVAL,
                            )

                    tool_exec_results = []
                    for i, (tc, func_name, args) in enumerate(call_infos):
                        try:
                            result = exec_tasks[i].result()
                        except Exception as e:
                            logger.error("Tool call %s failed: %s", func_name, e)
                            result = {"error": str(e)}
                        tool_exec_results.append(((tc, func_name, args), result))

                # ── Process all tool results (tracking, format, append) ──
                try:
                    for (tc, func_name, args), result in tool_exec_results:
                        # Yield observation summary for reasoning chain
                        if result is not None:
                            obs = self._summarize_observation(result, func_name)
                            yield AgentResponse(type="thinking", content=obs,
                                                data={"reasoning_step": True, "step_type": "observation", "tool": func_name})

                        # ── 产出文件回传：技能/内置工具生成的文件 → 前端下载卡片 ──
                        # 文件落在服务端路径（技能目录 / /tmp / 用户指定路径），前端拿到的是
                        # 签名下载链接（见 services/file_outputs.py），同一文件只回传一次。
                        for file_info in describe_file_outputs(
                            result,
                            skill_name=args.get("skill_name") if func_name == "execute_skill" else None,
                            user_id=user_id,
                        ):
                            if file_info["file_path"] in emitted_files:
                                continue
                            emitted_files.add(file_info["file_path"])
                            yield AgentResponse(
                                type="file",
                                content=f"已生成文件：{file_info['file_name']}",
                                data=file_info,
                            )

                        # ── 后台任务提交：透传给前端轮询（完成后回传产出文件）──
                        if isinstance(result, dict) and result.get("type") == "task_submitted":
                            yield AgentResponse(
                                type="task_submitted",
                                content=result.get("message") or "任务已提交到后台执行",
                                data={
                                    "task_id": result.get("task_id"),
                                    "skill_name": args.get("skill_name", ""),
                                },
                            )

                        # Track execution context for memory update (replaces old Intent module)
                        if func_name == "execute_skill":
                            tracked_skill = args.get("skill_name")
                            # Fallback: skill failure → direct MCP tool retry
                            if isinstance(result, dict) and (result.get("error") or result.get("success") is False or result.get("type") == "error"):
                                fallback = await self._try_tool_fallback(message, memory_context,
                                                                         skill_name=tracked_skill, user_id=user_id)
                                if fallback:
                                    result = {"success": True, "message": fallback}
                        elif func_name == "call_mcp_tool":
                            tracked_tool = f"{args.get('server', '')}/{args.get('tool', '')}"
                        elif func_name == "call_builtin_tool":
                            tracked_tool = args.get("tool_name")
                        elif "__" in func_name:
                            # Direct MCP tool call (e.g., jira__search_issues)
                            parts = func_name.split("__", 1)
                            if len(parts) == 2:
                                tracked_tool = f"{parts[0]}/{parts[1]}"

                        # Extract entity-like params from any tool call.
                        # Direct MCP calls pass params at top-level, not nested under "params".
                        raw_params = args.get("params", {}) or {}
                        params = raw_params if isinstance(raw_params, dict) else {}
                        if not params:
                            params = {k: v for k, v in args.items()
                                      if isinstance(v, (str, int, float)) and len(str(v)) < 100
                                      and k not in ("skill_name", "server", "tool", "tool_name")}
                        for key, value in params.items():
                            if isinstance(value, (str, int, float)) and len(str(value)) < 100:
                                tracked_entities[key] = value

                        # Track tool calls for conversation logging
                        tracked_tool_calls.append({"name": func_name, "arguments": args})

                        # Format tool results for MCP/builtin tools as structured markdown
                        if func_name in ("call_mcp_tool", "call_builtin_tool") or "__" in func_name:
                            # A4: 评论类结果 → 预算化展示（最新优先）+ 超预算 spill（async，主循环内可 await）
                            if isinstance(result, dict) and isinstance(result.get("comments"), list) and result.get("comments"):
                                result_str = await self._format_issue_comments_for_llm(
                                    result, user_id=user_id, source_tool=func_name, source_args=args
                                )
                            else:
                                formatted = self._format_tool_result(result)
                                # Confluence 表格结果已由各自的格式化函数内部截断，直接使用
                                if isinstance(result, dict) and (
                                    isinstance(result.get("matches"), list)
                                    or isinstance(result.get("cells"), list)
                                    or isinstance(result.get("tables"), list)
                                ):
                                    result_str = formatted
                                elif len(formatted) <= self.RESULT_GATE_BUDGET:
                                    # 预算内直接使用（原 5000 过严，预算化后放宽到统一门阈值）
                                    result_str = formatted
                                else:
                                    # A5 统一结果门：超预算结果 spill 原文 + 头尾截断附可查回提示
                                    result_str = await self._gate_oversized_result(
                                        formatted, result, user_id=user_id,
                                        source_tool=func_name, source_args=args,
                                    )
                            # Debug: log format conversion for Jira issue detail responses
                            if func_name in ("jira__get_issue", "jira__search_issues") or func_name.endswith("/get_issue"):
                                dbg = result_str[:300].replace("\n", "\\n")
                                logger.info("FORMAT result for %s (%d chars): %s", func_name, len(result_str), dbg)
                                if "customfield_10700" in result_str or "customfield_10107" in result_str:
                                    match = "customfield_10700" in result_str and "customfield_10107" in result_str
                                    logger.info("FORMAT contains converted custom fields: %s, total=%d chars",
                                                "YES" if match else "partial", len(result_str))
                        else:
                            result_str = self._truncate_result(result, max_len=5000)

                        # ── Phase 1: 进度追踪 + 失败分类 + 收敛/反思提示 ──
                        call_key = f"{func_name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
                        is_failure = isinstance(result, dict) and (
                            result.get("error") or result.get("type") == "error" or result.get("success") is False
                        )
                        # ── 缓存命中提示（同一 tool+args 本轮复用；重复查询时加强）──
                        if isinstance(result, dict) and result.get("_cached"):
                            dup = attempted_calls.get(call_key, 0)
                            if dup >= 1:
                                result_str += (
                                    "\n\n（⚠️ 你已多次查询该数据（本轮第 %d 次命中缓存）。"
                                    "请直接使用已有结果，不要重复调用相同查询；如需最新数据请向用户说明）" % (dup + 1)
                                )
                            else:
                                result_str += "\n\n（⚠️ 该数据来自本轮会话缓存，未重新查询）"
                        if is_failure:
                            err_type = self._classify_error(result)
                            attempted_calls[call_key] = attempted_calls.get(call_key, 0) + 1
                            state["failed"][call_key] = attempted_calls[call_key]
                            # config_error（MCP 未配置/地址错/认证）→ 记录，触发提前退出引导
                            if err_type == "config_error" and not state["config_error"]:
                                state["config_error"] = str(result.get("error") or result.get("message") or "")
                            # 反思引导：首次失败让 LLM 归因；重复失败提示停止空转
                            if attempted_calls[call_key] == 1:
                                result_str += (
                                    "\n\n（该调用失败。请先反思原因：是参数传错？数据源地址/凭据问题？"
                                    "还是数据源本身无此数据？据此修正参数、换工具或换数据源，勿盲目重试相同调用。）"
                                )
                            else:
                                result_str += (
                                    f"\n\n⚠️ 调用提示：`{func_name}` 已用相同参数执行 "
                                    f"{attempted_calls[call_key]} 次，均未成功。请停止重复尝试，"
                                    f"改用其他工具/参数/数据源，或向用户如实说明情况。"
                                )
                            last_error = result["error"]
                        elif result is not None:
                            # 成功（有结果且未报错）：标记本轮进展 + 清除历史错误
                            state["round_has_progress"] = True
                            state["done"].append(func_name)
                            last_error = None

                        # Append tool result to messages for LLM to continue reasoning
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result_str,
                        })
                except Exception as e:
                    logger.error("Tool result processing failed: %s", e, exc_info=True)
                    yield AgentResponse(type="thinking", content=f"工具结果处理出错: {str(e)}")
                    break

            except Exception as e:
                logger.error("Tool processing outer try failed: %s", e, exc_info=True)
                yield AgentResponse(type="thinking", content=f"工具处理出错: {str(e)}")
                continue

        # ── Phase 1: 优雅退出（替代"40步报错"）──
        # 区分两种退出：配置类错误（引导去设置页）vs 一般收敛/停滞（交付部分结果+建议方向）
        if state["config_error"]:
            logger.info("Graceful exit on config_error for message: %.60s", message)
            exit_content = (
                f"检测到环境配置问题，无法继续执行该请求：\n{state['config_error']}\n"
                f"建议在「设置 → MCP 服务」中配置或修正相关服务器后重试。"
            )
        else:
            logger.warning("Tool rounds/stall limit reached for message: %.60s", message)
            exit_content = self._build_graceful_exit(state, last_error)
        yield AgentResponse(type="text", content=exit_content)
        yield AgentResponse(type="complete")

        # Log conversation turn for graceful exit case
        if db and user_id:
            try:
                await self.memory.log_conversation_turn(
                    db=db,
                    user_id=user_id,
                    session_id=conversation_id or user_id or "default",
                    conversation_id=conversation_id,
                    user_message=message,
                    assistant_response=exit_content,
                    tool_calls=tracked_tool_calls,
                )
            except Exception:
                logger.debug("Conversation logging failed (non-critical)", exc_info=True)

            # Flush important context before returning — tracked entities,
            # tool calls, and decisions are about to be lost
            try:
                await self.memory.flush_important_context(
                    db=db,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    reason="max_rounds",
                    default_provider=llm_override.get("default_provider"),
                )
            except Exception:
                logger.debug("Memory flush after max rounds failed (non-critical)", exc_info=True)

    # ── Markdown table sanitization ──

    @staticmethod
    def _sanitize_markdown_tables(text: str) -> str:
        """Fix common LLM markdown table formatting errors.

        LLMs often generate malformed tables like:
          | A | B | |---|---| | 1 | 2 |
        This should be:
          | A | B |
          |---|---|
          | 1 | 2 |

        Fixes applied:
        1. Split concatenated table rows that are on the same line
        2. Ensure header and separator rows are on separate lines
        3. Ensure tables are separated by blank lines
        """
        import re

        if not text or "|" not in text:
            return text

        # Step 1: Split concatenated table rows on the same line.
        #
        # LLMs often output: | Issue | 标题 | 状态 | |-------|------|-----:| | TV-1 | ... | Open |
        # Which should be:   | Issue | 标题 | 状态 |
        #                    |-------|------|-----:|
        #                    | TV-1  | ...  | Open |
        #
        # Strategy: scan for sequences of `| ... |` that look like independent rows.
        # A row boundary is detected when we see `| |` (empty cell) or `||` where
        # the next segment starts a new row (separator dashes or new cell content).

        def split_concatenated_line(line: str) -> str:
            """Split a single line containing multiple concatenated table rows."""
            # Find all table-row-like segments in the line.
            # A table row is: | cell1 | cell2 | ... |
            # We scan character by character to find complete rows.
            rows = []
            i = 0
            n = len(line)

            while i < n:
                if line[i] != '|':
                    i += 1
                    continue

                # Try to find a complete row starting at position i
                row_start = i
                pipe_count = 0
                j = i
                while j < n:
                    if line[j] == '|':
                        pipe_count += 1
                    j += 1
                    # A complete row has at least 3 pipes (| cell | cell |)
                    # and ends with a pipe that is followed by space, another pipe,
                    # or end of string.
                    if pipe_count >= 3 and line[j - 1] == '|':
                        # Check if this is really the end of a row
                        # Lookahead: if next non-space char is '|' or end of string,
                        # this is a row boundary.
                        k = j
                        while k < n and line[k] == ' ':
                            k += 1
                        if k >= n or line[k] == '|':
                            rows.append(line[row_start:j])
                            i = j
                            break
                else:
                    # No complete row found starting at i
                    i += 1

            return "\n".join(rows) if rows else line

        lines = text.split("\n")
        processed_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                pipe_count = stripped.count("|")
                # Heuristic: a normal table row with <= 4 cells has <= 5 pipes.
                # If there are many more, it's likely multiple rows concatenated.
                if pipe_count > 6:
                    processed_lines.append(split_concatenated_line(stripped))
                else:
                    processed_lines.append(line)
            else:
                processed_lines.append(line)

        text = "\n".join(processed_lines)

        # Step 2: Ensure blank lines between consecutive tables
        lines = text.split("\n")
        result_lines = []
        prev_was_table_data = False
        for line in lines:
            stripped = line.strip()
            is_table_row = stripped.startswith("|") and stripped.endswith("|")
            is_separator = is_table_row and re.match(r'^\|[-\s:|]+\|$', stripped) is not None

            if prev_was_table_data and is_table_row and not is_separator:
                if not re.search(r'\b[A-Z]+-\d+\b', stripped):
                    result_lines.append("")

            result_lines.append(line)
            prev_was_table_data = is_table_row and not is_separator

        text = "\n".join(result_lines)

        # Step 3: Remove orphaned separator lines — separator rows that appear
        # without a proper header row above and data row below. These are
        # typically LLM artifacts that render as raw text like:
        #   | -------|------|:----:|--------|:------:|
        lines = text.split("\n")
        result_lines = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            is_table_line = stripped.startswith("|") and stripped.endswith("|")
            is_sep = is_table_line and re.match(r'^\|[\s\-:|]+\|$', stripped) is not None
            if is_sep:
                # Check context: separator is valid only when preceded by a header
                # row (starts with |, not just dashes/colons) and followed by data.
                prev_idx = i - 1
                while prev_idx >= 0 and not lines[prev_idx].strip():
                    prev_idx -= 1
                prev_line = lines[prev_idx].strip() if prev_idx >= 0 else ""
                next_idx = i + 1
                while next_idx < len(lines) and not lines[next_idx].strip():
                    next_idx += 1
                next_line = lines[next_idx].strip() if next_idx < len(lines) else ""

                has_valid_header = (
                    prev_line.startswith("|") and prev_line.endswith("|")
                    and not re.match(r'^\|[\s\-:|]+\|$', prev_line)
                )
                has_valid_data = (
                    next_line.startswith("|") and next_line.endswith("|")
                    and not re.match(r'^\|[\s\-:|]+\|$', next_line)
                )

                if not (has_valid_header and has_valid_data):
                    continue  # drop orphaned separator

            result_lines.append(line)

        return "\n".join(result_lines)

    # ── Markdown table formatters for structured tool results ──

    @staticmethod
    def _format_jira_table(issues: list) -> str:
        """Format Jira issue list as markdown table (matching AGENTS.md format).
        Columns: Issue, 摘要, 状态, 负责人 (≤ 4 per rule)
        """
        rows = [
            "| Issue | 摘要 | 状态 | 负责人 |",
            "|-------|------|:----:|:------:|",
        ]
        for issue in issues:
            key = issue.get("key", "N/A")
            summary = issue.get("summary") or (issue.get("fields", {}) or {}).get("summary", "")
            if isinstance(summary, str) and len(summary) > 30:
                summary = summary[:28] + "..."

            raw_status = issue.get("status") or (issue.get("fields", {}) or {}).get("status", "")
            if isinstance(raw_status, dict):
                status = raw_status.get("name", str(raw_status))
            else:
                status = str(raw_status) if raw_status else ""

            raw_assignee = issue.get("assignee") or (issue.get("fields", {}) or {}).get("assignee", "")
            if isinstance(raw_assignee, dict):
                assignee = raw_assignee.get("displayName", raw_assignee.get("name", "未分配"))
            else:
                assignee = str(raw_assignee) if raw_assignee else "未分配"
            rows.append(f"| {key} | {summary} | {status} | {assignee} |")
        return "\n".join(rows)

    @staticmethod
    def _format_confluence_table(pages: list) -> str:
        """Format Confluence page list as markdown table (matching AGENTS.md format).
        Columns: ID, 页面, 空间, 更新时间 (≤ 4 per rule)
        Note: ID is critical — LLM needs it to call get_page for full content.
        """
        rows = [
            "| ID | 页面 | 空间 | 更新时间 |",
            "|----|------|:----:|:--------:|",
        ]
        for page in pages:
            page_id = str(page.get("id", page.get("contentId", "")))
            title = page.get("title", "无标题")
            if isinstance(title, str) and len(title) > 30:
                title = title[:28] + "..."
            space = page.get("space", page.get("spaceKey", ""))
            if isinstance(space, dict):
                space = space.get("key", space.get("name", ""))
            updated = (
                page.get("updated")
                or page.get("lastModified")
                or (page.get("version", {}) or {}).get("when", "")
            )
            if isinstance(updated, dict):
                updated = ""
            rows.append(f"| {page_id} | {title} | {space} | {updated} |")
        return "\n".join(rows)

    @staticmethod
    def _format_gerrit_table(changes: list) -> str:
        """Format Gerrit CL list as markdown table (matching AGENTS.md format).
        Columns: Change-Id, 标题, 状态, 作者 (≤ 4 per rule)
        """
        rows = [
            "| Change-Id | 标题 | 状态 | 作者 |",
            "|-----------|------|:----:|:----:|",
        ]
        for cl in changes:
            cl_id = cl.get("_number", cl.get("changeId", cl.get("id", "N/A")))
            subject = cl.get("subject", cl.get("title", ""))
            if isinstance(subject, str) and len(subject) > 30:
                subject = subject[:28] + "..."
            status = cl.get("status", "")
            owner = cl.get("owner", cl.get("author", {}))
            if isinstance(owner, dict):
                owner = owner.get("name", owner.get("username", ""))
            rows.append(f"| {cl_id} | {subject} | {status} | {owner} |")
        return "\n".join(rows)

    # ── Jira error formatter ──

    @staticmethod
    def _format_jira_error(error_text: str) -> str:
        """Parse verbose JiraError into concise, actionable field-level guidance.

        Input format:
          错误: JiraError HTTP 400 ...
            text: Component/s is required., ...
            response text = {"errors":{"components":"...","customfield_10107":"Product is required.",...}}

        Output: structured markdown showing each failing field, its error,
        and the expected value format, so the LLM can fix everything in one retry.
        """
        import re
        import json

        lines = error_text.split("\n")

        # ── Parse "response text = {...}" JSON (most reliable source) ──
        response_json = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("response text ="):
                json_str = stripped[len("response text ="):].strip()
                try:
                    response_json = json.loads(json_str)
                except json.JSONDecodeError:
                    pass
                break

        errors_dict = {}
        if response_json and isinstance(response_json, dict):
            errs = response_json.get("errors") or {}
            if isinstance(errs, dict):
                errors_dict = errs

        if not errors_dict:
            return "Jira 错误: " + error_text[:500]

        # ── Classify errors ──
        required_fields = {}    # field → "is required"
        format_errors = {}      # field → "data was not an object/array" / "expected Object/Array"
        cannot_set_fields = {}  # field → "cannot be set"
        other_errors = {}       # field → other message

        for field, msg in errors_dict.items():
            msg_lower = msg.lower().strip(".")
            if "required" in msg_lower:
                required_fields[field] = msg
            elif "data was not an" in msg_lower or msg_lower.startswith("expected"):
                format_errors[field] = msg
            elif "cannot be set" in msg_lower:
                cannot_set_fields[field] = msg
            else:
                other_errors[field] = msg

        # ── Format hint lookup ──
        KNOWN_USER_FIELDS = {
            "customfield_10700", "customfield_11002", "customfield_10409",
            "customfield_11104", "customfield_12506", "customfield_12507",
        }
        KNOWN_LIST_FIELDS = {
            "customfield_10107", "customfield_10300", "customfield_10407",
            "customfield_10000",
        }

        def format_hint(field: str) -> str:
            if field in KNOWN_USER_FIELDS:
                return '{"name": "用户名"}'
            if field in KNOWN_LIST_FIELDS:
                return '[{"value": "选项值"}]'
            if field == "components":
                return '[{"name": "组件名"}]'
            return ''  # unknown — let LLM decide

        parts = []

        # ── Required fields block ──
        if required_fields:
            rows = []
            for field, msg in sorted(required_fields.items()):
                hint = format_hint(field)
                hint_str = f" → {hint}" if hint else ""
                rows.append(f"  - {field}: {msg}{hint_str}")
            parts.append("⚠️ 缺少必填字段。加上这些字段后重试，**不要删除其他已有字段**：\n" + "\n".join(rows))

        # ── Format errors block ──
        if format_errors:
            rows = []
            for field, msg in sorted(format_errors.items()):
                hint = format_hint(field)
                msg_lower = msg.lower()
                if "object" in msg_lower:
                    hint = hint or '{"name": "用户名"} (用户选择器)'
                elif "array" in msg_lower:
                    hint = hint or '[{"value": "选项值"}] (选择列表)'
                rows.append(f"  - {field}: {msg} → 正确格式: {hint}")
            parts.append("⚠️ 字段格式错误。**只修正格式**，保留其他字段不变后重试：\n" + "\n".join(rows))

        # ── Cannot-set fields (Jira screen restriction) ──
        if cannot_set_fields:
            rows = "\n".join(f"  - {k}: {v}" for k, v in sorted(cannot_set_fields.items()))
            parts.append(
                "⚠️ 以下字段在创建时**不可设置**。**只移除这些字段**，保留其他字段完全不变后重试：\n" + rows
            )

        # ── Other errors ──
        if other_errors:
            parts.append("其他错误：\n" + "\n".join(f"  - {k}: {v}" for k, v in sorted(other_errors.items())))

        return "\n\n".join(parts)

    # ── Observation summarizer for reasoning chain ──

    @staticmethod
    def _summarize_observation(result: Any, func_name: str) -> str:
        """Create a brief one-line observation from a tool result."""
        if result is None:
            return f"{func_name}: 无返回结果"

        if isinstance(result, dict):
            if result.get("error"):
                return f"{func_name}: 出错 — {result['error'][:100]}"
            # Jira issue list
            issues = result.get("issues")
            if isinstance(issues, list):
                return f"Jira 查询: 共 {len(issues)} 个 issue"
            # Confluence pages
            pages = result.get("pages") or result.get("results")
            if isinstance(pages, list):
                return f"Confluence 查询: 共 {len(pages)} 个页面"
            # Gerrit CLs
            cls = result.get("changes") or result.get("changeInfos")
            if isinstance(cls, list):
                return f"Gerrit 查询: 共 {len(cls)} 个 CL"
            # Skill execution
            if result.get("success") is True:
                msg = str(result.get("message", ""))
                return f"技能执行成功: {msg[:80]}" if msg else f"{func_name}: 执行成功"
            if result.get("saved") is True:
                return f"已保存记忆: {result.get('key', '')}"
            if result.get("entries") is not None:
                count = len(result["entries"])
                return f"记忆搜索: 找到 {count} 条结果"
            if result.get("success") is False:
                return f"{func_name}: 执行失败 — {str(result.get('message', ''))[:100]}"

        if isinstance(result, list):
            return f"{func_name}: 返回 {len(result)} 条结果"

        result_str = str(result)
        return f"{func_name}: {result_str[:100]}" if len(result_str) > 100 else f"{func_name}: {result_str}"

    # ── Result truncation with JSON structure preservation ──

    @staticmethod
    def _truncate_result(result: Any, max_len: int = 5000) -> str:
        """Truncate result while keeping JSON structure valid."""
        result_str = json.dumps(result, ensure_ascii=False, default=str)
        if len(result_str) <= max_len:
            return result_str

        # Strategy 1: for dict, trim long lists down and annotate total count
        if isinstance(result, dict):
            truncated = dict(result)
            for key, value in list(truncated.items()):
                if isinstance(value, list) and len(value) > 10:
                    truncated[key] = value[:10]
                    truncated[f"_{key}_total"] = len(value)
            truncated["_truncated"] = True
            result_str = json.dumps(truncated, ensure_ascii=False, default=str)
            if len(result_str) <= max_len:
                return result_str

        # Strategy 2: for long strings, truncate with overflow note
        if max_len > 200:
            preview = result_str[:max_len - 200]
            return json.dumps({
                "_note": "结果过长，已截断",
                "preview": preview,
                "original_length": len(result_str),
                "type": type(result).__name__,
            }, ensure_ascii=False)

        # Last resort: return a structured note with preview
        preview_len = max(0, min(max_len - 200, len(str(result))))
        return json.dumps({
            "_note": "原始结果过长，已截断",
            "preview": str(result)[:preview_len],
            "type": type(result).__name__,
        }, ensure_ascii=False)

    @staticmethod
    async def _call_with_retry(func, max_retries=2, base_delay=1.0):
        """Execute an async callable with exponential backoff retry for transient errors."""
        last_exception = None
        for attempt in range(max_retries):
            try:
                return await func()
            except (ConnectionError, asyncio.TimeoutError, TimeoutError) as e:
                last_exception = e
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning("Transient error (attempt %d/%d), retrying in %.1fs: %s",
                                  attempt + 1, max_retries, delay, e)
                    await asyncio.sleep(delay)
            except MCPException as e:
                # MCPClient.call_tool 会把 ReadTimeout 包装成 MCPException，
                # 超时类错误同样按瞬时错误重试（capability.call 已给出中文超时提示）
                if any(k in str(e) for k in ("Timeout", "ReadTimeout", "timed out", "超时")):
                    last_exception = e
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning("MCP timeout (attempt %d/%d), retrying in %.1fs: %s",
                                      attempt + 1, max_retries, delay, e)
                        await asyncio.sleep(delay)
                else:
                    raise
            except Exception:
                raise
        logger.warning("All %d retry attempts exhausted for MCP call", max_retries)
        raise last_exception

    # ── Tool definition builder ──

    async def _build_tool_definitions(self, context: Dict[str, Any]) -> List[Dict]:
        """Build function calling tool definitions from registered skills/tools.

        Returns a list of tool dicts compatible with OpenAI function calling format.
        Keeps the tool count manageable by defining meta-tools rather than one
        per skill/tool. The system prompt (AGENTS.md) describes specific options.
        MCP tool definitions are resolved per-user from MCPCapabilityService
        (hot-reload: reflects the latest saved config without restart).
        """
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "execute_skill",
                    "description": "执行一个已注册的技能(Skill)。技能是独立子进程脚本，用于批量操作如汇总Jira、生成周报、监控CL合并等。技能已有用户配置的默认参数，直接调用即可，无需向用户询问任何参数。技能名和可用列表在系统提示词的'运行时上下文'中。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill_name": {
                                "type": "string",
                                "description": "要执行的技能名称，例如 jira-confluence-report"
                            }
                        },
                        "required": ["skill_name"],
                        "additionalProperties": False
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "call_mcp_tool",
                    "description": "调用 MCP 服务器提供的工具，用于查询外部系统(Jira/Confluence/Gerrit)。例如查询Jira issue、搜索Confluence页面、查询Gerrit CL状态。格式: server/tool_name",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "server": {
                                "type": "string",
                                "description": "MCP 服务器名称，取决于您在设置中配置的服务器名"
                            },
                            "tool": {
                                "type": "string",
                                "description": "工具名称，如 search_issues, search_pages"
                            },
                            "params": {
                                "type": "object",
                                "description": "调用参数，根据具体工具要求提供"
                            }
                        },
                        "required": ["server", "tool"],
                        "additionalProperties": False
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "call_builtin_tool",
                    "description": "调用内置工具，包括文档处理工具、Gerrit操作工具、Confluence工具和暂存数据取回。文档: read_docx, read_pdf, read_xlsx, read_pptx, ocr_image, write_docx, write_xlsx, write_pptx, read_image_metadata, read_text_file, read_html, write_html, read_eml, write_eml, extract_pdf_metadata。文件回传: deliver_file(把服务器上已有的文件发给用户——用户说『把XXX文件发给我/我要下载XXX/把刚才生成的文件发我』时必须调用，文件会以下载卡片直接出现在聊天里，**禁止回答『无法发送附件』**；传file_path或file_name，不知路径时只给文件名即可)。Gerrit: gerrit_submit_change(提交合并), gerrit_review_change(评审打分), gerrit_get_change_detail(查询详情), gerrit_batch_get_changes(批量查询多个CL状态——当需要查大量CL时务必用此工具一次传入change_ids，勿逐个查)。Confluence: confluence_get_page_content(读取Confluence页面完整正文，需要page_id参数，可选confluence_base_url指定真实地址如https://confluence.example.com), confluence_ocr_page_image(对Confluence页面图片做OCR识别)。暂存数据: context_spill_read(有界读取先前因过大而暂存的原始结果——当某次查询提示『全文已暂存可查回』且需要未展示细节时调用，传spill_id+offset/limit+可选query)。定时任务: create_scheduled_task(创建无人值守定时任务——PM要求『每隔N重复做X直到某日/N天没更新自动提醒』时必须调用，声明式config=source+conditions+action), list_scheduled_tasks(查询已建定时任务)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tool_name": {
                                "type": "string",
                                "description": "内置工具名称，例如 read_pdf, read_docx, confluence_get_page_content"
                            },
                            "params": {
                                "type": "object",
                                "description": "工具参数，通常需要 file_path 或 page_id 字段"
                            }
                        },
                        "required": ["tool_name"],
                        "additionalProperties": False
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "memory_search",
                    "description": "搜索长期记忆，查找之前保存的重要信息（用户偏好、项目信息、历史决策等）。在回答需要历史上下文的问题时使用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "搜索关键词，描述要查找的信息"
                            }
                        },
                        "required": ["query"],
                        "additionalProperties": False
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "memory_save",
                    "description": "保存重要信息到长期记忆。用于记住用户偏好、项目决策、重要约定等跨会话需要保留的信息。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "key": {
                                "type": "string",
                                "description": "记忆的键名，用于检索"
                            },
                            "content": {
                                "type": "string",
                                "description": "要记住的内容"
                            },
                            "category": {
                                "type": "string",
                                "enum": ["user_preference", "project_info", "decision", "note"],
                                "description": "记忆分类"
                            }
                        },
                        "required": ["key", "content"],
                        "additionalProperties": False
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "ask_user",
                    "description": "暂停执行并向用户提问（Phase 2 用户协作工具）。适用于：①缺少完成任务必需的关键信息（如查哪个项目、目标Epic、具体范围）；②需要在多个方案/数据源/参数之间让用户选择；③执行陷入僵局、已尝试多种方式仍无进展时，让用户给出方向或补充信息。调用后本轮结束，用户答复后你会继续执行。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "向用户提出的具体问题"
                            },
                            "options": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "可选项（2-4个），用户可直接点选；留空则用户自由输入回答"
                            }
                        },
                        "required": ["question"],
                        "additionalProperties": False
                    }
                }
            },
        ]

        # Append individual MCP tool definitions for precise LLM function calling.
        # Instead of a single generic call_mcp_tool with guessed params, each MCP
        # tool gets its own function definition with the exact input schema from
        # the server, so the LLM knows exactly what parameters to provide.
        try:
            mcp_tools = await capability.get_tool_definitions(context.get("_user_id"))
            tools.extend(mcp_tools)
        except Exception:
            logger.warning("Failed to build MCP tool definitions")

        return tools

    # ── Heartbeat wrapper for long-running tool calls ──

    async def _execute_with_heartbeat(
        self,
        func_name: str,
        args: Dict[str, Any],
        context: Dict[str, Any],
        db: Optional[AsyncSession] = None,
        user_id: Optional[int] = None,
        default_provider: Optional[str] = None,
    ) -> AsyncGenerator[Any, None]:
        """Execute a function call, yielding periodic keepalive sentinels.

        Yields:
            HEARTBEAT_SENTINEL (str) every HEARTBEAT_INTERVAL seconds,
            then the actual result.
        """
        task = asyncio.create_task(
            self._execute_with_cache(func_name, args, context, db=db, user_id=user_id,
                                      default_provider=default_provider)
        )
        while True:
            done, _ = await asyncio.wait([task], timeout=self.HEARTBEAT_INTERVAL)
            if done:
                yield task.result()
                return
            yield "heartbeat"

    # ── Function dispatch ──

    async def _execute_function_call(
        self,
        func_name: str,
        args: Dict[str, Any],
        context: Dict[str, Any],
        db: Optional[AsyncSession] = None,
        user_id: Optional[int] = None,
        default_provider: Optional[str] = None,
    ) -> Any:
        """Dispatch a function call to the appropriate executor.

        Routes to:
          - execute_skill  → ActionExecutor._execute_skill
          - call_mcp_tool  → MCPCapabilityService.call (server/tool, 统一接口)
          - server__tool   → MCPCapabilityService.call (直接调用，统一接口)
          - call_builtin_tool → ActionExecutor._execute_tool (simple name, 内置工具)
          - memory_search  → search learning records (placeholder)
          - memory_save    → save to learning records (placeholder)
        """
        try:
            if func_name == "ask_user":
                # 正常情况下由循环的 ask_infos 拦截处理（yield ask_user + complete）。
                # 此分支为防御兜底：若 ask_user 意外进入正常执行路径，返回占位结果。
                return {"message": "已向用户提问，等待用户答复", "success": True}

            if func_name == "execute_skill":
                skill_name = args.get("skill_name", "")
                params = args.get("params", {})

                # Inject user_id so executor can load per-user skill config
                context["_user_id"] = user_id

                # Long-running skills → submit to background queue (Phase B)
                # Falls back to inline execution if queue unavailable.
                if skill_name in self.LONG_RUNNING_SKILLS and user_id is not None:
                    skill_def = skill_scanner.get_metadata().get(skill_name)
                    if skill_def and skill_def.mcp_required:
                        user_mcp = await capability.get_servers(user_id)
                        if not user_mcp:
                            servers = "、".join(skill_def.mcp_required)
                            return {
                                "type": "error",
                                "message": (
                                    f"技能「{skill_name}」需要 MCP 服务器配置（{servers}），"
                                    f"但您尚未配置任何 MCP 服务器。请先在设置页面的「MCP 服务」中配置后再执行此技能。"
                                    f"然后使用「技能配置」为此技能设置业务参数。"
                                ),
                            }

                    # Try background queue first
                    try:
                        from app.services.task_queue import get_worker
                        worker = await get_worker()
                        if worker.is_running:
                            handler = self.action_executor.skill_registry.get(skill_name)
                            if handler:
                                context["_user_id"] = user_id
                                task_id = await worker.submit(handler, params, user_id, skill_name)
                                return {
                                    "type": "task_submitted",
                                    "task_id": task_id,
                                    "message": f"技能「{skill_name}」已提交到后台执行。可通过 GET /api/tasks/{task_id} 查询状态。",
                                }
                    except Exception:
                        logger.debug("Background queue unavailable, falling back inline")

                # Inline execution (fallback or non-long-running skills)
                return await self.action_executor._execute_skill(skill_name, params, context)

            elif func_name == "call_mcp_tool":
                server = args.get("server", "")
                tool = args.get("tool", "")
                params = args.get("params", {})

                # ── Jira create_issue 安全保护 ──
                # 当 JIRA_CREATE_DISABLED=true 或 JIRA_CREATE_CONFIRM=true 时，
                # create_issue 会被拦截，防止误操作向真实 Jira 写入数据。
                if server == "jira" and tool == "create_issue" and params:
                    if os.environ.get("JIRA_CREATE_DISABLED") == "true":
                        return {
                            "error": "Jira Issue 创建已禁用(JIRA_CREATE_DISABLED=true)",
                            "_safety": True,
                        }
                    if os.environ.get("JIRA_CREATE_CONFIRM") == "true":
                        return {
                            "_requires_confirmation": True,
                            "message": f"需要在创建前确认。请向用户展示以下创建参数并等待确认。",
                            "params_preview": {
                                "project": params.get("project_key", ""),
                                "summary": params.get("summary", ""),
                                "issue_type": params.get("issue_type", ""),
                                "custom_fields_count": len(params.get("custom_fields", {})),
                            },
                        }

                # 统一走 MCPCapabilityService：ensure client + per-user auth + call_tool
                # （懒创建 client，两路径行为一致）
                return await self._call_with_retry(
                    lambda: capability.call(server, tool, params, user_id)
                )

            # Direct MCP tool call from individual tool definitions (server__tool_name format)
            # The LLM calls these directly with the exact parameter schema from the MCP server
            elif "__" in func_name:
                parts = func_name.split("__", 1)
                if len(parts) == 2:
                    server, tool = parts
                    # ── Jira create_issue 安全保护（与 call_mcp_tool 路径一致）──
                    if server == "jira" and tool == "create_issue" and args:
                        if os.environ.get("JIRA_CREATE_DISABLED") == "true":
                            return {
                                "error": "Jira Issue 创建已禁用(JIRA_CREATE_DISABLED=true)",
                                "_safety": True,
                            }
                        if os.environ.get("JIRA_CREATE_CONFIRM") == "true":
                            return {
                                "_requires_confirmation": True,
                                "message": "需要在创建前确认。请向用户展示以下创建参数并等待确认。",
                                "params_preview": {
                                    "project": args.get("project_key", ""),
                                    "summary": args.get("summary", ""),
                                    "issue_type": args.get("issue_type", ""),
                                    "custom_fields_count": len(args.get("custom_fields", {})),
                                },
                            }
                    # 统一走 MCPCapabilityService（懒创建 client，与 call_mcp_tool 一致）
                    return await self._call_with_retry(
                        lambda: capability.call(server, tool, args, user_id)
                    )
                # If routing fails, fall through to the "unknown" handler below
                logger.warning("Unrecognized MCP direct call format: %s", func_name)
                return {"error": f"未知的MCP工具: {func_name}"}

            elif func_name == "call_builtin_tool":
                tool_name = args.get("tool_name", "")
                params = args.get("params", {})
                return await self.action_executor._execute_tool(tool_name, params, context)

            elif func_name == "memory_search":
                query = args.get("query", "")
                return await self._search_memory(query, db=db, user_id=user_id, default_provider=default_provider)

            elif func_name == "memory_save":
                key = args.get("key", "")
                content = args.get("content", "")
                category = args.get("category", "note")
                return await self._save_memory(key, content, category, db=db, user_id=user_id, default_provider=default_provider)

            else:
                logger.warning("Unknown function call: %s", func_name)
                return {"error": f"未知的函数: {func_name}"}

        except Exception as e:
            logger.error("Function call '%s' failed: %s", func_name, e)
            # Return user-friendly message for MCP auth failures
            err_str = str(e)
            server_name = getattr(e, "server", None) or (func_name.split("__")[0] if "__" in func_name else None)
            if "Auth required" in err_str or ("auth" in err_str.lower() and server_name):
                return {"error": f"MCP服务器 {server_name} 未配置或认证失败，请先在设置页面配置后再使用"}
            return {"error": err_str}

    # ── Memory operations ──

    async def _search_memory(self, query: str, db: Optional[AsyncSession] = None, user_id: Optional[int] = None, default_provider: Optional[str] = None) -> Dict:
        """Hybrid search across long-term memory.

        Uses FTS5 BM25 full-text search + optional vector embedding search.
        Falls back gracefully if the search index is not yet synced or if
        embedding service is unavailable.
        """
        if not db or not user_id:
            return {
                "result": "memory_search",
                "query": query,
                "entries": [],
                "message": "数据库未连接，无法搜索记忆。",
            }

        try:
            # Ensure search index is synced from DB
            await search_engine.sync_from_db(db, user_id)

            # Run hybrid search
            result = await search_engine.search(query, mode="hybrid", limit=20, user_id=user_id, default_provider=default_provider)

            # Enrich results with DB-level metadata for completeness
            entries = result.get("entries", [])
            if entries:
                # Load full records from DB for details not stored in FTS5 index
                try:
                    records = await self.memory.long_term_memory.get_learning_records(
                        db, user_id, limit=50
                    )
                    record_map = {}
                    for r in records:
                        rid = getattr(r, "id", None)
                        if rid:
                            record_map[f"lr_{rid}"] = r

                    for entry in entries:
                        key = entry.get("key", "")
                        lr = record_map.get(key)
                        if lr:
                            entry["usage_count"] = getattr(lr, "usage_count", 0)
                            entry["success_rate"] = (
                                float(lr.success_rate) if hasattr(lr, "success_rate") and lr.success_rate else None
                            )
                except Exception:
                    pass

            return {
                "result": "memory_search",
                "query": query,
                "entries": entries,
                "total_matches": len(entries),
                "search_mode": result.get("search_mode", "hybrid"),
                "message": f"找到 {len(entries)} 条相关记忆" if entries else "未找到匹配的记忆。",
            }

        except Exception as e:
            logger.warning("Hybrid memory search failed, falling back to keyword: %s", e, exc_info=True)
            return await self._keyword_search_fallback(query, db, user_id)

    async def _keyword_search_fallback(self, query: str, db: AsyncSession, user_id: int) -> Dict:
        """Fallback: pure keyword search when hybrid search is unavailable."""
        try:
            results = []
            query_lower = query.lower()
            records = await self.memory.long_term_memory.get_learning_records(db, user_id, limit=50)
            for r in records:
                pattern = (r.get("pattern", "") if isinstance(r, dict) else getattr(r, "pattern", "")) or ""
                context = (r.get("context", "") if isinstance(r, dict) else getattr(r, "context", "")) or ""
                if query_lower in pattern.lower() or query_lower in context.lower():
                    results.append({
                        "type": "learning_record",
                        "pattern": pattern,
                        "context": context,
                        "usage_count": r.get("usage_count", 0) if isinstance(r, dict) else getattr(r, "usage_count", 0),
                        "relevance": "high" if query_lower in pattern.lower() else "medium",
                    })

            profile = await self.memory.long_term_memory.get_user_profile(db, user_id)
            if profile:
                prefs = profile.get("learned_preferences", {})
                for pref_key, pref_value in prefs.items():
                    if query_lower in pref_key.lower() or query_lower in str(pref_value).lower():
                        results.append({
                            "type": "user_preference",
                            "key": pref_key,
                            "value": pref_value,
                            "relevance": "high" if query_lower in pref_key.lower() else "medium",
                        })

            return {
                "result": "memory_search",
                "query": query,
                "entries": results[:20],
                "total_matches": len(results),
                "search_mode": "keyword_fallback",
                "message": f"找到 {len(results)} 条相关记忆" if results else "未找到匹配的记忆。",
            }
        except Exception as e:
            logger.warning("Keyword fallback search also failed: %s", e, exc_info=True)
            return {
                "result": "memory_search",
                "query": query,
                "entries": [],
                "message": f"记忆搜索出错: {str(e)}",
            }

    async def _save_memory(self, key: str, content: str, category: str, db: Optional[AsyncSession] = None, user_id: Optional[int] = None, default_provider: Optional[str] = None) -> Dict:
        """Save information to long-term memory via DB persistence.

        Also syncs the FTS5 search index to keep it up-to-date.
        """
        if not db or not user_id:
            return {
                "saved": False,
                "key": key,
                "category": category,
                "message": "数据库未连接，无法保存记忆。",
            }

        try:
            # 1. Save to UserProfile.learned_preferences as key-value
            await self.memory.long_term_memory.update_user_profile(
                db, user_id,
                {"learned_preferences": {key: {"content": content, "category": category}}},
            )

            # 2. Create a learning record for pattern-based retrieval
            record = await self.memory.long_term_memory.add_learning_record(
                db, user_id,
                pattern=f"[{category}] {key}: {content}",
                context=f"User saved memory via memory_save tool. Category: {category}",
            )

            # 3. Sync to FTS5 search index (async, non-blocking)
            try:
                record_id = getattr(record, "id", None) if record else None
                index_key_raw = f"lr_{record_id}" if record_id else f"pref_{key}"
                index_content = f"[{category}] {key}: {content}"
                # Prefixed key for per-user isolation in FTS5 index
                storage_key = f"u{user_id}_{index_key_raw}"

                # Add to FTS5 index (in executor to avoid blocking)
                await search_engine.add_entry(
                    key=storage_key,
                    content=index_content,
                    category=category,
                    entry_type="learning_record",
                    # user_id already encoded in key, no need to pass again
                )

                # Optionally compute embedding (best-effort, non-blocking)
                try:
                    await search_engine._compute_and_store_embedding(storage_key, index_content, default_provider=default_provider)
                except Exception:
                    pass
            except Exception as e:
                logger.debug("FTS5 index sync failed (non-critical): %s", e)

            return {
                "saved": True,
                "key": key,
                "category": category,
                "content_preview": content[:200],
                "message": f"已保存记忆: {key}",
            }

        except Exception as e:
            logger.warning("Memory save failed: %s", e, exc_info=True)
            return {
                "saved": False,
                "key": key,
                "category": category,
                "message": f"记忆保存出错: {str(e)}",
            }

    # ── Fallback: skill failure → direct MCP tool retry ──

    _MCP_KEYWORDS: Dict[str, List[str]] = {
        "jira": ["jira", "issue", "ticket", "bug", "story", "task", "缺陷", "问题"],
        "confluence": ["confluence", "wiki", "page", "文档", "空间", "知识库"],
    }
    # Gerrit-like servers are discovered dynamically by matching "gerrit" in the server name.
    # Users can add custom MCP servers with any name via the settings JSON editor.

    _MCP_JQL_TOOLS: Dict[str, str] = {
        "search_issues": "jira",
        "search_pages": "confluence",
        "query_changes": "gerrit",
    }

    async def _try_tool_fallback(
        self,
        message: str,
        context: Dict[str, Any],
        skill_name: str = "",
        user_id: Optional[int] = None,
    ) -> Optional[str]:
        """When a skill fails, try answering via direct MCP tool calls.

        Tries in order:
          1. Exact entity references (Jira ID, Confluence page ID, Gerrit change ID)
          2. Domain keywords in the message → matching MCP client
          3. Skill name hints → matching MCP client
        """
        import re

        try:
            servers = await capability.get_servers(user_id)
        except Exception:
            servers = {}
        if not servers:
            return None

        msg_lower = message.lower()

        # ── Helper: try calling a tool through MCPCapabilityService ──
        async def _try_call(client_name: str, tool: str, params: dict) -> Optional[str]:
            if client_name not in servers:
                return None
            try:
                result = await capability.call(client_name, tool, params, user_id)
                if result:
                    formatted = self._format_tool_result(result)
                    return f"⚠️ 技能执行失败，已通过 {client_name} 直接查询获取数据：\n\n{formatted}"
            except Exception:
                pass
            return None

        # 1. Extract entity references
        jira_ids = re.findall(r'[A-Z]+-\d+', message)

        # 2. Try Jira first (most common fallback)
        if jira_ids:
            result = await _try_call("jira", "search_issues", {"jql": f"key = {jira_ids[0]}"})
            if result:
                return result
            # Try JQL-free search tool name variant
            if "jira" in servers:
                result = await _try_call("jira", "get_issue", {"issue_key": jira_ids[0]})
                if result:
                    return result

        # 3. Detect domain from message keywords
        for client_name, keywords in self._MCP_KEYWORDS.items():
            if client_name in servers:
                if any(kw in msg_lower for kw in keywords):
                    # Try the most appropriate tool for this client
                    result = await _try_call(client_name, "search", {"query": message[:200]})
                    if result:
                        return result

        # 4. Try skill_name as client name hint
        if skill_name:
            for client_name in servers:
                if client_name in skill_name.lower():
                    result = await _try_call(client_name, "search", {"query": message[:200]})
                    if result:
                        return result

        return None

    def _format_tool_result(self, data: Any) -> str:
        """Format MCP tool result into markdown tables (AGENTS.md §6 format).

        Routes to table formatters for Jira issues, Confluence pages,
        Gerrit CLs, or falls back to generic display.
        """
        if not data:
            return "（空结果）"

        if isinstance(data, str):
            # Clean up verbose Jira MCP error strings — extract just the actionable part
            if data.startswith("错误: JiraError"):
                return self._format_jira_error(data)
            return data

        # ── Handle JSON array (MCP returning list directly) ──
        if isinstance(data, list) and data:
            first = data[0] if data else {}
            if isinstance(first, dict):
                if "key" in first:
                    return self._format_jira_table(data)
                if "changeId" in first or "_number" in first:
                    return self._format_gerrit_table(data)
                if "title" in first:
                    return self._format_confluence_table(data)
            return str(data)

        if not isinstance(data, dict):
            return str(data)

        # ── Jira issue list (from `search_issues` with dict wrapper) ──
        issues = data.get("issues")
        if isinstance(issues, list) and issues:
            return self._format_jira_table(issues)

        # ── Single Jira issue (from `get_issue` or direct key query) ──
        if data.get("key") and (data.get("summary") or data.get("fields")):
            key = data["key"]
            fields = data.get("fields", {}) or {}
            summary = data.get("summary") or fields.get("summary", "")

            raw_status = data.get("status") or fields.get("status", "")
            if isinstance(raw_status, dict):
                status = raw_status.get("name", str(raw_status))
            else:
                status = str(raw_status) if raw_status else ""

            raw_assignee = data.get("assignee") or fields.get("assignee", "")
            if isinstance(raw_assignee, dict):
                assignee = raw_assignee.get("displayName", raw_assignee.get("name", "未分配"))
            else:
                assignee = str(raw_assignee) if raw_assignee else "未分配"

            lines = [
                f"| 字段 | 值 |",
                f"|------|-----|",
                f"| Issue | {key} |",
                f"| 摘要 | {summary} |",
                f"| 状态 | {status} |",
                f"| 负责人 | {assignee} |",
            ]

            # Include issue type if available
            raw_type = fields.get("issuetype", "")
            if isinstance(raw_type, dict):
                lines.append(f"| 类型 | {raw_type.get('name', '')} |")
            elif raw_type:
                lines.append(f"| 类型 | {raw_type} |")

            # Include project key if available
            pkey = ""
            raw_project = fields.get("project", "")
            if isinstance(raw_project, dict):
                pkey = raw_project.get("key", raw_project.get("name", ""))
            elif isinstance(raw_project, str) and raw_project:
                pkey = raw_project
            if pkey:
                lines.append(f"| 项目 | {pkey} |")
            # Fallback: extract project key from issue key (e.g., "TV-12345" → "TV")
            if not pkey and "-" in key:
                pkey = key.split("-")[0]

            # ── Append non-empty custom fields so LLM can see Epic Link, etc. ──
            # Previously custom fields were silently dropped, which made it
            # impossible for the LLM to know the correct customfield_XXXXX ID
            # when creating sub-tasks under an epic.
            #
            # Format conversion: get_issue returns display format, but
            # create_issue needs API input format. Convert known field types
            # so the LLM sees the exact value it needs for create_issue.
            # Fields that cannot be set at creation time are flagged as warnings.
            from app.core.config import settings
            KNOWN_USER_FIELDS = set(settings.JIRA_CUSTOM_FIELD_USER.split(","))
            KNOWN_LIST_FIELDS = set(settings.JIRA_CUSTOM_FIELD_LIST.split(","))
            CANNOT_SET_FIELDS = set(settings.JIRA_CUSTOM_FIELD_CANNOT_SET.split(","))

            custom_rows = []
            for k, v in sorted(fields.items()):
                if not k.startswith("customfield_"):
                    continue
                if v is None or v == "" or v == [] or v == {}:
                    continue
                # Skip serialized Java toString garbage
                if isinstance(v, str) and (v.startswith("{summaryBean=") or v.startswith("com.atlassian")):
                    continue

                # Mark "cannot set at creation" fields with a warning (don't convert to API format)
                if k in CANNOT_SET_FIELDS:
                    try:
                        val_str = json.dumps(v, ensure_ascii=False)
                    except (TypeError, ValueError):
                        val_str = str(v)
                    if len(val_str) > 80:
                        val_str = val_str[:60] + "..."
                    custom_rows.append(f"  {k}: {val_str}  ⚠️创建时不可设置，请勿在create_issue中包含此字段")
                    continue

                # Convert display format → API format for known field types
                converted = False
                if k in KNOWN_USER_FIELDS and isinstance(v, str):
                    # User picker: GET returns "username", POST needs {"name": "username"}
                    v = {"name": v}
                    converted = True
                elif k in KNOWN_LIST_FIELDS and isinstance(v, list) and v and isinstance(v[0], str):
                    # List field: GET returns ["Value"], POST needs [{"value": "Value"}]
                    v = [{"value": item} for item in v]
                    converted = True
                elif k == "components" and isinstance(v, list) and v and isinstance(v[0], str):
                    # Components: GET returns ["Name"], POST needs [{"name": "Name"}]
                    v = [{"name": item} for item in v]
                    converted = True

                try:
                    val_str = json.dumps(v, ensure_ascii=False)
                except (TypeError, ValueError):
                    val_str = str(v)
                if len(val_str) > 100:
                    val_str = val_str[:80] + "..."
                if converted:
                    custom_rows.append(f"  {k} (API格式): {val_str}")
                else:
                    custom_rows.append(f"  {k}: {val_str}")

            if custom_rows:
                lines.append("")
                lines.append("自定义字段:")
                lines.extend(custom_rows[:20])
                if len(custom_rows) > 20:
                    lines.append(f"  ...（共 {len(custom_rows)} 个自定义字段）")

            # ── Sub-task creation guide for parent issues ──
            # When the LLM sees an Epic/Story, it often needs to create sub-tasks.
            # Pre-populate the mandatory field values and flag "cannot-set" fields
            # so the LLM can create issues correctly on the first try instead of
            # burning rounds on trial-and-error with Jira's validation errors.
            raw_type = fields.get("issuetype", "")
            issue_type = ""
            if isinstance(raw_type, dict):
                issue_type = raw_type.get("name", "")
            elif isinstance(raw_type, str):
                issue_type = raw_type

            if issue_type in ("Epic", "Story") and pkey:
                guide = ["", "---", "📋 创建子任务(Task)指南："]

                # Collect mandatory field values from this parent issue
                mandatory_vals = {}
                for k in ("customfield_10107", "customfield_10300", "customfield_10407"):
                    v = fields.get(k)
                    if isinstance(v, list) and v and isinstance(v[0], str):
                        mandatory_vals[k] = f'[{{"value": "{v[0]}"}}]'
                for k in ("customfield_10700", "customfield_11002", "customfield_10409"):
                    v = fields.get(k)
                    if isinstance(v, str) and v:
                        mandatory_vals[k] = f'{{"name": "{v}"}}'
                comps = fields.get("components", [])
                if isinstance(comps, list) and comps:
                    comp_name = comps[0] if isinstance(comps[0], str) else (comps[0].get("name", "") if isinstance(comps[0], dict) else "")
                    if comp_name:
                        mandatory_vals["components"] = f'[{{"name": "{comp_name}"}}]'

                if mandatory_vals:
                    guide.append("  ⚠️ 请严格按照以下字段创建，**不要添加 guide 未列出的字段**：")
                    for k, val in sorted(mandatory_vals.items()):
                        guide.append(f"    {k}: {val}")
                    guide.append("    description: \"任务描述文本\"")
                    # Epic Link: customfield_10102 links the Task under this Epic
                    guide.append(f"    customfield_10102: \"{key}\"  ← 将Task挂到此Epic下")

                cannot_set = [k for k in ("customfield_10000", "customfield_12506", "customfield_12507") if k in fields and fields[k] is not None]
                if cannot_set:
                    guide.append(f"  ❌ 禁止包含（会导致错误）：{'、'.join(cannot_set)}")

                lines.extend(guide)

            return "\n".join(lines)

        # ── Confluence pages list ──
        pages = data.get("pages") or data.get("results")
        if isinstance(pages, list) and pages:
            return self._format_confluence_table(pages)

        # ── Single Confluence page — show full details + body content ──
        if data.get("title") and data.get("space"):
            title = data["title"]
            space = data.get("space", "")
            if isinstance(space, dict):
                space = space.get("key", space.get("name", ""))
            page_id = str(data.get("id", data.get("contentId", "")))
            version = data.get("version", {})
            if isinstance(version, dict):
                version_str = str(version.get("number", ""))
            else:
                version_str = str(version)

            lines = [
                f"| 字段 | 值 |",
                f"|------|-----|",
                f"| ID | {page_id} |",
                f"| 页面 | {title} |",
                f"| 空间 | {space} |",
            ]
            if version_str:
                lines.append(f"| 版本 | {version_str} |")

            # Include excerpt if available (from search results)
            excerpt = data.get("excerpt", "")
            if excerpt and isinstance(excerpt, str):
                lines.append("")
                lines.append(f"**摘要**: {excerpt[:1000]}")

            # Extract and display page body content
            body = data.get("body", {}) or {}
            if isinstance(body, dict):
                storage = body.get("storage", {}) or {}
                content = (storage.get("value", "")
                           or body.get("view", {}).get("value", "")
                           or body.get("editor", {}).get("value", "")
                           or body.get("atlas_doc_format", {}).get("value", ""))
                if content:
                    # 先表格转 markdown 保留行列结构，再剥 HTML 标签
                    # （否则表格单元格文本粘连成串，LLM 无法定位列/行）
                    text_content = _html_table_to_markdown(content)
                    text_content = re.sub(r'<[^>]+>', '', text_content)
                    # Collapse multiple blank lines
                    text_content = re.sub(r'\n{3,}', '\n\n', text_content)
                    text_content = text_content.strip()
                    if len(text_content) > 15000:
                        text_content = text_content[:15000] + "\n\n...（内容过长已截断）"
                    if text_content:
                        lines.append("")
                        lines.append("### 页面正文")
                        lines.append(text_content)

            return "\n".join(lines)

        # ── confluence_get_page_content tool result ──
        if isinstance(data, dict) and data.get("type") == "document" and data.get("success"):
            title = data.get("title", "")
            content = data.get("content", "")
            meta = data.get("metadata", {}) or {}
            lines = [
                f"| 字段 | 值 |",
                f"|------|-----|",
                f"| 页面 | {title} |",
            ]
            for mk in ("id", "space_key", "space_name", "version", "updated"):
                mv = meta.get(mk, "")
                if mv:
                    label = {"id": "ID", "space_key": "空间Key", "space_name": "空间名",
                             "version": "版本", "updated": "更新时间"}.get(mk, mk)
                    lines.append(f"| {label} | {mv} |")
            if content:
                lines.append("")
                lines.append("### 页面正文")
                lines.append(content)
            return "\n".join(lines)

        # ── Gerrit CL list ──
        cls = data.get("changes") or data.get("changeInfos")
        if isinstance(cls, list) and cls:
            return self._format_gerrit_table(cls)

        # ── Jira issue comments (from get_issue_comments) ──
        # 必须先于泛型分支 —— 泛型分支只展平标量值，comments 数组会被静默丢弃
        comments = data.get("comments")
        if isinstance(comments, list) and comments:
            return self._format_issue_comments(data)

        # ── Confluence 表格工具结果 ──
        # 必须先于泛型分支 —— matches/cells/tables 数组是核心数据，泛型分支会丢弃。
        # 这正是此前"find_rows_in_table 返回 42 条匹配但 LLM 一条都看不到"死循环的根因。
        if isinstance(data.get("matches"), list) and data.get("matches"):
            return self._format_confluence_row_matches(data)
        if isinstance(data.get("cells"), list) and data.get("cells"):
            return self._format_confluence_cells(data)
        if isinstance(data.get("tables"), list) and data.get("tables"):
            return self._format_confluence_table_list(data)

        # ── Generic: flatten top-level key-value pairs ──
        generic = []
        for k, v in data.items():
            if isinstance(v, (str, int, float, bool)) and k not in ("_truncated", "_note", "original_length"):
                generic.append(f"  - {k}: {v}")
        if generic:
            return "\n".join(generic)

        # Last resort
        return str(data)[:2000]

    # ── Jira issue comments 展示预算（层 A，2026-09-08）──
    # MCP get_issue_comments 不分页、一次全量返回；展示受字符预算约束（非条数）。
    # 溢出部分由 async 版 spill 暂存（可 context_spill_read 精确查回）。
    COMMENT_RESULT_BUDGET = 20000  # 单次评论结果展示预算（字符，含每条 overhead 估算）
    RESULT_GATE_BUDGET = 20000  # 单次 MCP/内置工具结果进入 messages 的统一展示预算（层 A）
    # 层 B/C：上下文溢出压缩恢复（2026-09-08）——Agent 对模型 context 窗口无感知，
    # 撑爆报错那一刻 reactive 触发：压缩早期轮次 → 摘要进 memory + 原文 spill → 重试。
    COMPACT_MAX_PASSES = 2  # 最大压缩重试次数（防死循环）
    COMPACT_KEEP_RECENT = (2, 0)  # 两次 pass 分别保留的最近完整轮数（逐步加大压缩范围）
    COMPACT_SUMMARY_MAX_CHARS = 1200  # 单次压缩摘要长度上限

    @staticmethod
    def _render_comment_item(index: int, comment: Dict[str, Any], body: str) -> str:
        """渲染单条评论（作者/时间/正文）。供同步与 async 评论格式化共用。"""
        # 作者：dict（displayName/name）或 str，缺失用"未知"
        author_raw = comment.get("author", "")
        if isinstance(author_raw, dict):
            author = author_raw.get("displayName") or author_raw.get("name") or "未知"
        elif author_raw:
            author = str(author_raw)
        else:
            author = "未知"

        # 时间：created / updated / createdAt 原样输出（Jira ISO 时间）
        created = comment.get("created") or comment.get("updated") or comment.get("createdAt") or ""
        ts = "（%s）" % created if created else ""

        return "%d. **%s**%s:\n%s" % (index, author, ts, body)

    @staticmethod
    def _comment_body(comment: Dict[str, Any], per_item_max: int = 2000) -> str:
        """提取并规范化评论正文（压缩连续空行，单条截断）。"""
        body = str(comment.get("body", "") or "").strip()
        # 压缩连续 ≥3 空行为 ≤2（保留 markdown/代码块换行语义）
        import re
        body = re.sub(r"\n{3,}", "\n\n", body)
        if len(body) > per_item_max:
            body = body[:per_item_max] + "\n…（内容过长已截断）"
        return body

    def _select_comments_for_display(self, data: Dict[str, Any]) -> Tuple[List[Tuple[Dict[str, Any], str]], List[Dict[str, Any]], int]:
        """按预算挑选评论展示集（最新优先）。返回 (shown, omitted, spent)。

        shown: [(原评论, 展示用正文)]，最新在前；omitted: 溢出（更旧）评论。
        纯选择逻辑，不含渲染/spill，便于同步与 async 复用。
        """
        comments = data.get("comments", []) or []
        return fit_items_to_budget(
            comments,
            budget_chars=self.COMMENT_RESULT_BUDGET,
            sort_key=lambda c: str(c.get("created") or c.get("updated") or c.get("createdAt") or ""),
            get_body=lambda c: self._comment_body(c),
            newest_first=True,
            per_item_max=2000,
            overhead_per_item=60,
        )

    def _format_issue_comments(self, data: Dict[str, Any]) -> str:
        """Format get_issue_comments result into a readable comment list for the LLM.

        MCP 返回结构: {issue_key, total_comments, comments: [{id, author, body, created, updated}]}.
        泛型分支只展平标量值会丢掉 comments 数组，此分支逐条渲染作者/时间/正文。
        正文可能含 markdown/代码块（保留换行，压缩连续空行），LLM 可据此分析问题原因与进展。

        展示受字符预算约束（COMMENT_RESULT_BUDGET），**最新优先**；预算内尽量多放
        短评论、长评论自动少装。溢出（更旧）评论仅在此处给出尾部提示；如需将溢出
        内容 spill 暂存供精确查回，走 async `_format_issue_comments_for_llm`。
        """
        comments = data.get("comments", []) or []
        total = data.get("total_comments") or len(comments)
        issue_key = data.get("issue_key", "")

        header = "## 评论（共 %s 条）" % total
        if issue_key:
            header = "## %s 评论（共 %s 条）" % (issue_key, total)

        shown, omitted, _ = self._select_comments_for_display(data)
        lines = [header]
        for i, (comment, body) in enumerate(shown, 1):
            lines.append(self._render_comment_item(i, comment, body))

        if omitted:
            newest_shown = shown[0][0] if shown else None
            shown_ts = ""
            if newest_shown:
                shown_ts = "至 %s" % (newest_shown.get("created") or newest_shown.get("updated") or "")
            lines.append(
                "\n…（共 %s 条，已展示最新 %d 条%s；更早 %d 条因预算未列出，如需可让 Agent "
                "暂存后用 context_spill_read 精确查回）" % (total, len(shown), shown_ts, len(omitted))
            )

        return "\n\n".join(lines)

    async def _format_issue_comments_for_llm(
        self,
        data: Dict[str, Any],
        user_id: Optional[int] = None,
        source_tool: str = "",
        source_args: Optional[Dict[str, Any]] = None,
    ) -> str:
        """预算化展示评论（最新优先），溢出部分 spill 暂存（主循环专用，async）。

        与 `_format_issue_comments` 的区别：本方法在溢出时把**完整原始评论**存入
        context_spill 暂存区，并在结果尾部附 spill_id，LLM 需要更早评论时可调
        context_spill_read 精确查回——解决"MCP 不分页、只展示前 30 条"的信息缺失。

        Args:
            data: get_issue_comments 原始结果 dict。
            user_id: 当前用户（spill 目录隔离；None 时不 spill，降级为同步展示）。
            source_tool: 来源工具（如 "call_mcp_tool/jira/get_issue_comments"）。
            source_args: 来源调用参数（spill 追溯）。
        """
        comments = data.get("comments", []) or []
        total = data.get("total_comments") or len(comments)
        issue_key = data.get("issue_key", "")

        header = "## 评论（共 %s 条）" % total
        if issue_key:
            header = "## %s 评论（共 %s 条）" % (issue_key, total)

        shown, omitted, _ = self._select_comments_for_display(data)
        lines = [header]
        for i, (comment, body) in enumerate(shown, 1):
            lines.append(self._render_comment_item(i, comment, body))

        spill_id = None
        if omitted and user_id:
            try:
                # 完整原始评论进 spill（不截断），供 context_spill_read 精确查回
                from app.services.context_spill import save_spill
                spill_id = await save_spill(
                    user_id=user_id,
                    source_tool=source_tool or "get_issue_comments",
                    source_args=source_args or {},
                    data={
                        "issue_key": issue_key,
                        "total_comments": total,
                        "comments": comments,  # 全量原文
                    },
                    kind="comments",
                    summary=f"{issue_key or 'issue'} 的 {total} 条评论（全文暂存）" if issue_key else None,
                )
            except Exception as e:  # noqa: BLE001 — spill 失败不阻塞展示，仅日志
                logger.warning("comment spill failed (issue=%s): %s", issue_key, e)

        if omitted:
            newest_shown = shown[0][0] if shown else None
            shown_ts = ""
            if newest_shown:
                shown_ts = "至 %s" % (newest_shown.get("created") or newest_shown.get("updated") or "")
            if spill_id:
                lines.append(
                    "\n…（共 %s 条，已展示最新 %d 条%s；更早 %d 条已全文暂存 spill_id=`%s`。"
                    "如需分析更早评论，调 context_spill_read 传 spill_id 和 offset/limit/query 精确取回，勿整读）"
                    % (total, len(shown), shown_ts, len(omitted), spill_id)
                )
            else:
                lines.append(
                    "\n…（共 %s 条，已展示最新 %d 条%s；更早 %d 条因预算未列出）"
                    % (total, len(shown), shown_ts, len(omitted))
                )

        return "\n\n".join(lines)

    async def _gate_oversized_result(
        self,
        formatted: str,
        result: Any,
        user_id: Optional[int] = None,
        source_tool: str = "",
        source_args: Optional[Dict[str, Any]] = None,
    ) -> str:
        """统一结果门（层 A）：单次 MCP/内置工具结果超过展示预算时 spill 原文并截断。

        原 `_truncate_result(max_len=5000)` 会把超长结果的**中段静默丢弃**，LLM 既看不到
        完整数据、也不知道有数据被丢弃。本门改为：
          1. 把**完整原始 result** 存入 context_spill 暂存区（不截断，供精确查回）；
          2. 展示串用 `_truncate_long_result` 保留头尾、丢弃中段；
          3. 结果尾部附 spill_id + 可查回提示，消除"静默丢数据"。

        Args:
            formatted: 已格式化的展示字符串（超预算）。
            result: 原始工具结果（完整 spill，结构化保留）。
            user_id: 当前用户（spill 目录隔离；None 时不 spill，仅头尾截断）。
            source_tool: 来源工具名（spill 追溯）。
            source_args: 来源调用参数（spill 追溯）。
        """
        spill_id = None
        if user_id:
            try:
                from app.services.context_spill import save_spill
                spill_id = await save_spill(
                    user_id=user_id,
                    source_tool=source_tool or "tool_result",
                    source_args=source_args or {},
                    data=result,  # 完整原始结构
                    kind="generic",
                )
            except Exception as e:  # noqa: BLE001 — spill 失败不阻塞展示
                logger.warning("oversized result spill failed (%s): %s", source_tool, e)

        truncated = self._truncate_long_result(formatted, self.RESULT_GATE_BUDGET)
        if spill_id:
            truncated += (
                f"\n\n（⚠️ 结果过大（{len(formatted)} 字符），已截断展示；完整数据已暂存 "
                f"spill_id=`{spill_id}`，需要细节可调 context_spill_read 精确取回，勿整读）"
            )
        else:
            truncated += f"\n\n（⚠️ 结果过大（{len(formatted)} 字符），已截断展示）"
        return truncated

    # ── Confluence 表格工具结果格式化 ──
    # MCP 的 find_rows_in_table / read_table_structure / list_page_tables 返回的
    # 核心数据都是数组（matches/cells/tables/schema）。这些字段不能被泛型分支
    # 丢弃，必须渲染成 markdown 表格，否则 LLM 只看到 found_count 而看不到任何
    # 一行数据，陷入反复重试的死循环。

    @staticmethod
    def _cell_str(value: Any, max_len: int = 150) -> str:
        """将表格单元格值转为单行短文本：换行压缩、管道符转义、超长截断。"""
        if value is None:
            return ""
        text = str(value).replace("|", "\\|")
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > max_len:
            text = text[:max_len] + "…"
        return text

    @staticmethod
    def _format_confluence_row_matches(data: Dict[str, Any]) -> str:
        """Format `find_rows_in_table` result (row_data dicts) as markdown table.

        结构: {found_count, schema: [列名], matches: [{row_index, row_data: {列名: 值}}]}
        """
        schema = data.get("schema") or []
        matches = data.get("matches") or []
        if not schema and matches:
            first = matches[0].get("row_data", {})
            if isinstance(first, dict):
                schema = list(first.keys())
        if not schema:
            return "（表格结果缺少 schema，无法格式化）"

        found = data.get("found_count", len(matches))
        lines = [f"匹配 {found} 行:"]
        lines.append("| " + " | ".join(AgentEngine._cell_str(c) for c in schema) + " |")
        lines.append("|" + "|".join(["---"] * len(schema)) + "|")
        for m in matches:
            row_data = m.get("row_data", {})
            if not isinstance(row_data, dict):
                continue
            values = [AgentEngine._cell_str(row_data.get(c, "")) for c in schema]
            lines.append("| " + " | ".join(values) + " |")
        return AgentEngine._truncate_long_result("\n".join(lines), max_len=20000)

    @staticmethod
    def _format_confluence_cells(data: Dict[str, Any]) -> str:
        """Format `read_table_structure` result (cells with row/col) as markdown table.

        结构: {cells: [{row, col, value, is_header}], schema, row_count, col_count}
        """
        cells = data.get("cells") or []
        if not cells:
            return "（表格无数据）"
        schema = data.get("schema") or []
        grid: Dict[tuple, str] = {}
        max_row = max_col = 0
        for c in cells:
            r, col = int(c.get("row", 0)), int(c.get("col", 0))
            grid[(r, col)] = c.get("value", "")
            max_row = max(max_row, r)
            max_col = max(max_col, col)
        n_cols = max_col + 1
        if not schema:
            schema = [grid.get((0, col), f"列{col}") for col in range(n_cols)]

        lines = [f"表格（{max_row + 1} 行 × {n_cols} 列）:"]
        lines.append("| " + " | ".join(AgentEngine._cell_str(c) for c in schema) + " |")
        lines.append("|" + "|".join(["---"] * n_cols) + "|")
        for r in range(1, max_row + 1):
            values = [AgentEngine._cell_str(grid.get((r, col), "")) for col in range(n_cols)]
            lines.append("| " + " | ".join(values) + " |")
        return AgentEngine._truncate_long_result("\n".join(lines), max_len=20000)

    @staticmethod
    def _format_confluence_table_list(data: Dict[str, Any]) -> str:
        """Format `list_page_tables` result as a concise table overview."""
        tables = data.get("tables") or []
        if not tables:
            return "（页面中未找到表格）"
        lines = [f"页面共 {data.get('table_count', len(tables))} 张表格:"]
        lines.append("| 索引 | 行数 | 列数 | 表头预览 |")
        lines.append("|:----:|:----:|:----:|----------|")
        for t in tables:
            preview = " / ".join(
                AgentEngine._cell_str(c, 40) for c in (t.get("header_preview") or [])[:5]
            )
            lines.append(
                f"| {t.get('index', '?')} | {t.get('row_count', '?')} | "
                f"{t.get('col_count', '?')} | {preview} |"
            )
        return "\n".join(lines)

    @staticmethod
    def _truncate_long_result(text: str, max_len: int) -> str:
        """超出上限时保留头尾的截断，避免 LLM 上下文爆炸。"""
        if len(text) <= max_len:
            return text
        head = text[: int(max_len * 0.7)]
        tail = text[-int(max_len * 0.25):]
        return f"{head}\n\n…（结果过长，中间已截断 {len(text) - max_len} 字符）…\n\n{tail}"

    # ── Phase 1: 认知脚手架（元认知 / 失败分类 / 优雅退出）──

    @staticmethod
    def _is_context_overflow(e: Exception) -> bool:
        """判断异常是否为"上下文超出模型窗口"（层 B/C，2026-09-08）。

        优先认 client.py 打的 details["overflow"] 标；兜底用特征串匹配（防 LLMException
        被其它层重包装丢失 details）。触发上下文压缩恢复流程。
        """
        details = getattr(e, "details", None)
        if isinstance(details, dict) and details.get("overflow"):
            return True
        text = f"{type(e).__name__} {e}".lower()
        return any(k in text for k in (
            "context_length_exceeded", "maximum context length",
            "reduce the length of the messages", "prompt is too long",
            "message too long", "too many tokens",
        ))

    async def _compact_messages_for_overflow(
        self,
        messages: List[Dict[str, Any]],
        keep_recent: int = 2,
        user_id: Optional[int] = None,
        db: Optional[AsyncSession] = None,
        llm_override: Optional[Dict[str, Any]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """上下文溢出压缩（层 B/C）：把早期轮次压成摘要，保骨架 + 最近轮。

        消息结构约束（OpenAI 协议）：tool 消息必须紧跟带匹配 tool_call_id 的
        assistant(tool_calls) 消息。压缩按"轮"成对提取，绝不拆散单条 tool 消息。

        策略：
          - 保骨架：system(0) + 原始用户请求（首个 user 消息）。
          - 保最近：尾部 keep_recent 轮 [assistant(tool_calls)+tool...] 完整保留
            （内部强制至少保留当前正在执行的最后 1 轮，否则 tool 结果成孤儿）。
          - 中间：工具轮组打包 spill（一次 save_spill）→ LLM 压成结构化摘要
            （失败降级为机械摘要）→ 摘要 memory_save → 整段替换为 1 条 assistant
            文本消息（无 tool_calls，消息序列仍合法）。

        Args:
            messages: 当前 LLM 循环的完整 messages。
            keep_recent: 尾部保留的完整轮数（0 也会被强制提升到 1）。
            user_id: spill 目录隔离 + memory 按用户隔离。
            db: memory_save 的 session。
            llm_override: 用户 LLM 配置（压缩调用用同一 provider）。

        Returns:
            压缩后的新 messages；无可压缩内容时返回 None。
        """
        if len(messages) < 4:
            return None

        # ── 1. 骨架边界：system + 首个 user 消息 ──
        skeleton_end = 1
        for idx in range(1, len(messages)):
            if messages[idx].get("role") == "user":
                skeleton_end = idx + 1
                break

        # ── 2. 尾部保留边界：从后往前数 keep_recent 个工具轮 ──
        keep_recent = max(int(keep_recent or 0), 1)  # 至少保留当前执行中的最后 1 轮
        keep_start = len(messages)
        rounds_seen = 0
        # 注意下界 skeleton_end-1：一轮紧贴骨架时其 assistant 恰在 skeleton_end 处
        for idx in range(len(messages) - 1, skeleton_end - 1, -1):
            m = messages[idx]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                rounds_seen += 1
                if rounds_seen >= keep_recent:
                    keep_start = idx
                    break

        middle = messages[skeleton_end:keep_start]
        if not middle:
            return None

        # ── 3. 中间段分组：工具轮组 + 纯文本消息（history 对话等）──
        tool_groups: List[Dict[str, Any]] = []
        text_msgs: List[Dict[str, Any]] = []
        i = 0
        while i < len(middle):
            m = middle[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                names = [
                    (tc.get("function") or {}).get("name", "?")
                    for tc in (m.get("tool_calls") or [])
                ]
                results: List[str] = []
                j = i + 1
                while j < len(middle) and middle[j].get("role") == "tool":
                    results.append(str(middle[j].get("content", ""))[:400])
                    j += 1
                tool_groups.append({"tools": names, "results": results})
                i = j
            else:
                text_msgs.append(m)
                i += 1

        if not tool_groups and not text_msgs:
            return None

        # ── 4. 原始数据打包 spill（一次，不逐轮）──
        spill_id = None
        if user_id:
            try:
                from app.services.context_spill import save_spill
                spill_payload = {
                    "rounds": tool_groups,
                    "dialogue": [
                        {"role": m.get("role"), "content": str(m.get("content", ""))[:2000]}
                        for m in text_msgs
                    ],
                }
                spill_id = await save_spill(
                    user_id=user_id,
                    source_tool="context_compact",
                    source_args={"keep_recent": keep_recent, "groups": len(tool_groups)},
                    data=spill_payload,
                    kind="generic",
                    summary=f"上下文压缩暂存：{len(tool_groups)} 个工具轮 + {len(text_msgs)} 条对话",
                )
            except Exception as e:  # noqa: BLE001 — spill 失败不阻塞压缩
                logger.warning("context compact spill failed: %s", e)

        # ── 5. 生成摘要（LLM 优先，失败降级机械摘要）──
        steps_text = ""
        for g in tool_groups:
            result_brief = " | ".join(r.replace("\n", " ")[:100] for r in g["results"][:3])
            steps_text += f"- 工具: {', '.join(g['tools'])}\n  结果: {result_brief}\n"
        dialogue_text = "\n".join(
            f"{m.get('role')}: {str(m.get('content', ''))[:200]}" for m in text_msgs
        )
        summary = await self._summarize_compact_material(
            steps_text=steps_text, dialogue_text=dialogue_text,
            llm_override=llm_override,
        )

        # ── 6. 摘要进 memory（供 Agent 整体思考/跨轮复用）──
        if user_id:
            try:
                await self._save_memory(
                    key=f"context_compact_{uuid.uuid4().hex[:8]}",
                    content=f"【上下文压缩摘要】{summary}",
                    category="note",
                    db=db, user_id=user_id,
                    default_provider=(llm_override or {}).get("default_provider"),
                )
            except Exception as e:  # noqa: BLE001 — memory 失败不阻塞恢复
                logger.warning("context compact memory_save failed: %s", e)

        # ── 7. 组装新 messages：骨架 + 压缩摘要消息 + 保留尾轮 ──
        note = "【早期步骤已压缩】以下为本任务早期执行步骤的摘要：\n" + summary
        if spill_id:
            note += f"\n（原始完整数据已暂存 spill_id=`{spill_id}`，需要细节可调 context_spill_read 精确查回）"
        compact_note = {"role": "assistant", "content": note}

        return messages[:skeleton_end] + [compact_note] + messages[keep_start:]

    async def _summarize_compact_material(
        self,
        steps_text: str,
        dialogue_text: str,
        llm_override: Optional[Dict[str, Any]] = None,
    ) -> str:
        """压缩早期步骤材料为结构化摘要。LLM 不可用/失败时降级为机械摘要。"""
        material = steps_text
        if dialogue_text:
            material += "\n早期对话：\n" + dialogue_text

        override = dict(llm_override or {})
        try:
            from app.services.llm.prompts import PromptManager
            from app.services.llm.router import TaskType
            prompt = PromptManager.format_prompt(
                "context_compact",
                steps=material[:12000],
                max_chars=self.COMPACT_SUMMARY_MAX_CHARS,
            )
            summary = await llm_router.route_and_complete(
                TaskType.SUMMARIZATION,
                [{"role": "user", "content": prompt}],
                max_tokens=1500,
                **override,
            )
            summary = (summary or "").strip()
            if summary:
                return summary[: self.COMPACT_SUMMARY_MAX_CHARS]
        except Exception as e:  # noqa: BLE001 — LLM 失败降级机械摘要
            logger.warning("context compact LLM summary failed, fallback to mechanical: %s", e)

        # 机械摘要兜底（不依赖 LLM）：直接截取材料前缀
        mechanical = steps_text[: self.COMPACT_SUMMARY_MAX_CHARS]
        if dialogue_text and len(mechanical) < 200:
            mechanical += "\n早期对话：" + dialogue_text[:400]
        return mechanical.strip() or "（早期步骤无文本内容）"

    @staticmethod
    def _classify_error(result: Any) -> Optional[str]:
        """将工具错误归类为可路由的 error_type。

        Returns:
            "config_error" | "auth_error" | "timeout" | "tool_unavailable" | "data_error" | None
        """
        if isinstance(result, dict):
            err = str(result.get("error") or result.get("message") or "")
        elif isinstance(result, str):
            err = result
        else:
            return None
        low = err.lower()
        if not low:
            return None
        # 注意顺序：auth 判定先于 config（"用户名和密码"只出现在认证上下文）
        if any(k in low for k in ("401", "403", "认证失败", "auth required", "未授权", "用户名和密码", "登录")):
            return "auth_error"
        if any(k in low for k in ("timeout", "readtimeout", "timed out", "超时")):
            return "timeout"
        if any(k in low for k in ("未配置", "不存在: ", "服务器未配置", "无 url", "not configured", "client not found")):
            return "config_error"
        if any(k in low for k in ("未知的工具", "未知的函数", "unavailable", "不可用")):
            return "tool_unavailable"
        if any(k in low for k in ("404", "不存在", "未找到", "not found", "失败")):
            return "data_error"
        return None

    def _build_meta_status(self, tool_round: int, state: Dict[str, Any],
                           max_rounds: Optional[int] = None) -> str:
        """生成每轮的元认知提示（LLM 的执行仪表盘）。

        不污染 messages 历史——仅在本次 LLM 调用时以副本注入。
        让 LLM 看见：轮次/已完成/失败清单/剩余预算/停滞警告。
        """
        max_rounds = max_rounds or self.MAX_TOOL_ROUNDS
        parts = [f"[执行状态] 当前第 {tool_round}/{max_rounds} 轮。"]
        if state.get("done"):
            done = list(dict.fromkeys(state["done"]))[-6:]
            parts.append("已完成: " + "、".join(done))
        if state.get("failed"):
            fails = list(state["failed"].items())[-5:]
            parts.append("已失败: " + "、".join(f"{k.split(':')[0]}({v}次)" for k, v in fails))
        remaining = max_rounds - tool_round
        if remaining <= 10:
            parts.append(f"剩余 {remaining} 轮预算，请尽快收敛。")
        if state.get("stall_count", 0) >= 2:
            parts.append(
                f"已连续 {state['stall_count']} 轮没有取得新进展。"
                f"请改变策略：换工具/换参数/换数据源。若确实无可行路径，"
                f"请直接向用户说明情况，交付已取得的部分结果并提供下一步建议，不要再重复相同尝试。"
            )
        parts.append("目标：完成用户请求。若无法完整完成，交付部分结果并说明剩余障碍。")
        return "\n".join(parts)

    def _build_graceful_exit(self, state: Dict[str, Any], last_error: Optional[str]) -> str:
        """生成优雅退出消息：已完成部分 + 障碍 + 建议方向（替代"40步报错"）。"""
        lines = []
        if state.get("done"):
            done = list(dict.fromkeys(state["done"]))[-8:]
            lines.append("已完成: " + "、".join(done))
        else:
            lines.append("当前进展: 尚未取得可交付的结果。")
        if last_error:
            lines.append(f"遇到问题: {last_error}")
        if state.get("failed"):
            reasons = "、".join(k.split(":")[0] for k in list(state["failed"].keys())[-5:])
            if reasons:
                lines.append(f"受阻工具: {reasons} 多次失败。")
        lines.append(
            "建议: A. 简化请求或补充必要信息（如提供数据源地址/文件/截图）重试; "
            "B. 更换数据源或改用其他方式（描述你的目标，我会换思路尝试）; "
            "C. 手动处理，我可提供操作步骤。"
        )
        return "\n".join(lines)

    # ── Confirmation handling (deprecated) ──

    async def handle_confirmation(
        self,
        execution_id: str,
        step: int,
        confirmed: bool,
        conversation_id: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> AsyncGenerator[AgentResponse, None]:
        """Handle user confirmation for a pending action.

        .. deprecated::
            Confirmations are now handled via natural language
            (LLM asks → user responds). Kept only as fallback.
        """
        if not confirmed:
            self._pending_plans.pop(execution_id, None)
            yield AgentResponse(type="text", content="已取消操作。")
            yield AgentResponse(type="complete")
            return

        pending = self._pending_plans.pop(execution_id, None)
        if not pending:
            # In the new architecture, _pending_plans may be empty because
            # confirmations are conversational. Just re-enter the LLM loop.
            yield AgentResponse(type="thinking", content="继续执行...")
            async for resp in self.process_message(
                "用户已确认，请继续执行之前的操作。",
                conversation_id=conversation_id,
                user_id=user_id,
            ):
                yield resp
            return

        # Re-inject confirmation as a user message into the processing loop
        yield AgentResponse(type="thinking", content="继续执行...")
        async for resp in self.process_message(
            "用户已确认之前的操作，请继续执行。",
            conversation_id=conversation_id,
            user_id=user_id,
        ):
            yield resp


# Global Agent engine instance
agent_engine = AgentEngine()
