"""
Agent Context — 文件化上下文系统

将 IDENTITY.md / SOUL.md / USER.md / MEMORY.md / AGENTS.md / TOOLS.md 等文件
在启动时读取拼装为 System Prompt，注入给 LLM。
"""

import os
import re
from typing import Dict, Optional, Any
from datetime import datetime

_CONTEXT_DIR = os.path.dirname(os.path.abspath(__file__))

# Context injection size limits (matching OpenClaw's bootstrap limits)
CONTEXT_FILE_MAX_CHARS = 12000   # Per-section cap
CONTEXT_TOTAL_MAX_CHARS = 60000  # Total system prompt cap

# File content cache for context files (Opt 8).
# Keyed by filename, value is (content, mtime). Invalidated on mtime change.
_context_file_cache: Dict[str, tuple] = {}


def load_context_file(filename: str) -> Optional[str]:
    """Load a context file from the agent_context directory.

    Caches file content in memory after first read. Cache is invalidated
    automatically when the file's modification time changes, so edits to
    IDENTITY.md / SOUL.md / AGENTS.md / TOOLS.md are picked up on the
    next system prompt build without requiring a service restart.
    """
    path = os.path.join(_CONTEXT_DIR, filename)
    if not os.path.isfile(path):
        return None

    # Check cache: return cached content if file hasn't been modified
    try:
        current_mtime = os.path.getmtime(path)
    except OSError:
        current_mtime = 0

    cached = _context_file_cache.get(filename)
    if cached is not None:
        cached_content, cached_mtime = cached
        if current_mtime == cached_mtime:
            return cached_content

    # Load from disk
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    _context_file_cache[filename] = (content, current_mtime)
    return content


def clear_context_cache() -> None:
    """Clear the in-memory context file cache.

    Useful in tests or when forcing a full reload of all context files.
    """
    _context_file_cache.clear()


def _render_template(content: str, variables: Dict[str, str]) -> str:
    """Replace {{placeholders}} with values, remove unfilled ones."""
    def _replace(match):
        key = match.group(1).strip()
        if key in variables and variables[key]:
            return variables[key]
        return ""
    return re.sub(r'\{\{(.*?)\}\}', _replace, content)


def _truncate_section(content: str, max_chars: int) -> str:
    """Truncate content at a paragraph boundary, preserving markdown structure.

    Cuts at the last double-newline before max_chars if possible,
    otherwise at the last single newline. Appends '...' to indicate truncation.
    """
    if len(content) <= max_chars:
        return content
    cutoff = content[:max_chars]
    # Try paragraph boundary (double newline)
    para_break = cutoff.rfind('\n\n')
    if para_break > max_chars * 0.4:
        return cutoff[:para_break] + '\n\n...'
    # Try single newline
    line_break = cutoff.rfind('\n')
    if line_break > max_chars * 0.4:
        return cutoff[:line_break] + '\n...'
    # Last resort: hard cut
    return cutoff[:max_chars - 3] + '...'


def _truncate_sections_with_priority(
    sections: Dict[str, str],
    per_file_max: int = CONTEXT_FILE_MAX_CHARS,
    total_max: int = CONTEXT_TOTAL_MAX_CHARS,
    separator: str = "\n\n---\n\n",
) -> str:
    """Join sections with per-file cap and total cap, dropping low-priority sections.

    Priority order (highest = kept first):
      1. IDENTITY, SOUL, AGENTS, TOOLS, HEARTBEAT — core identity, always kept
      2. USER                            — user profile
      3. MEMORY, daily logs              — long-term context
      4. Runtime context                 — most volatile, dropped first

    Section keys in the dict carry their priority. The convention:
      core_*   → priority 0 (always kept)
      user_*   → priority 1
      memory_* → priority 2
      log_*    → priority 2
      runtime_*→ priority 3 (dropped first)

    Returns:
        Joined string that fits within total_max.
    """
    # Apply per-section truncation first
    truncated = {}
    priority_order: Dict[int, list] = {0: [], 1: [], 2: [], 3: []}
    for key, content in sections.items():
        capped = _truncate_section(content, per_file_max)
        truncated[key] = capped
        if key.startswith("core_"):
            priority_order[0].append(key)
        elif key.startswith("user_"):
            priority_order[1].append(key)
        elif key.startswith("memory_") or key.startswith("log_"):
            priority_order[2].append(key)
        elif key.startswith("runtime_"):
            priority_order[3].append(key)
        else:
            priority_order[3].append(key)  # unknown → lowest priority

    # Start with highest priority, add lower priorities until total limit is reached
    selected_keys = []
    for prio in sorted(priority_order.keys()):
        for key in priority_order[prio]:
            selected_keys.append(key)
        # Check if adding this priority level would exceed the limit
        joined = separator.join(truncated[k] for k in selected_keys)
        if len(joined) > total_max:
            # Remove the last priority level we added
            for _ in priority_order[prio]:
                selected_keys.pop()
            break

    return separator.join(truncated[k] for k in selected_keys)


