"""
Memory System — Four-layer architecture with database persistence.

Layer 1: System Memory   — Agent's own knowledge (Skills, tools). Immutable per session.
Layer 2: Working Memory  — Current conversation context (sliding window + LLM compaction).
Layer 3: Session Memory  — Current login session state (persisted to DB).
Layer 4: Long-term Memory — Persistent user/project knowledge (persisted to DB).

== Design ==
All four layers are aggregated by MemorySystem.get_context() which is called
at the START of every agent pipeline turn, so every intent-understanding
and planning prompt sees the full memory picture.
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, date, timedelta
from collections import Counter, OrderedDict
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.crud.memory import (
    get_user_profile as db_get_user_profile,
    update_user_profile as db_update_user_profile,
    list_learning_records as db_list_learning_records,
    create_learning_record as db_create_learning_record,
    save_session_memory as db_save_session_memory,
    get_session_memory as db_get_session_memory,
    create_memory_log as db_create_memory_log,
    list_memory_logs as db_list_memory_logs,
    count_undistilled_logs as db_count_undistilled_logs,
    mark_logs_distilled as db_mark_logs_distilled,
    mark_logs_distilled_by_ids as db_mark_logs_distilled_by_ids,
    delete_old_logs as db_delete_old_logs,
    delete_stale_learning_records as db_delete_stale_learning_records,
)
from app.crud.conversation import get_messages as db_get_messages
from app.services.llm.router import llm_router, TaskType

logger = logging.getLogger(__name__)


# ── Layer 1: System Memory ───────────────────────────────────────────────────

class SystemMemory:
    """Layer 1: System Memory — Agent's own knowledge (Skills, tools, prompts).

    Populated at startup from Skill registration. Read-only during execution.
    Stays in-memory because it is re-built on every restart from code.
    """

    def __init__(self):
        self.skills: Dict[str, Dict] = {}
        self.tools: Dict[str, Dict] = {}

    def add_skill(self, name: str, description: str, params: List[str] = None):
        self.skills[name] = {"name": name, "description": description, "params": params or []}

    def add_tool(self, server: str, name: str, description: str):
        key = f"{server}/{name}"
        self.tools[key] = {"server": server, "name": name, "description": description}

    def add_builtin_tool(self, name: str, description: str, input_types: list = None):
        self.tools[name] = {"name": name, "description": description, "input_types": input_types or []}

    def get_skill_descriptions(self) -> str:
        return "\n".join(f"- {name}: {info['description']}" for name, info in self.skills.items())

    def get_tool_descriptions(self) -> str:
        return "\n".join(f"- {key}: {info['description']}" for key, info in self.tools.items())


# ── Layer 2: Working Memory ──────────────────────────────────────────────────

class WorkingMemory:
    """Layer 2: Working Memory — Current conversation context.

    Maintains a sliding window of recent messages (default 20).
    When the window overflows, older messages are compacted via LLM summary
    so the conversation's early context is not lost entirely.
    """

    def __init__(self, conversation_id: str = None):
        self.conversation_id = conversation_id
        self.messages: List[Dict[str, Any]] = []
        self.entities: Dict[str, List[Dict]] = {}
        self.intent_stack: List[str] = []
        self.context_vars: Dict[str, Any] = {}
        self.max_messages = 20
        self.compacted_summaries: List[str] = []  # LLM-compacted summaries of older turns
        self.user_id: Optional[int] = None          # Set externally for shutdown flush
        self._compacted_recently: bool = False      # Reset each check via was_compacted()
        self._pending_llm_summary: Optional[List[Dict]] = None  # Overflow msgs for async LLM

    def add_message(self, role: str, content: str, message_type: str = "text"):
        self.messages.append({
            "role": role,
            "content": content,
            "type": message_type,
            "timestamp": datetime.utcnow().isoformat(),
        })
        # When approaching the limit, keep a summary of older messages
        if len(self.messages) > self.max_messages + 2:
            overflow = self.messages[:-self.max_messages]
            summary = self._quick_summarize(overflow)
            self.compacted_summaries.append(summary)
            self.messages = self.messages[-self.max_messages:]
            self._compacted_recently = True
            self._pending_llm_summary = overflow  # Store for async LLM enhancement

    def add_entity(self, entity_type: str, value: Any):
        if entity_type not in self.entities:
            self.entities[entity_type] = []
        self.entities[entity_type].append({"value": value, "timestamp": datetime.utcnow().isoformat()})

    def set_context_var(self, key: str, value: Any):
        self.context_vars[key] = value

    def push_intent(self, intent: str):
        self.intent_stack.append(intent)

    def pop_intent(self) -> Optional[str]:
        return self.intent_stack.pop() if self.intent_stack else None

    def get_recent_messages(self, n: int = 10) -> List[Dict]:
        return self.messages[-n:]

    def get_context_summary(self) -> str:
        parts = []
        if self.compacted_summaries:
            parts.append("【对话历史摘要】")
            parts.extend(f"- {s}" for s in self.compacted_summaries[-3:])
        if self.context_vars:
            parts.append("当前上下文变量:")
            for k, v in self.context_vars.items():
                parts.append(f"  - {k}: {v}")
        if self.entities:
            parts.append("已提及的实体:")
            for etype, values in self.entities.items():
                latest = values[-1]["value"] if values else None
                parts.append(f"  - {etype}: {latest}")
        return "\n".join(parts)

    def was_compacted(self) -> bool:
        """Check and reset the compaction flag.

        Returns True if add_message() compacted the window since the last check.
        Used by MemorySystem to trigger a memory flush when overflow occurs.
        """
        val = self._compacted_recently
        self._compacted_recently = False
        return val

    def pop_pending_llm_summary(self) -> Optional[List[Dict]]:
        """Get and clear overflow messages awaiting LLM summarization.

        Returns the overflow message list if compaction just happened,
        or None if no pending LLM summarization is needed.
        """
        data = self._pending_llm_summary
        self._pending_llm_summary = None
        return data

    def _quick_summarize(self, overflow_messages: List[Dict]) -> str:
        """Compress overflow messages into a structured one-line summary.

        Produces a rule-based summary immediately (synchronous path).
        The same overflow is also sent for async LLM enhancement
        which may replace this summary with a semantic version later.
        """
        user_msgs = [m for m in overflow_messages if m.get("role") == "user"]
        assistant_msgs = [m for m in overflow_messages if m.get("role") == "assistant"]
        parts = []

        # 1. User messages — extract first complete sentence
        if user_msgs:
            topics = []
            for m in user_msgs[:5]:
                content = m.get("content", "")
                sentence = self._extract_first_sentence(content, max_len=150)
                if sentence:
                    topics.append(sentence)
            if topics:
                parts.append(f"[用户] {'; '.join(topics)}")

        # 2. Tool usage — detect concrete tool names in assistant messages
        if assistant_msgs:
            tool_names = set()
            all_assistant_text = " ".join(m.get("content", "") or "" for m in assistant_msgs).lower()
            for kw in ["jira", "confluence", "gerrit", "search", "skill", "mcp",
                        "memory", "docx", "pdf", "xlsx", "pptx", "ocr"]:
                if kw in all_assistant_text:
                    tool_names.add(kw)
            # Also check tool-related keywords
            for tool_kw in ["查询", "搜索", "报告", "汇总", "创建", "更新"]:
                if tool_kw in all_assistant_text:
                    tool_names.add(tool_kw)
            if tool_names:
                parts.append(f"[工具] {', '.join(sorted(tool_names))}")

        # 3. Entities — scan overflow content for entity types tracked in self.entities
        if self.entities:
            overflow_text = " ".join(m.get("content", "") or "" for m in overflow_messages).lower()
            entity_refs = []
            for etype, values in self.entities.items():
                if values:
                    latest = values[-1]
                    val = latest.get("value") if isinstance(latest, dict) else latest
                    if val and str(val).lower() in overflow_text:
                        entity_refs.append(f"{etype}={val}")
            if entity_refs:
                parts.append(f"[实体] {'; '.join(entity_refs[:5])}")

        # 4. Decision patterns — detect decision keywords in user messages
        decision_kws = ["记住", "保存", "重要", "决定", "确认", "就这么办", "采用"]
        decisions_found = []
        for m in user_msgs:
            content = m.get("content", "") or ""
            for kw in decision_kws:
                if kw in content:
                    # Extract the sentence containing the decision keyword
                    sentences = self._extract_sentences(content)
                    for s in sentences:
                        if kw in s:
                            decisions_found.append(s[:100])
                            break
                    break
        if decisions_found:
            parts.append(f"[决策] {'; '.join(decisions_found[:3])}")

        # 5. Context variables
        if self.context_vars:
            var_strs = [f"{k}={v}" for k, v in self.context_vars.items()]
            parts.append(f"[上下文] {'; '.join(var_strs[:4])}")

        return " | ".join(parts) if parts else "(对话片段)"

    @staticmethod
    def _extract_first_sentence(text: str, max_len: int = 150) -> str:
        """Extract the first complete sentence from text."""
        if not text:
            return ""
        sentences = WorkingMemory._extract_sentences(text)
        if sentences:
            s = sentences[0][:max_len]
            return s
        return text[:max_len]

    @staticmethod
    def _extract_sentences(text: str) -> List[str]:
        """Split text into sentences by Chinese/English sentence boundaries."""
        import re
        # Split by Chinese sentence-ending punctuation or English period + space/caps
        parts = re.split(r'(?<=[。！？\.\!\?\n])\s*', text)
        return [p.strip() for p in parts if p.strip()]


# ── Layer 3: Session Memory ──────────────────────────────────────────────────

class SessionMemory:
    """Layer 3: Session Memory — Current login session state.

    Persisted to the session_memories table. Falls back to in-memory
    when no DB session is available.
    """

    def __init__(self, session_id: str = None, user_id: int = None):
        self.session_id = session_id
        self.user_id = user_id
        self.active_project: Optional[str] = None
        self.active_config: Optional[str] = None
        self.skill_results: Dict[str, Any] = {}
        self.preferences: Dict[str, Any] = {}
        self.created_at = datetime.utcnow()

    def set_active_project(self, project: str):
        self.active_project = project

    def cache_skill_result(self, skill_name: str, result: Any):
        self.skill_results[skill_name] = {"result": result, "timestamp": datetime.utcnow().isoformat()}

    def get_cached_result(self, skill_name: str) -> Optional[Any]:
        cached = self.skill_results.get(skill_name)
        return cached["result"] if cached else None

    def set_preference(self, key: str, value: Any):
        self.preferences[key] = value

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "active_project": self.active_project,
            "active_config": self.active_config,
            "preferences": self.preferences,
        }


# ── Layer 4: Long-term Memory ────────────────────────────────────────────────

class LongTermMemory:
    """Layer 4: Long-term Memory — Persistent user/project knowledge.

    Backed by the database (user_profiles / learning_records / project_memories tables).
    Every read/write goes through the CRUD layer.
    """

    async def get_user_profile(self, db: AsyncSession, user_id: int) -> Optional[dict]:
        profile = await db_get_user_profile(db, user_id)
        if profile is None:
            return None
        return {
            "role": profile.role,
            "preferred_projects": profile.preferred_projects or [],
            "preferred_skills": profile.preferred_skills or [],
            "communication_style": profile.communication_style,
            "alert_threshold": profile.alert_threshold,
            "learned_preferences": profile.learned_preferences or {},
        }

    async def update_user_profile(self, db: AsyncSession, user_id: int, updates: dict):
        await db_update_user_profile(db, user_id, updates)

    async def get_learning_records(self, db: AsyncSession, user_id: int, limit: int = 20) -> List[dict]:
        records = await db_list_learning_records(db, user_id, limit=limit)
        return [
            {
                "pattern": r.pattern,
                "context": r.context,
                "success_rate": float(r.success_rate) if r.success_rate else 0,
                "usage_count": r.usage_count,
            }
            for r in records
        ]

    async def add_learning_record(self, db: AsyncSession, user_id: int, pattern: str, context: str = None):
        await db_create_learning_record(db, user_id, pattern, context)


# ── MemorySystem (aggregator) ────────────────────────────────────────────────

class MemorySystem:
    """Four-layer memory system manager.

    Usage:
        context = await memory_system.get_context(db, conversation_id, user_id, session_id, message)
        # → use context dict in prompts

        await memory_system.update_from_execution(db, conversation_id, user_id, session_id, ...)
        # → persist execution data
    """

    def __init__(self):
        self.system_memory = SystemMemory()
        self.working_memories: Dict[str, WorkingMemory] = {}
        self.session_memories: Dict[str, SessionMemory] = {}
        self.long_term_memory = LongTermMemory()
        # Turn counters for auto-distillation trigger (keyed by user_id__conversation_id, LRU capped at 200)
        self._turn_counters: OrderedDict[str, int] = OrderedDict()
        # Distillation threshold: run maintenance every N turns
        self.distillation_interval = 10
        # Circuit breaker for LLM distillation (per-user, skip after 3 consecutive failures, reset after 1h)
        self._llm_distill_failures: Dict[int, int] = {}  # user_id → failure count
        self._llm_distill_max_failures = 3
        self._llm_distill_last_attempt: Dict[int, datetime] = {}  # user_id → last attempt time
        self._llm_distill_cooldown_minutes = 60

    def _wm_key(self, conversation_id: str, user_id: int = None) -> str:
        return f"{user_id or 0}__{conversation_id}"

    def _sm_key(self, session_id: str, user_id: int = None) -> str:
        return f"{user_id or 0}__{session_id}"

    def get_working_memory(self, conversation_id: str, user_id: int = None) -> WorkingMemory:
        key = self._wm_key(conversation_id, user_id)
        if key not in self.working_memories:
            self.working_memories[key] = WorkingMemory(conversation_id)
        return self.working_memories[key]

    def get_session_memory(self, session_id: str, user_id: int = None) -> SessionMemory:
        key = self._sm_key(session_id, user_id)
        if key not in self.session_memories:
            self.session_memories[key] = SessionMemory(session_id, user_id)
        return self.session_memories[key]

    async def get_context(
        self,
        db: Optional[AsyncSession] = None,
        conversation_id: str = None,
        user_id: int = None,
        session_id: str = None,
        message: str = None,
    ) -> Dict[str, Any]:
        """Aggregate all four memory layers into a single context dict.

        This is called at the start of every agent pipeline turn.
        The returned dict is injected into intent-understanding and planning prompts.
        """
        context = {"available_skills": self.system_memory.get_skill_descriptions()}

        # ── Layer 1: System Memory ──
        context["available_skills"] = self.system_memory.get_skill_descriptions()
        context["available_tools"] = self.system_memory.get_tool_descriptions()

        # ── Layer 2: Working Memory ──
        if conversation_id:
            wm = self.get_working_memory(conversation_id, user_id)
            # Restore from DB on restart: load recent messages if working memory is empty
            if not wm.messages and db and conversation_id:
                try:
                    db_messages = await db_get_messages(db, conversation_id=int(conversation_id))
                    for m in db_messages[-10:]:
                        wm.add_message(m.role, m.content, m.message_type)
                except Exception:
                    logger.warning("Failed to load messages from DB into working memory", exc_info=True)
            wm_summary = wm.get_context_summary()
            if wm_summary:
                context["conversation_context"] = wm_summary
            context["recent_messages"] = wm.get_recent_messages(6)

        # ── Layer 3: Session Memory ──
        if session_id:
            sm = self.get_session_memory(session_id, user_id)
            # Restore from DB on restart: load session state if in-memory is fresh
            if db and not sm.skill_results and not sm.preferences:
                try:
                    db_session = await db_get_session_memory(db, session_id)
                    if db_session:
                        if db_session.active_project:
                            sm.set_active_project(db_session.active_project)
                        if db_session.active_config:
                            sm.active_config = db_session.active_config
                        if db_session.preferences:
                            sm.preferences = dict(db_session.preferences) if isinstance(db_session.preferences, dict) else {}
                        if db_session.skill_results:
                            sm.skill_results = dict(db_session.skill_results) if isinstance(db_session.skill_results, dict) else {}
                except Exception:
                    logger.warning("Failed to load session memory from DB", exc_info=True)
            session_dict = sm.to_dict()
            if session_dict.get("active_project"):
                context["active_project"] = session_dict["active_project"]
            if session_dict.get("preferences"):
                context["session_preferences"] = session_dict["preferences"]

        # ── Layer 4: Long-term Memory ──
        if db and user_id:
            profile = await self.long_term_memory.get_user_profile(db, user_id)
            if profile:
                context["user_profile"] = profile

            patterns = await self.long_term_memory.get_learning_records(db, user_id)
            if patterns:
                context["learned_patterns"] = patterns

        return context

    async def update_from_execution(
        self,
        db: Optional[AsyncSession] = None,
        conversation_id: str = None,
        user_id: int = None,
        session_id: str = None,
        message: str = None,
        intent: Any = None,
        plan: Any = None,
        result: Any = None,
    ):
        """Persist execution results across all four memory layers."""
        # ── Layer 2: Working Memory ──
        if conversation_id:
            wm = self.get_working_memory(conversation_id, user_id)
            wm.user_id = user_id  # Track for shutdown flush
            if message:
                wm.add_message("user", message)
                # Check if compaction just happened → flush overflow data + fire async LLM
                if wm.was_compacted():
                    if db and user_id:
                        try:
                            await self.flush_important_context(
                                db, user_id, conversation_id, reason="overflow"
                            )
                        except Exception:
                            logger.warning("Overflow memory flush failed", exc_info=True)
                    # Fire-and-forget async LLM summary enhancement (non-blocking)
                    overflow = wm.pop_pending_llm_summary()
                    if overflow:
                        asyncio.create_task(
                            self._llm_enhance_summary(wm, overflow)
                        )
            if result:
                if isinstance(result, str):
                    # 单脑架构：result 为 final_content（LLM 最终回复文本）
                    wm.add_message("assistant", result[:2000], message_type="text")
                elif isinstance(result, dict):
                    # 兼容旧三层架构的 ExecutionResult 或纯 dict 结果
                    output = result.get("final_output", json.dumps(result, ensure_ascii=False))
                    wm.add_message("assistant", str(output)[:2000], message_type="card")
            if intent:
                if hasattr(intent, "entities") and intent.entities:
                    for key, value in intent.entities.items():
                        if isinstance(value, (str, int, float)):
                            wm.add_entity(key, value)
                if hasattr(intent, "target_skill") and intent.target_skill:
                    wm.set_context_var("last_skill", intent.target_skill)

        # ── Layer 3: Session Memory (DB + in-memory) ──
        if session_id:
            sm = self.get_session_memory(session_id, user_id)
            if intent and hasattr(intent, "target_skill") and intent.target_skill:
                sm.cache_skill_result(intent.target_skill, result)
            # Persist to DB
            if db and user_id:
                try:
                    await db_save_session_memory(
                        db,
                        session_id=session_id,
                        user_id=user_id,
                        active_project=sm.active_project,
                        skill_results=sm.skill_results,
                        preferences=sm.preferences,
                    )
                except Exception:
                    logger.warning("Session memory DB write failed", exc_info=True)

        # ── Layer 4: Long-term Memory (DB) ──
        if db and user_id:
            try:
                skill_name = intent.target_skill if (intent and hasattr(intent, "target_skill")) else None
                await self.long_term_memory.update_user_profile(
                    db, user_id,
                    {"last_active": datetime.utcnow().isoformat()},
                )
                if skill_name:
                    await self.long_term_memory.update_user_profile(
                        db, user_id,
                        {"preferred_skills": skill_name},
                    )
            except Exception:
                logger.warning("Long-term memory DB write failed", exc_info=True)

    # ── Stage 1: Daily Session Log ───────────────────────────────────────

    async def log_conversation_turn(
        self,
        db: AsyncSession,
        user_id: int,
        session_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        user_message: Optional[str] = None,
        assistant_response: Optional[str] = None,
        tool_calls: Optional[List[Dict]] = None,
    ) -> None:
        """Log a conversation turn to the daily session log (Stage 1).

        Creates a structured log entry for the current turn, which feeds
        into periodic distillation. Best-effort: failures are logged but
        do not propagate.
        """
        if not db or not user_id:
            return

        today = date.today().isoformat()

        # Increment turn counter for auto-distillation trigger
        if conversation_id:
            key = f"{user_id}__{conversation_id}"
            self._turn_counters[key] = self._turn_counters.get(key, 0) + 1
            self._turn_counters.move_to_end(key)
            # LRU eviction: keep newest 200
            while len(self._turn_counters) > 200:
                self._turn_counters.popitem(last=False)

        # Build topic hints from tool usage and user message keywords
        tool_usage = []
        key_topics = []
        if tool_calls:
            for tc in tool_calls:
                name = tc.get("name", tc.get("function", {}).get("name", "unknown"))
                args = tc.get("arguments", tc.get("function", {}).get("arguments", {}))
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                tool_usage.append({"tool": name, "args": args})
                if name in ("memory_save",):
                    key_topics.append(args.get("key", ""))

        # Extract potential topics from user message (simple keyword extraction)
        if user_message:
            words = user_message.split()
            for token in words[:10]:
                cleaned = token.strip("，。！？、；：""''（）【】").strip()
                if 2 <= len(cleaned) <= 20 and not cleaned.isascii():
                    key_topics.append(cleaned)

        # Build a concise assistant summary
        assistant_summary = (assistant_response or "")[:200]
        user_preview = (user_message or "")[:200]

        # Limit topics to avoid excessive DB bloat
        key_topics = list(dict.fromkeys(key_topics))[:10]

        try:
            await db_create_memory_log(
                db,
                user_id=user_id,
                date_str=today,
                session_id=session_id,
                turn_index=self._turn_counters.get(f"{user_id}__{conversation_id}", 0),
                user_message_preview=user_preview,
                assistant_summary=assistant_summary,
                key_topics=key_topics,
                tool_usage=tool_usage,
            )
        except Exception as e:
            logger.warning("Failed to log conversation turn: %s", e)

        # (Daily log file write removed — consolidated into DB memory_logs table only)

    # ── Stage 2: Distillation ────────────────────────────────────────────

    async def distill_recent_logs(
        self,
        db: AsyncSession,
        user_id: int,
        days_back: int = 7,
    ) -> Dict[str, Any]:
        """Distill recent session logs into long-term memory (Stage 2).

        Two-pass approach:
        1. Heuristic pass: frequency-based topic/tool extraction (fast, always runs)
        2. LLM pass: semantic understanding for nuanced patterns (if enough logs)

        Heuristic results are saved immediately. LLM insights are merged
        on top as additional learning records.
        """
        stats = {"logs_processed": 0, "preferences_added": 0, "records_added": 0,
                 "llm_insights_added": 0}

        # 1. Read un-distilled logs
        logs = await db_list_memory_logs(db, user_id, undistilled_only=True, limit=200)
        if not logs:
            return stats

        # 2. Extract frequent topics
        topic_counter: Counter = Counter()
        tools_used: Counter = Counter()
        key_topics_mentioned: Counter = Counter()

        for log in logs:
            topics = log.key_topics if isinstance(log.key_topics, list) else []
            for t in topics:
                if isinstance(t, str) and len(t) >= 2:
                    topic_counter[t] += 1

            usage = log.tool_usage if isinstance(log.tool_usage, list) else []
            for u in usage:
                if isinstance(u, dict) and "tool" in u and u["tool"] != "memory_save":
                    tools_used[u["tool"]] += 1

        # 3. Surface frequent topics as learned_preferences
        frequent_topics = [t for t, c in topic_counter.most_common(5) if c >= 2]
        if frequent_topics:
            try:
                await db_update_user_profile(
                    db, user_id,
                    {
                        "learned_preferences": {
                            "frequent_topics": frequent_topics,
                            f"_distilled_{date.today().isoformat()}": {
                                "topics": frequent_topics,
                                "tools": [t for t, _ in tools_used.most_common(5)],
                            },
                        }
                    },
                )
                stats["preferences_added"] = len(frequent_topics)
            except Exception as e:
                logger.warning("Distillation: failed to update preferences: %s", e)

        # 4. Create learning records for new recurring topics
        for topic in frequent_topics:
            try:
                await db_create_learning_record(
                    db, user_id,
                    pattern=f"[distilled] 用户频繁提及: {topic}",
                    context=f"通过每日记忆日志蒸馏自动提取。来源: 最近 {days_back} 天的对话记录。",
                )
                stats["records_added"] += 1
            except Exception as e:
                logger.warning("Distillation: failed to create learning record: %s", e)
                continue

        # 5. LLM-enhanced semantic distillation (if enough logs exist + circuit breaker)
        if len(logs) >= 5:
            # Per-user circuit breaker
            user_failures = self._llm_distill_failures.get(user_id, 0)
            user_last_attempt = self._llm_distill_last_attempt.get(user_id)

            # Check circuit breaker: reset counter if cooldown has elapsed
            if user_failures >= self._llm_distill_max_failures:
                if (user_last_attempt and
                        (datetime.utcnow() - user_last_attempt).total_seconds() > self._llm_distill_cooldown_minutes * 60):
                    self._llm_distill_failures[user_id] = 0
                    logger.info("LLM distillation circuit breaker reset for user %s after cooldown", user_id)
                else:
                    logger.info("LLM distillation skipped for user %s: %d consecutive failures",
                                user_id, user_failures)

            if self._llm_distill_failures.get(user_id, 0) < self._llm_distill_max_failures:
                try:
                    self._llm_distill_last_attempt[user_id] = datetime.utcnow()
                    llm_stats = await self._llm_distill(db, user_id, logs)
                    stats["llm_insights_added"] = llm_stats.get("insights_saved", 0)
                    self._llm_distill_failures[user_id] = 0  # reset on success
                except Exception as e:
                    self._llm_distill_failures[user_id] = self._llm_distill_failures.get(user_id, 0) + 1
                    logger.debug("LLM distillation failed for user %s (%d/%d): %s",
                                 user_id, self._llm_distill_failures[user_id], self._llm_distill_max_failures, e)

        # 6. Mark only the processed logs as distilled (not all logs up to max_id)
        if logs:
            log_ids = [log.id for log in logs]
            stats["logs_processed"] = await db_mark_logs_distilled_by_ids(db, user_id, log_ids)

        logger.info("Distillation complete for user %s: %s", user_id, stats)
        return stats

    async def _llm_distill(
        self,
        db: AsyncSession,
        user_id: int,
        logs: List,
    ) -> Dict[str, Any]:
        """LLM-enhanced semantic distillation of conversation logs.

        Takes raw logs, sends a batch to commercial LLM for semantic analysis,
        extracts user preferences, decisions, and recurring themes that
        frequency-based heuristics would miss.

        Graceful degradation: if LLM call fails, returns empty stats.
        """
        stats = {"insights_saved": 0}

        # Build a compact log summary for the LLM
        log_lines = []
        for log in logs[-30:]:  # Last 30 entries max
            user_msg = (log.user_message_preview or "")[:150]
            assistant = (log.assistant_summary or "")[:150]
            tools = ""
            usage = log.tool_usage if isinstance(log.tool_usage, list) else []
            if usage:
                tool_names = [u.get("tool", "") for u in usage if isinstance(u, dict)]
                tools = ", ".join(t for t in tool_names if t)
            log_lines.append(
                f"[{log.date} T{log.turn_index}] 用户: {user_msg}"
                f"{' | 工具: ' + tools if tools else ''}"
                f"{' | AI: ' + assistant if assistant else ''}"
            )

        if not log_lines:
            return stats

        prompt = (
            "你是一个记忆蒸馏助手。分析以下对话日志，提取3类信息：\n\n"
            "1. **用户偏好** — 用户明确表达的习惯、偏好、工作方式\n"
            "2. **重要决策** — 用户做出的决定、确认的方案、选择的路径\n"
            "3. **关注领域** — 用户反复提及的项目、Jira ID、技术方向\n\n"
            "以JSON格式输出，key为类别，value为字符串列表。不要加markdown代码块标记：\n"
            '{"偏好": ["..."], "决策": ["..."], "关注领域": ["..."]}\n\n'
            "如果没有发现某类信息，返回空列表。\n\n"
            "日志内容：\n" + "\n".join(log_lines)
        )

        try:
            result = await llm_router.route_and_complete(
                task_type=TaskType.SUMMARIZATION,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.3,
                json_mode=True,
            )
        except Exception as e:
            logger.debug("LLM distillation call failed: %s", e)
            return stats

        if not result or not result.strip():
            return stats

        # Parse JSON result
        try:
            insights = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            logger.debug("LLM distillation returned non-JSON, skipping")
            return stats

        # Save insights as learning records
        for category, items in insights.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, str) or len(item) < 5:
                    continue
                try:
                    await db_create_learning_record(
                        db, user_id,
                        pattern=f"[llm_distill.{category}] {item[:300]}",
                        context=f"LLM语义蒸馏提取。来源类别: {category}",
                    )
                    stats["insights_saved"] += 1
                except Exception:
                    continue

        # Also save as learned_preferences summary
        try:
            summary = {k: v[:3] for k, v in insights.items() if isinstance(v, list)}
            if summary:
                await db_update_user_profile(
                    db, user_id,
                    {"learned_preferences": {f"_llm_distilled_{date.today().isoformat()}": summary}},
                )
        except Exception:
            pass

        if stats["insights_saved"]:
            logger.info("LLM distillation saved %d insights for user %s",
                        stats["insights_saved"], user_id)

        return stats

    # ── Stage 3: Cleanup ─────────────────────────────────────────────────

    async def cleanup_stale_memories(
        self,
        db: AsyncSession,
        user_id: int,
        log_retention_days: int = 30,
    ) -> Dict[str, Any]:
        """Clean up stale/expired memories for a specific user (Stage 3).

        - Deletes memory logs older than log_retention_days for this user
        - Deletes learning records with low usage + low success rate for this user
        """
        stats = {"logs_deleted": 0, "learning_records_deleted": 0}

        try:
            cutoff_date = (date.today() - timedelta(days=log_retention_days)).isoformat()
            stats["logs_deleted"] = await db_delete_old_logs(db, cutoff_date, user_id)
        except Exception as e:
            logger.warning("Cleanup for user %s: failed to delete old logs: %s", user_id, e)

        try:
            stats["learning_records_deleted"] = await db_delete_stale_learning_records(db, user_id=user_id)
        except Exception as e:
            logger.warning("Cleanup for user %s: failed to delete stale records: %s", user_id, e)

        if stats["logs_deleted"] or stats["learning_records_deleted"]:
            logger.info("Cleanup complete for user %s: %s", user_id, stats)
        return stats

    # ── Combined Maintenance ─────────────────────────────────────────────

    async def run_maintenance(
        self,
        db: AsyncSession,
        user_id: int,
        days_back: int = 7,
        log_retention_days: int = 30,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run full memory maintenance: distill + cleanup (Stages 2+3).

        Call this periodically (auto-triggered after N conversation turns
        or manually via API).
        """
        result = {"distillation": {}, "cleanup": {}}

        # Stage 2: Distill recent logs
        try:
            result["distillation"] = await self.distill_recent_logs(db, user_id, days_back)
        except Exception as e:
            logger.warning("Maintenance distillation failed: %s", e)
            result["distillation"] = {"error": str(e)}

        # Stage 3: Cleanup stale entries (per-user)
        try:
            result["cleanup"] = await self.cleanup_stale_memories(db, user_id, log_retention_days)
        except Exception as e:
            logger.warning("Maintenance cleanup failed for user %s: %s", user_id, e)
            result["cleanup"] = {"error": str(e)}

        # Reset turn counter if triggered by conversation
        if conversation_id:
            key = f"{user_id}__{conversation_id}"
            self._turn_counters[key] = 0
            self._turn_counters.move_to_end(key)

        return result

    # ── Maintenance Status ───────────────────────────────────────────────

    async def get_maintenance_status(self, db: AsyncSession, user_id: int) -> Dict[str, Any]:
        """Get memory maintenance status for display."""
        status = {"undistilled_logs": 0, "total_logs": 0, "turn_count": 0}

        try:
            status["undistilled_logs"] = await db_count_undistilled_logs(db, user_id)
            # Use count() for accurate total
            from sqlalchemy import func, select
            from app.models.memory import MemoryLog
            cnt = await db.execute(select(func.count()).select_from(MemoryLog).where(MemoryLog.user_id == user_id))
            status["total_logs"] = cnt.scalar() or 0
        except Exception as e:
            logger.warning("Failed to get maintenance status: %s", e)

        return status

    def should_run_maintenance(self, conversation_id: str, user_id: int = None) -> bool:
        """Check if the turn counter has crossed the distillation threshold."""
        key = f"{user_id}__{conversation_id}"
        count = self._turn_counters.get(key, 0)
        if count:
            self._turn_counters.move_to_end(key)  # Update LRU order on access
        return count > 0 and count % self.distillation_interval == 0

    # ── Memory Flush ──────────────────────────────────────────────────

    async def flush_important_context(
        self,
        db: AsyncSession,
        user_id: int,
        conversation_id: str,
        reason: str = "auto",
        default_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Flush important context from WorkingMemory to long-term memory.

        Safety net called before context is lost (compaction, max tool rounds,
        shutdown). Saves entities, context variables, and decision-like
        patterns to the database so they survive the loss of the working
        memory window.

        Args:
            db: Database session.
            user_id: Target user.
            conversation_id: Which conversation's working memory to flush.
            reason: Why this flush was triggered ("overflow", "max_rounds",
                    "shutdown", or "auto").

        Returns:
            Dict with counts of what was saved.
        """
        stats: Dict[str, Any] = {
            "entities_saved": 0,
            "vars_saved": 0,
            "patterns_saved": 0,
            "reason": reason,
        }

        wm = self.working_memories.get(self._wm_key(conversation_id, user_id))
        if not wm:
            return stats

        # 1. Save entities (project, user_preference, decision types) as learning_records
        for etype, values in wm.entities.items():
            if values:
                latest_value = values[-1].get("value") if isinstance(values[-1], dict) else values[-1]
                if latest_value:
                    try:
                        await db_create_learning_record(
                            db, user_id,
                            pattern=f"[flush.{etype}] {latest_value}",
                            context=f"从工作记忆冲洗保存。会话: {conversation_id}, 原因: {reason}",
                        )
                        stats["entities_saved"] += 1
                    except Exception:
                        logger.debug("Flush: failed to save entity %s", etype, exc_info=True)

        # 2. Save context_vars as learned_preferences (cap flush_ keys at 30)
        if wm.context_vars:
            try:
                flush_prefs = {f"flush_{k}": v for k, v in wm.context_vars.items()}
                # Prune old flush_* keys to prevent unbounded growth
                try:
                    profile = await db_get_user_profile(db, user_id)
                    if profile:
                        prefs = dict(profile.learned_preferences or {})
                        # Keep non-flush keys, merge new flush_ keys, cap total flush_ keys
                        non_flush = {k: v for k, v in prefs.items() if not k.startswith("flush_")}
                        old_flush = {k: v for k, v in prefs.items() if k.startswith("flush_")}
                        old_flush.update(flush_prefs)
                        # If over cap, drop oldest (dict preserves insertion order since 3.7)
                        if len(old_flush) > 30:
                            keys_to_drop = sorted(old_flush.keys())[:len(old_flush) - 30]
                            for k in keys_to_drop:
                                del old_flush[k]
                        old_flush.update(non_flush)
                        profile.learned_preferences = old_flush
                        await db.commit()
                        stats["vars_saved"] = len(wm.context_vars)
                    else:
                        await db_update_user_profile(db, user_id, {"learned_preferences": flush_prefs})
                        stats["vars_saved"] = len(wm.context_vars)
                except Exception:
                    await db_update_user_profile(db, user_id, {"learned_preferences": flush_prefs})
                    stats["vars_saved"] = len(wm.context_vars)
            except Exception as e:
                logger.debug("Flush: failed to save context vars: %s", e)

        # 3. Scan recent messages for decision-like patterns
        decision_keywords = ["记住", "保存", "记住这个", "重要", "决定", "确认方案", "就这么办"]
        for msg in wm.messages[-10:]:
            content = msg.get("content", "") or ""
            if any(kw in content for kw in decision_keywords):
                try:
                    await db_create_learning_record(
                        db, user_id,
                        pattern=f"[flush.decision] {content[:200]}",
                        context=f"对话中的决策/重要信息。会话: {conversation_id}, 原因: {reason}",
                    )
                    stats["patterns_saved"] += 1
                except Exception:
                    logger.debug("Flush: failed to save decision pattern", exc_info=True)

        if stats["entities_saved"] or stats["vars_saved"] or stats["patterns_saved"]:
            logger.info("Memory flush (%s): entities=%d vars=%d patterns=%d",
                        reason, stats["entities_saved"], stats["vars_saved"], stats["patterns_saved"])

        return stats

    # ── Async LLM Summary Enhancement ────────────────────────────────────

    async def _llm_enhance_summary(
        self,
        wm: WorkingMemory,
        overflow: List[Dict],
        default_provider: Optional[str] = None,
    ) -> None:
        """Enhance the last compacted summary using LLM (fire-and-forget).

        Called asynchronously after compaction to produce a semantic summary
        of the overflow messages. If successful, replaces the last rule-based
        summary. If LLM fails (exception, empty result), keeps the rule-based
        summary — graceful degradation.
        """
        if not overflow or not wm.compacted_summaries:
            return

        # Format overflow messages into a concise prompt
        lines = []
        for m in overflow:
            role = m.get("role", "unknown")
            content = (m.get("content", "") or "")[:500]
            lines.append(f"[{role}] {content}")

        prompt = (
            "请用一句话总结以下对话片段的要点，包含：\n"
            "- 用户的核心诉求或问题\n"
            "- 使用的工具或查询\n"
            "- 涉及的项目、Jira ID 或实体\n"
            "- 做出的决策或结论\n\n"
            "对话内容：\n" + "\n".join(lines[-20:])
        )

        try:
            result = await llm_router.route_and_complete(
                task_type=TaskType.SUMMARIZATION,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.3,
                default_provider=default_provider,
            )
            if result and result.strip():
                wm.compacted_summaries[-1] = f"[LLM摘要] {result.strip()}"
                logger.info("LLM summary enhancement applied (%d chars)", len(result))
            else:
                logger.debug("LLM summary enhancement returned empty result, keeping rule-based")
        except Exception as e:
            logger.debug("LLM summary enhancement failed: %s (keeping rule-based)", e)


# Global singleton
memory_system = MemorySystem()
