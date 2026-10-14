"""
关注集合后台刷新 + 主动提醒服务（P0-1/P0-2，monitoring_optimization 落地增量）。

P0-1 数据保鲜：后台定时对每用户 active focus_sets 按 jql 重拉成员并刷新快照，
  保持看板健康度新鲜 —— 否则成员停滞标记/状态分布永远是创建那一刻的（"重点截屏"）。

P0-2 主动提醒 PM：刷新后扫描集合 ——
  - 到期未达标（距截止 ≤ _DUE_DAYS 且 fix rate 未达标）→ 站内消息（铃铛红点）提醒 PM；
  - 已达标 → 主动提示一次。
  去重：overdue_date 按日（同一集合同日只提醒一次）、achieved 标记（达标只提示一次，
  重新变未达标后重置，便于再次达标可再提示）。
"""

import logging
from typing import Any, Dict, List

from app.services.focus_set_service import (
    _build_epic_jql,
    _split_goal_tag,
    _today,
    fetch_members_with_meta,
    get_focus_set_notify_state,
    list_active_focus_sets,
    list_focus_sets,
    refresh_focus_set_members,
    save_focus_set_notify_state,
    update_weekly_baseline,
)

logger = logging.getLogger(__name__)

# 距截止 ≤ _DUE_DAYS 视为临期，触发提醒 PM
_DUE_DAYS = 3


async def run_focus_set_cycle(user_id: int) -> Dict[str, Any]:
    """一轮关注集合维护：刷新成员快照 → 扫描到期/达标提醒 PM。

    Args:
        user_id: 用户 ID（后台任务按用户并发执行）

    Returns:
        {"refresh": {"total", "refreshed", "failed"}, "notified": [...]}
    """
    stats: Dict[str, Any] = {"refresh": {}, "notified": []}
    try:
        stats["refresh"] = await _refresh_focus_sets(user_id)
        if stats["refresh"].get("total", 0) > 0:
            stats["notified"] = await _scan_and_notify(user_id)
    except Exception as e:
        logger.error("focus_set cycle failed for user %s: %s", user_id, e, exc_info=True)
    return stats


async def _refresh_focus_sets(user_id: int) -> Dict[str, int]:
    """刷新该用户所有 active focus_sets 的成员快照（替换式）。

    对 epic 类型自动构造 JQL；每次刷新前更新周基线（周一重置 / 无基线时建立）。
    单个集合刷新失败不中断整轮（MCP 不可达等网络问题跳过，下轮重试）。
    """
    sets = await list_active_focus_sets(user_id)
    stats = {"total": len(sets), "refreshed": 0, "failed": 0}
    for fs in sets:
        jql = fs.get("jql")
        if not jql:
            if fs.get("type") == "filter":
                # identifier 可能带 #goal 目标后缀（多目标集合），JQL 用基础 id
                base_id, _tag = _split_goal_tag(fs["identifier"])
                jql = f"filter = {base_id}"
            elif fs.get("type") == "epic":
                jql = _build_epic_jql(fs["identifier"])
        if not jql:
            continue  # manual 集合无 jql，跳过刷新
        try:
            issues, meta = await fetch_members_with_meta(user_id, jql)
            # review #1：截断不静默——记 stats 由调用方/日志感知（fix rate 分母可能偏小）
            if meta.get("truncated"):
                logger.warning(
                    "focus_set %s (%s) 成员截断：fetched=%d < total=%d，健康度可能不完整",
                    fs.get("id"), fs.get("name"), meta.get("fetched"), meta.get("total"),
                )
                stats["truncated"] = stats.get("truncated", 0) + 1
            # epic 类型先固化/更新周基线，再替换成员快照
            if fs.get("type") == "epic":
                await update_weekly_baseline(fs["id"], issues)
            if issues:
                await refresh_focus_set_members(fs["id"], issues, user_id=user_id)
            stats["refreshed"] += 1
        except Exception as e:
            logger.warning("refresh focus_set %s (%s) failed: %s", fs.get("id"), fs.get("name"), e)
            stats["failed"] += 1
    return stats


