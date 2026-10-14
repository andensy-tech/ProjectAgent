"""
FocusSet 创建工具 — PM 在 web chat 下发关注指令后，Agent 一键建集合（P-B）。

流程：解析 filter/epic 链接 → 拉取成员（复用 jira_resolve_filter / MCP）→
持久化 FocusSet 主表 → 刷新成员子表（snapshot + 停滞标记）。
LLM 识别到"关注指令"（filter/epic 链接 + 目标 + 截止）时调用本工具即可完成闭环。
"""

import logging
from datetime import date
from typing import Any, Dict

from app.tools.jira_filter_tool import jira_resolve_filter

logger = logging.getLogger(__name__)

_FIELDS = ["key", "summary", "status", "priority", "assignee", "updated", "duedate"]


def _extract_identifier_from_url(identifier: str) -> str:
    """从 Jira URL 中提取标识符（filter id 或 epic key）。"""
    s = identifier.strip()
    if "filter=" in s:
        return s.split("filter=")[-1].split("&")[0].strip().strip("/")
    if "?" in s:
        s = s.split("?")[0]
    s = s.rstrip("/")
    if "/" in s:
        s = s.split("/")[-1]
    return s


async def _fetch_issues_by_jql(user_id: int, jql: str) -> list:
    """通过 MCP search_issues 按 JQL 拉取成员（真实环境主路径，不依赖 REST filter 解析）。

    委托 focus_set_service.fetch_members_by_jql —— 与后台刷新共用同一实现（单一事实来源）。
    """
    from app.services.focus_set_service import fetch_members_by_jql
    return await fetch_members_by_jql(user_id, jql)


async def create_focus_set(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """创建/更新关注集合（PM 关注指令 → 看板状态图）。

    Args:
        params:
          type: "filter" | "epic" | "manual"
          identifier / filter_id: filter 编号或完整链接 / epic key
          name: 集合名（可选，filter 时默认用 filter 名）
          jql: 自定义 JQL（manual 时用；filter 时自动解析）
          deadline: 截止日期 YYYY-MM-DD（可选）
          metric_type / metric_target: 指标与目标（如 fix_rate / 0.8）
          priority_semantics: 优先级语义（如 "本周必清"）
        context: 含 _user_id

    Returns:
        {"success": True, "focus_set_id": ..., "name": ..., "member_count": N, "health": {...}}
        或 {"success": False, "error": ...}
    """
    user_id = context.get("_user_id") if isinstance(context, dict) else None
    if not user_id:
        return {"success": False, "error": "缺少用户身份信息"}

    focus_type = params.get("type", "filter")
    identifier = str(params.get("filter_id", "") or params.get("identifier", "")).strip()
    identifier = _extract_identifier_from_url(identifier)
    if not identifier:
        return {"success": False, "error": "缺少 filter_id 或 identifier"}

    # 多目标支持（review #8）：identifier 可带 `#目标标签` 后缀（如 TV-1#week80），
    # 同一 filter/epic 可建多个不同目标的独立集合（互不 upsert 覆盖）。
    # 全量 identifier 落库参与唯一索引；解析/JQL 只用基础标识。
    from app.services.focus_set_service import _split_goal_tag
    base_identifier, goal_tag = _split_goal_tag(identifier)

    # 1. 获取成员（优先 JQL 直传——真实环境主路径；filter_id 仅作标识，REST 解析可能不可用）
    jql = params.get("jql", "")
    name = params.get("name", "")
    issues: list = []

    if jql:
        # 直接使用提供的 JQL（Agent 已通过 MCP 查询确定成员范围）
        issues = await _fetch_issues_by_jql(user_id, jql)
    elif focus_type == "filter":
        # 通过 REST 解析 filter 元信息；配置错误/查询失败直接透传（提醒用户修正配置，不做代码规避）
        resolved = await jira_resolve_filter({"filter_id": base_identifier}, context)
        if not resolved.get("success"):
            return resolved
        name = name or resolved.get("name", f"filter-{base_identifier}")
        jql = resolved.get("jql", f"filter = {base_identifier}")
        issues = resolved.get("issues", [])
    elif focus_type == "epic":
        # epic 类型自动生成 JQL："Epic Link" = KEY（#goal 后缀由 _build_epic_jql 内部剥掉）
        from app.services.focus_set_service import _build_epic_jql
        jql = _build_epic_jql(identifier)
        issues = await _fetch_issues_by_jql(user_id, jql)
    elif focus_type == "manual":
        issues = await _fetch_issues_by_jql(user_id, jql or "")

    if not name:
        name = f"{focus_type}-{base_identifier}" + (f"·{goal_tag}" if goal_tag else "")

    # 2. deadline 解析（支持 YYYY-MM-DD）
    deadline = None
    raw_deadline = params.get("deadline")
    if raw_deadline:
        try:
            deadline = date.fromisoformat(str(raw_deadline).strip())
        except ValueError:
            return {"success": False, "error": f"deadline 格式应为 YYYY-MM-DD，收到: {raw_deadline}"}

    # 3. 持久化 FocusSet + 刷新成员
    from app.services.focus_set_service import (
        create_or_update_focus_set,
        list_focus_sets,
        refresh_focus_set_members,
        update_weekly_baseline,
    )

    fs = await create_or_update_focus_set(
        user_id=user_id,
        focus_type=focus_type,
        identifier=identifier,
        name=name,
        jql=jql or None,
        deadline=deadline,
        metric_type=params.get("metric_type"),
        metric_target=params.get("metric_target"),
        priority_semantics=params.get("priority_semantics"),
        description=params.get("description"),
    )
    if issues:
        await refresh_focus_set_members(fs.id, issues, user_id=user_id)

    # 4. epic 类型立即固化/更新本周基线（周一重置，无基线时建立）
    if focus_type == "epic":
        await update_weekly_baseline(fs.id, issues)

    # 5. 返回健康度概览
    sets = await list_focus_sets(user_id)
    health = next((s["health"] for s in sets if s["id"] == fs.id), None)

    return {
        "success": True,
        "focus_set_id": fs.id,
        "name": name,
        "identifier": identifier,
        "type": focus_type,
        "member_count": len(issues),
        "deadline": deadline.isoformat() if deadline else None,
        "metric": {"type": params.get("metric_type"), "target": params.get("metric_target")}
        if params.get("metric_type") else None,
        "health": health,
        "message": f"已记录关注集合「{name}」",
    }