def _build_user_profile_vars(extra_context: Dict[str, Any]) -> Dict[str, str]:
    """Build USER.md template variables from context data."""
    p = extra_context.get("user_profile") or {}
    projects = p.get("preferred_projects") or []
    skills = p.get("preferred_skills") or []
    return {
        "user_role": p.get("role", ""),
        "communication_style": p.get("communication_style", ""),
        "preferred_projects": ", ".join(projects) if isinstance(projects, list) else str(projects),
        "preferred_skills": ", ".join(skills) if isinstance(skills, list) else str(skills),
    }


def _build_memory_vars(extra_context: Dict[str, Any]) -> Dict[str, str]:
    """Build MEMORY.md template variables from context data."""
    p = extra_context.get("user_profile") or {}
    prefs = p.get("learned_preferences") or {}
    patterns = extra_context.get("learned_patterns") or []

    # ── Partition patterns by type ──
    decision_patterns = []
    project_patterns = []
    user_fact_patterns = []
    flush_patterns = []
    other_high_usage = []

    for r in patterns:
        pat = (r.get("pattern") or "").lower()
        if pat.startswith("[flush."):
            flush_patterns.append(r)
            # Also classify flush patterns by their sub-type
            if pat.startswith("[flush.decision]") or pat.startswith("[flush.user_preference]"):
                decision_patterns.append(r)
            elif pat.startswith("[flush.project"):
                project_patterns.append(r)
        elif pat.startswith("[llm_distill."):
            # LLM-distilled patterns go to high-usage pool
            other_high_usage.append(r)
        elif r.get("usage_count", 0) > 3:
            other_high_usage.append(r)
        else:
            # Map memory_save patterns (category: note/user_preference/decision/project_info)
            # to the appropriate section so they appear in MEMORY.md instead of being invisible.
            if pat.startswith("[decision"):
                decision_patterns.append(r)
            elif pat.startswith("[project"):
                project_patterns.append(r)
            elif pat.startswith("[user_preference"):
                user_fact_patterns.append(r)
            elif pat.startswith("[note"):
                user_fact_patterns.append(r)
            else:
                # Unknown pattern type — put in high-usage pool so it shows up somewhere
                other_high_usage.append(r)

    # ── user_facts: direct user descriptors —─
    user_fact_lines = [f"- {r['pattern']}" for r in user_fact_patterns[:5]]
    # Also add learned_preferences as user facts (key-value pairs)
    for k, v in prefs.items():
        if k.startswith("flush_"):
            continue  # flush vars are transient, shown in behavior_adjustments instead
        # learned_preferences values are dicts like {"content": "...", "category": "note"}
        # Extract clean text instead of showing raw Python dict format.
        if isinstance(v, dict):
            content = v.get("content", "")
            cat = v.get("category", "")
            label = f"[{cat}] " if cat else ""
            val_str = f"{label}{content}"
        else:
            val_str = str(v)
        user_fact_lines.append(f"- {k}: {val_str[:120]}")
    user_facts = "\n".join(user_fact_lines) if user_fact_lines else "（暂无记录）"

    # ── important_decisions: high-usage or explicitly tagged —─
    decision_sources = decision_patterns + other_high_usage
    decision_lines = [f"- {r['pattern']}" for r in decision_sources[:5]]
    important_decisions = "\n".join(decision_lines) if decision_lines else "（暂无记录）"

    # ── user_preferences: from learned_preferences dict, excluding flush_ keys —─
    pref_lines = []
    for k, v in prefs.items():
        if k.startswith("flush_"):
            continue
        if isinstance(v, dict):
            content = v.get("content", "")
            cat = v.get("category", "")
            label = f"[{cat}] " if cat else ""
            pref_lines.append(f"- {k}: {label}{content[:120]}")
        else:
            pref_lines.append(f"- {k}: {v[:120] if isinstance(v, str) else v}")
    user_preferences = "\n".join(pref_lines) if pref_lines else "（暂无记录）"

    # ── learned_lessons: low-success-rate patterns —─
    lessons = [r for r in patterns if r.get("success_rate", 1.0) < 0.7]
    lesson_lines = [f"- {r['pattern']}" for r in lessons[:5]]
    learned_lessons = "\n".join(lesson_lines) if lesson_lines else "（暂无记录）"

    # ── project_context: project-related patterns —─
    project_lines = [f"- {r['pattern']}" for r in project_patterns[:5]]
    project_context = "\n".join(project_lines) if project_lines else "（暂无记录）"

    # ── behavior_adjustments: flush patterns (overflow/max_rounds/shutdown artifacts) —─
    behavior_lines = [f"- {r['pattern']}" for r in flush_patterns[:5]]
    behavior_adjustments = "\n".join(behavior_lines) if behavior_lines else "（暂无记录）"

    return {
        "user_facts": user_facts,
        "important_decisions": important_decisions,
        "user_preferences": user_preferences,
        "learned_lessons": learned_lessons,
        "project_context": project_context,
        "behavior_adjustments": behavior_adjustments,
    }


