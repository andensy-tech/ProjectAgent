"""
项目进展判定工具 — Agent 实时判断"当前到哪、下个里程碑、问题相对计划的偏差"（P-E）。

结合 项目计划知识库（project_plans，时间基准）+ 关注集合（focus_sets，优先级基准）
输出结构化进展信息，供 LLM 判断问题紧迫度/是否预警/建议动作。
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def judge_progress(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """判断项目进展：当前阶段 / 下个里程碑 / 关注集合状态。

    Args:
        params: {"project_name": 可选，默认第一个有计划的项目}
        context: 含 _user_id

    Returns:
        {"success": True, "data": {"plan": {...}, "focus_sets": [...], "next_milestone": {...}}}
    """
    user_id = context.get("_user_id") if isinstance(context, dict) else None
    if not user_id:
        return {"success": False, "error": "缺少用户身份信息"}

    from app.services.focus_set_service import list_focus_sets
    from app.services.project_plan_service import get_project_plan, list_project_plans

    project_name = (params.get("project_name") or "").strip()

    # 1. 项目计划（时间基准）
    plan_info = None
    try:
        plans = await list_project_plans(user_id)
        if project_name:
            plan_info = await get_project_plan(user_id, project_name)
        elif plans:
            plan_info = await get_project_plan(user_id, plans[0]["project_name"])
    except Exception as e:
        logger.warning("judge_progress: load plan failed: %s", e)

    # 2. 关注集合（优先级基准）
    focus_sets = []
    try:
        focus_sets = await list_focus_sets(user_id)
    except Exception as e:
        logger.warning("judge_progress: load focus_sets failed: %s", e)

    # 3. 计算下个里程碑（最近的未完成阶段）
    next_milestone = None
    if plan_info:
        from datetime import date
        today = date.today()
        upcoming = [
            p for p in plan_info.get("phases", [])
            if p.get("target_date") and p.get("status") != "done"
        ]
        upcoming.sort(key=lambda p: p["target_date"])
        for p in upcoming:
            if p["target_date"] >= today.isoformat():
                next_milestone = p
                break
        if next_milestone is None and upcoming:
            next_milestone = upcoming[0]  # 最近的（可能已过期）

    return {
        "success": True,
        "data": {
            "project": plan_info["project_name"] if plan_info else project_name,
            "overall_status": plan_info.get("overall_status") if plan_info else None,
            "plan": plan_info,
            "next_milestone": next_milestone,
            "focus_sets": focus_sets,
            "summary": _summarize(plan_info, next_milestone, focus_sets),
        },
    }


def _summarize(plan_info, next_milestone, focus_sets) -> str:
    """生成一段简明进展摘要（供 LLM 快速理解）。"""
    parts = []
    if plan_info and plan_info.get("overall_status"):
        parts.append(f"项目整体进度: {plan_info['overall_status']}")
    if next_milestone:
        parts.append(
            f"下个里程碑: {next_milestone['phase_name']} "
            f"(目标 {next_milestone['target_date']}, 状态 {next_milestone['status']})"
        )
    urgent_focus = [f for f in focus_sets if f.get("health", {}).get("status") in ("red", "yellow")]
    if urgent_focus:
        for f in urgent_focus[:5]:
            h = f["health"]
            parts.append(
                f"关注集合「{f['name']}」: {h['done']}/{h['total']} 达成"
                f"{', 距截止 ' + str(h['days_left']) + ' 天' if h['days_left'] is not None else ''}"
                f"{'，停滞 ' + str(len(h['stagnant_members'])) + ' 个' if h['stagnant_members'] else ''}"
            )
    if not parts:
        return "暂无项目计划或关注集合信息"
    return "；".join(parts)