async def _scan_and_notify(user_id: int) -> List[Dict[str, Any]]:
    """扫描 active focus_sets：到期未达标提醒 PM、达标主动提示。按日/状态去重。

    Returns:
        [{"focus_set_id", "type": achieved|due|overdue, "days_left"?: int}, ...]
    """
    from app.services.notification_service import create_notification

    sets = await list_focus_sets(user_id)
    state = await get_focus_set_notify_state(user_id)
    today = _today()
    notified: List[Dict[str, Any]] = []
    dirty = False

    for fs in sets:
        fsid = str(fs["id"])
        h = fs["health"]
        metric = fs.get("metric") or {}
        target = metric.get("target")
        progress = h.get("progress", 0.0)
        days_left = h.get("days_left")
        # epic 周基线口径：有基线总数时用基线，否则用当前总数
        denominator = h.get("baseline_total") if h.get("baseline_total") is not None else h.get("total", 0)
        done = h.get("done", 0)
        entry = dict(state.get(fsid) or {})

        achieved = target is not None and denominator > 0 and progress >= target

        if achieved:
            # 达标 → 主动提示一次（achieved 标记）
            if not entry.get("achieved"):
                await create_notification(
                    user_id,
                    title=f"✅ 关注集合「{fs['name']}」已达标",
                    content=(f"{done}/{denominator} 达成（目标 {round(target * 100)}%，"
                             f"当前 {round(progress * 100)}%）"),
                    notif_type="focus_set", link="/dashboard",
                )
                entry["achieved"] = True
                entry["overdue_date"] = None  # 达标后清除到期标记
                dirty = True
                notified.append({"focus_set_id": fsid, "type": "achieved"})
        else:
            # 重新变未达标 → 重置 achieved（下次再达标可再次提示）
            if entry.get("achieved"):
                entry["achieved"] = False
                dirty = True
            # 到期未达标 → 提醒 PM（同日不重复）
            due = days_left is not None and days_left <= _DUE_DAYS
            if due and entry.get("overdue_date") != today.isoformat():
                overdue = days_left < 0
                await create_notification(
                    user_id,
                    title=(f"⏰ 关注集合「{fs['name']}」"
                           + ("已超期" if overdue else f"距截止 {days_left} 天")),
                    content=_build_due_content(fs),
                    notif_type="focus_set", link="/dashboard",
                )
                entry["overdue_date"] = today.isoformat()
                dirty = True
                notified.append({
                    "focus_set_id": fsid,
                    "type": "overdue" if overdue else "due",
                    "days_left": days_left,
                })

        state[fsid] = entry

    if dirty:
        await save_focus_set_notify_state(user_id, state)
    return notified


def _build_due_content(fs: Dict[str, Any]) -> str:
    """生成到期未达标提醒内容（含差几个达标 + 停滞成员摘要，供 PM 快速定位）。"""
    h = fs["health"]
    denominator = h.get("baseline_total") if h.get("baseline_total") is not None else h.get("total", 0)
    parts = [f"当前 {h['done']}/{denominator} 达成（{round(h['progress'] * 100)}%）"]
    if h.get("baseline_total") is not None:
        parts.append(f"本周起始 {h['baseline_total']} 个")
    diff = h.get("diff_to_target")
    if diff is not None and diff > 0:
        parts.append(f"还差 {diff} 个达标")
    done_delta = h.get("done_delta")
    if done_delta is not None and done_delta != 0:
        parts.append(f"今日新增完成 {done_delta} 个" if done_delta > 0 else f"今日回退 {abs(done_delta)} 个")
    stagnant = h.get("stagnant_members") or []
    if stagnant:
        keys = "、".join(m["key"] for m in stagnant[:5])
        parts.append(f"停滞 {len(stagnant)} 个（{keys}）")
    parts.append("请到看板「我的关注指令」查看详情")
    return "；".join(parts) + "。"
