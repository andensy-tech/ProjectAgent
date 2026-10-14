"""
风险精判工具 — LLM 对高紧迫度 issue 做深度分析（P-G：monitoring_optimization）。

规则引擎先筛出高紧迫度（🔴🟠）issue，本工具再对它们跑 LLM 精判：
结合成员状态/停滞/优先级，判断"真超时 or 有合理解释"，输出结构化风险分析
（问题/原因/建议/责任人）+ 智能摘要。消除误报，省 token（只对初筛跑 LLM）。
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_RISK_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "risk_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string"},
                    "is_overdue": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "suggestion": {"type": "string"},
                    "owner": {"type": "string"},
                },
                "required": ["issue_key", "is_overdue", "reason", "suggestion", "owner"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["risk_items", "summary"],
}

_SYSTEM_PROMPT = (
    "你是项目经理的风险分析助手。基于给定的 Jira 问题列表（含状态/优先级/停滞情况），"
    "逐条判断每个问题是否真正超期/有风险（结合优先级和停滞：P0/P1 且停滞 → 大概率真风险），"
    "给出根因推测、可执行的推动建议和责任人建议。输出 JSON。"
)


async def _load_issues(params: Dict[str, Any], user_id: int) -> List[Dict[str, Any]]:
    """从关注集合成员或指定 issue_keys 获取 issue 数据。"""
    focus_set_id = params.get("focus_set_id")
    if focus_set_id:
        from app.services.focus_set_service import get_focus_set_members
        members = await get_focus_set_members(int(focus_set_id))
        # 只分析高紧迫度成员（停滞 或 高优先级）
        return [m for m in members
                if m.get("stagnant") or (m.get("priority") or "").upper() in ("P0", "P1", "BLOCKER", "CRITICAL")]

    issue_keys = params.get("issue_keys") or []
    if issue_keys:
        from app.mcp.capability import capability
        fields = ["key", "summary", "status", "priority", "assignee", "updated", "duedate"]
        resp = await capability.call("jira", "search_issues", {
            "jql": f"key in ({','.join(issue_keys)})", "maxResults": 50, "fields": fields,
        }, user_id, timeout=30)
        issues = []
        if isinstance(resp, dict):
            for iss in resp.get("issues", []):
                if not isinstance(iss, dict):
                    continue
                f = iss.get("fields", {}) or {}
                issues.append({
                    "key": iss.get("key", ""),
                    "summary": f.get("summary", ""),
                    "status": (f.get("status") or {}).get("name", "") if isinstance(f.get("status"), dict) else (f.get("status") or ""),
                    "priority": (f.get("priority") or {}).get("name", "") if isinstance(f.get("priority"), dict) else (f.get("priority") or ""),
                    "updated": f.get("updated", ""),
                    "stagnant": False,
                })
        return issues
    return []


async def risk_analyze(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """对高紧迫度 issue 跑 LLM 风险精判。

    Args:
        params: {"focus_set_id": 关注集合 id} 或 {"issue_keys": ["TV-1", ...]}
        context: 含 _user_id

    Returns:
        {"success": True, "data": {"risk_items": [...], "summary": "..."}}
    """
    user_id = context.get("_user_id") if isinstance(context, dict) else None
    if not user_id:
        return {"success": False, "error": "缺少用户身份信息"}

    issues = await _load_issues(params, user_id)
    if not issues:
        return {"success": False, "error": "没有可分析的高紧迫度问题"}

    # 构造 prompt
    lines = []
    for i, iss in enumerate(issues, 1):
        stagnant = "停滞" if iss.get("stagnant") else "正常更新"
        lines.append(
            f"{i}. [{iss.get('key', '')}] {iss.get('summary', '')} | "
            f"状态={iss.get('status', '')} 优先级={iss.get('priority', '')} {stagnant}"
        )
    user_prompt = f"请分析以下 {len(issues)} 个 Jira 问题：\n" + "\n".join(lines)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    from app.services.llm.router import TaskType, llm_router
    try:
        result = await llm_router.route_and_complete_structured(
            TaskType.RISK_ANALYSIS, messages, schema=_RISK_SCHEMA,
        )
    except Exception as e:
        logger.warning("risk_analyze LLM call failed: %s", e)
        return {"success": False, "error": f"风险分析失败: {str(e)[:120]}"}

    if not isinstance(result, dict):
        return {"success": False, "error": "LLM 返回格式异常"}

    return {"success": True, "data": result}