def build_system_prompt(extra_context: Optional[Dict[str, Any]] = None) -> str:
    """
    Build the full system prompt from all context files.

    Loads IDENTITY.md, SOUL.md, AGENTS.md, TOOLS.md and assembles
    them into a single system prompt injected at the start of every
    LLM conversation turn.

    Applies per-section (12K chars) and total (60K chars) caps,
    dropping low-priority sections (runtime → logs → memory) when
    the total exceeds the limit.

    Args:
        extra_context: Optional dict of extra info to append
                       (e.g., current skills, tools, date)

    Returns:
        Complete system prompt string.
    """
    sections: Dict[str, str] = {}

    # ── Priority 0: Core identity (always kept) ──
    identity = load_context_file("IDENTITY.md")
    if identity:
        sections["core_identity"] = identity

    soul = load_context_file("SOUL.md")
    if soul:
        sections["core_soul"] = soul

    agents = load_context_file("AGENTS.md")
    if agents:
        sections["core_agents"] = agents

    tools = load_context_file("TOOLS.md")
    if tools:
        sections["core_tools"] = tools

    heartbeat = load_context_file("HEARTBEAT.md")
    if heartbeat:
        sections["core_heartbeat"] = heartbeat

    # ── Priority 1: User profile ──
    user_template = load_context_file("USER.md")
    if user_template and extra_context:
        profile_vars = _build_user_profile_vars(extra_context)
        rendered = _render_template(user_template, profile_vars)
        if rendered.strip():
            sections["user_profile"] = rendered

    # ── Priority 2: Long-term memory + daily logs ──
    memory_template = load_context_file("MEMORY.md")
    if memory_template and extra_context:
        memory_vars = _build_memory_vars(extra_context)
        rendered = _render_template(memory_template, memory_vars)
        if rendered.strip():
            sections["memory_longterm"] = rendered

    try:
        from app.db.session import AsyncSessionLocal as _LogSession
        _log_user_id = extra_context.get("_user_id") if extra_context else None
        if _log_user_id:
            from sqlalchemy import select, desc as _desc
            from app.models.memory import MemoryLog as _MemoryLog
            import asyncio
            async def _load_logs():
                async with _LogSession() as _db:
                    _result = await _db.execute(
                        select(_MemoryLog)
                        .where(_MemoryLog.user_id == _log_user_id)
                        .order_by(_desc(_MemoryLog.date), _desc(_MemoryLog.turn_index))
                        .limit(20)
                    )
                    return _result.scalars().all()
            _logs = asyncio.run(_load_logs())
            if _logs:
                _lines = []
                for _log in reversed(_logs):
                    _lines.append(f"## Turn {_log.turn_index}")
                    _lines.append("")
                    if _log.user_message_preview:
                        _lines.append(f"**User**: {_log.user_message_preview}")
                        _lines.append("")
                    if _log.tool_usage:
                        _tool_str = ", ".join(
                            f"{t.get('tool','?')}({t.get('args',{}).get('server','')}/{t.get('args',{}).get('tool','') or t.get('args',{}).get('skill_name','')})"
                            if t.get('args',{}).get('server') else f"{t.get('tool','?')}({t.get('args',{}).get('tool','') or t.get('args',{}).get('skill_name','')})"
                            for t in _log.tool_usage[:5]
                        )
                        _lines.append(f"**Tools**: {_tool_str}")
                        _lines.append("")
                    if _log.assistant_summary:
                        _lines.append(f"**Assistant**: {_log.assistant_summary}")
                        _lines.append("")
                    _lines.append("---")
                    _lines.append("")
                sections["log_daily"] = f"## 近日会话日志\n" + "\n".join(_lines)
    except Exception:
        pass

    # ── Priority 3: Runtime context (most volatile, dropped first) ──
    if extra_context:
        extra_parts = []
        if extra_context.get("available_skills"):
            extra_parts.append(f"\n## 可用技能\n{extra_context['available_skills']}")
        if extra_context.get("available_tools"):
            extra_parts.append(f"\n## 可用工具\n{extra_context['available_tools']}")
        if extra_context.get("available_mcp_servers"):
            servers = extra_context["available_mcp_servers"]
            extra_parts.append(f"\n## 已配置 MCP 服务器\n当前可用的 MCP 服务器（通过 call_mcp_tool 或 {{server}}__{{tool}} 直接调用）：\n" + "\n".join(f"- {s}" for s in servers))
        if extra_context.get("session_preferences"):
            import json
            extra_parts.append(f"\n## 会话偏好\n{json.dumps(extra_context['session_preferences'], ensure_ascii=False)}")
        if extra_context.get("active_project"):
            extra_parts.append(f"\n## 当前项目\n{extra_context['active_project']}")
        if extra_context.get("attached_files"):
            file_details = []
            for f in extra_context["attached_files"]:
                fname = f.get("file_name", "unknown")
                ftype = f.get("content_type", "")
                fpath = f.get("file_path", "")
                file_details.append(f"  - {fname} ({ftype})")
                if fpath:
                    file_details.append(f"    服务器路径: {fpath}")
            if file_details:
                extra_parts.append("\n## 用户上传的文件\n" + "\n".join(file_details))
        if extra_context.get("conversation_context"):
            extra_parts.append(f"\n## 对话上下文摘要\n{extra_context['conversation_context']}")
        if extra_context.get("auto_memories"):
            extra_parts.append(f"\n{extra_context['auto_memories']}")

        if extra_parts:
            sections["runtime_context"] = "# 运行时上下文" + "\n".join(extra_parts)

    # ── Join with size limits ──
    separator = "\n\n---\n\n"
    joined = _truncate_sections_with_priority(
        sections,
        per_file_max=CONTEXT_FILE_MAX_CHARS,
        total_max=CONTEXT_TOTAL_MAX_CHARS,
        separator=separator,
    )

    # ── Final instructions (always appended, ~600 chars, critical) ──
    prompt = joined + """

---

## 重要指令

1. 你是一个 AI 助手。收到用户消息后，自主判断意图并决定是否调用工具。
2. 如果需要调用工具，使用 function calling 返回工具调用请求。
3. 如果不需要工具，直接生成回复。
4. 每次收到工具结果后，分析结果再决定下一步——继续调工具还是回复用户。
5. 工具调用可以多步：先调工具 A，得到结果后视情况调工具 B 或直接回复。
6. 如果两个工具之间没有依赖关系，在同一轮调用中并行执行。
7. 工具出错时先尝试替代方案，两次失败后如实告知用户。
8. 最终回复用自然语言，结构化呈现（标题、表格、列表）。
9. Always respond in Chinese (中文) unless the user explicitly asks otherwise.
10. 当用户上传了文件时，内容已在"文件内容预览（自动读取）"中提供，可直接引用。如需进一步处理（如图片OCR、表格深度分析），可调用 call_builtin_tool。

### 回复格式强制规则

多条记录用 markdown 表格展示，但必须遵守：
- 列数 ≤ 4，不要在表格中放超过 4 列
- 长文本截断到 30 字以内，用 `...` 结尾
- 用 `###` 标题分隔不同分组
- 不同分组之间用 `---` 分隔

#### Markdown 表格语法规范（必须严格遵守）

表格的每一行必须单独占一行，用换行符 `\n` 分隔。以下示例展示了**正确**和**错误**的写法：

✅ 正确示例：
```markdown
### 动态背光接口 (User D)

| Issue | 标题 | 状态 |
|-------|------|:----:|
| TV-925675 | 动态背光接口需求6... | Open |
| TV-925674 | 动态背光接口需求5... | Open |

### Multiscreen (User E)

| Issue | 标题 | 状态 |
|-------|------|:----:|
| TV-925680 | 需求3：传递设备名称... | To Do |
```

❌ 错误示例（绝对禁止）：
```markdown
| Issue | 标题 | 状态 | |-------|------|:----:| | TV-925675 | 动态背光接口需求6... | Open |
```

**关键规则：**
1. 表头行、分隔行、每一行数据都必须各自独占一行
2. 分隔行必须以 `|` 开头和结尾，如 `|-------|------|------|`
3. 两个表格之间必须至少有一个空行
4. 标题（`###`）与表格之间必须有一个空行
5. 同一行内不要出现多个 `|` 开头的片段连在一起
6. **分隔行格式**：每个列的分隔必须是 `---` 或更多，如 `|-------|------|------|`，不要用 `| -------|------|:----:|`（不要在 `|` 后加空格再跟 `-`）
"""

    # Log prompt size for observability
    total_chars = len(prompt)
    if total_chars > CONTEXT_TOTAL_MAX_CHARS:
        logger = __import__('logging').getLogger(__name__)
        logger.warning("System prompt exceeds %d chars: %d (sections: %s)",
                       CONTEXT_TOTAL_MAX_CHARS, total_chars, list(sections.keys()))

    return prompt
