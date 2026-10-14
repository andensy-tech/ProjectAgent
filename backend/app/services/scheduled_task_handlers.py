"""声明式规则 handler — scheduled task 第一个任务类型（2026-09-09）。

把"扫描 → 条件 → 通知"类简单定时任务声明为 JSON 配置，无需写代码即可创建新任务
（同类任务换个 filter/天数/截止日期 = 新 config）。引擎（scheduled_task_service）
与任务类型无关，新任务类型 = 在本模块或新模块 register_handler。

config schema：
{
  "source":     {"kind": "jira_jql", "jql": "filter = 61364"},
  "conditions": [
      {"kind": "status_not_in", "statuses": ["Verified", "Closed"]},
      {"kind": "stale_days", "days": 3, "basis": "last_comment" | "updated"}
  ],
  "action":     {"kind": "jira_comment", "template": "...{key}...",
                  "remind_interval_days": 3}
}

模板变量：{key} {summary} {owner}（[~username]，非 username 形态自动省略）{stale_days}。
去重：task.state[issue_key] 记上次提醒日期，距今 < remind_interval_days 则跳过。
"""

import asyncio
import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional

from app.models.scheduled_task import ScheduledTask

logger = logging.getLogger(__name__)

_CONDITION_KINDS = ("status_not_in", "stale_days")
_STALE_BASES = ("last_comment", "updated")
_MAX_STALE_DAYS = 90  # 停滞阈值上限（防 LLM 写 36500 之类）
_MAX_NUDGE_PER_RUN = 50  # 单轮最多 comment 数（防失控刷屏）


def _validate(config: Dict[str, Any]) -> Optional[str]:
    """校验声明式 config。返回中文错误或 None。"""
    source = config.get("source")
    if not isinstance(source, dict) or source.get("kind") != "jira_jql" or not str(source.get("jql", "")).strip():
        return "config.source 必须是 {\"kind\": \"jira_jql\", \"jql\": \"...\"}"

    conditions = config.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        return "config.conditions 必须是非空数组"
    for c in conditions:
        if not isinstance(c, dict) or c.get("kind") not in _CONDITION_KINDS:
            return f"config.conditions 含未知条件类型: {c.get('kind') if isinstance(c, dict) else c}（支持 {_CONDITION_KINDS}）"
        if c["kind"] == "status_not_in" and not isinstance(c.get("statuses"), list):
            return "status_not_in 条件需要 statuses 数组"
        if c["kind"] == "stale_days":
            days = c.get("days")
            if not isinstance(days, int) or not (1 <= days <= _MAX_STALE_DAYS):
                return f"stale_days 条件的 days 必须是 1~{_MAX_STALE_DAYS} 的整数"
            if c.get("basis", "last_comment") not in _STALE_BASES:
                return f"stale_days 条件的 basis 必须是 {_STALE_BASES}"

    action = config.get("action")
    if not isinstance(action, dict) or action.get("kind") != "jira_comment":
        return "config.action 必须是 {\"kind\": \"jira_comment\", \"template\": \"...\"}"
    if not str(action.get("template", "")).strip():
        return "config.action.template 不能为空"
    return None


async def _run(task: ScheduledTask, user_id: int) -> Dict[str, Any]:
    """执行声明式规则：拉成员 → 条件过滤 → add_comment（去重）。"""
    from app.services.focus_set_service import (
        _fetch_last_comment_time,
        _parse_updated_date,
        _today,
        fetch_members_with_meta,
    )
    from app.services.cl_scan_service import extract_username as _extract_username
    from app.services.scheduled_task_service import update_task_state
    from app.mcp.capability import capability

    config = task.config or {}
    source = config["source"]
    conditions = config["conditions"]
    action = config["action"]
    today = _today()
    remind_interval = int(action.get("remind_interval_days") or 3)

    # 1) 拉成员（复用 FocusSet 单一事实来源；截断不静默记 warning）
    members, meta = await fetch_members_with_meta(user_id, str(source["jql"]).strip())
    if meta.get("truncated"):
        logger.warning(
            "scheduled task %s 成员截断: fetched=%s < total=%s",
            task.id, meta.get("fetched"), meta.get("total"),
        )

    # 2) 预取 last_comment 基准（仅声明了 last_comment 条件的停滞候选）
    stale_cond = next((c for c in conditions if c["kind"] == "stale_days"), None)
    need_comment_basis = bool(
        stale_cond and stale_cond.get("basis", "last_comment") == "last_comment"
    )
    comment_times: Dict[str, str] = {}
    if need_comment_basis and members:
        threshold = int(stale_cond.get("days", 7))
        candidates = [
            m for m in members
            if (today - _parse_updated_date(m.get("updated", ""))).days > threshold
        ]
        sem = asyncio.Semaphore(8)

        async def _fetch(key: str) -> None:
            async with sem:
                try:
                    ts = await _fetch_last_comment_time(user_id, key)
                    if ts:
                        comment_times[key] = ts
                except Exception:  # noqa: BLE001 — 评论拉取失败降级用 updated
                    pass

        await asyncio.gather(*(_fetch(m["key"]) for m in candidates))

    # 3) 条件过滤
    def _stale_days_of(m: Dict[str, Any]) -> int:
        basis_ts = comment_times.get(m["key"]) or m.get("updated", "")
        return (today - _parse_updated_date(basis_ts)).days if basis_ts else 0

    matched: List[Dict[str, Any]] = []
    for m in members:
        ok = True
        for c in conditions:
            if c["kind"] == "status_not_in":
                if (m.get("status") or "") in set(c.get("statuses") or []):
                    ok = False
                    break
            elif c["kind"] == "stale_days":
                if _stale_days_of(m) < int(c.get("days", 7)):
                    ok = False
                    break
        if ok:
            matched.append(m)

    # 4) 去重 + add_comment
    state = dict(task.state or {})
    nudged, skipped = 0, 0
    for m in matched[:_MAX_NUDGE_PER_RUN]:
        key = m.get("key", "")
        last_remind = state.get(key)
        if last_remind and (today - date.fromisoformat(last_remind)).days < remind_interval:
            skipped += 1
            continue
        owner = _extract_username(str(m.get("assignee") or ""))
        text = str(action["template"])
        for var, val in (
            ("{key}", key),
            ("{summary}", str(m.get("summary") or "")),
            ("{owner}", owner),
            ("{stale_days}", str(_stale_days_of(m))),
        ):
            text = text.replace(var, val)
        if not owner:
            text = text.replace("[~]", "")  # owner 非 username 形态 → 去掉空 mention
        try:
            await capability.call(
                "jira", "add_comment",
                {"issue_key": key, "comment": text},
                user_id, timeout=30,
            )
            state[key] = today.isoformat()
            nudged += 1
        except Exception as e:  # noqa: BLE001 — 单 issue 失败不中断整轮
            logger.warning("scheduled task %s add_comment(%s) failed: %s", task.id, key, e)

    if state != (task.state or {}):
        await update_task_state(task.id, state)

    return {"matched": len(matched), "nudged": nudged, "skipped_dedup": skipped}


# ── 自注册（service import 本模块即生效）─────────────────────────────────────

def _register() -> None:
    from app.services.scheduled_task_service import register_handler
    register_handler("declarative_rule", _validate, _run)


_register()
