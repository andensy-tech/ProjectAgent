"""CL 监控 V2 每日扫描 — 状态 diff + 焦点 CL 确认 + 提醒编排。

设计（2026-09-16 定稿，见 docs/design/cl_monitoring_v2.md）：

    ① 取当前集合   原样执行配置的 JQL（代码不追加任何状态条件）
    ② 与队列 diff  新题入队；状态变化写 prev_status；首轮历史 Closed 归档
    ③ 消失即复查   队列中不在 JQL 结果的题逐个 get_issue 确认新状态
    ④ CL 确认      只对焦点题（Resolved/Verified/Closed）拉评论查 Gerrit
    ⑤ 提醒         Resolved/Verified 每天 1 次 @owner；Closed 只进 PM summary
    ⑥ 刷新缓存     用队列重建页面快照

成本控制的关键：**焦点过滤发生在第 ④ 步之前** —— 每天只对在途的焦点题查 CL，
而不是 JQL 命中的全部题。`issue.updated` 未变的题复用上次 CL 结果，省掉重拉。
"""

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.cl_jira_state import ClJiraState
from app.services import cl_queue_service as queue
from app.services.cl_queue_service import CLOSED_STATUSES, FOCUS_STATUSES, ensure_aware
from app.services.gerrit_service import get_cl_status_for_issues, is_merged

logger = logging.getLogger(__name__)

# Jira search_issues 必须带非 key 字段：实测 fields=["key"] 会让 MCP 报
# 'Issue' object has no attribute 'fields'（'Issue' object is not subscriptable）
_SEARCH_FIELDS = ["key", "status", "summary", "assignee", "updated"]

# MCP 调用超时
_MCP_TIMEOUT = 60

# 提醒间隔：同一题每天最多提醒一次
REMIND_INTERVAL_SECONDS = 24 * 3600

# 本轮已加载的"整条 Jira 标不关注"集合（由 run_daily_scan 在加载时填充）。
# 值由 settings 里的 {"instance": "_jira", "change_id": "_all", ...} 派生；
# needs_reminder 据此跳过这些题，PM 可在页面随时恢复。
_jira_suppressed: Set[str] = set()

# get_issue 连续失败多少轮后把该题归档（Jira 被删除/无权限）
_MAX_FAIL_COUNT = 3


def _now() -> datetime:
    """当前 UTC 时间。"""
    return datetime.now(timezone.utc)


# ── Jira issue 字段解析 ──────────────────────────────────────────────────────


def parse_issue_fields(issue: Dict[str, Any]) -> Dict[str, str]:
    """从 Jira issue 字典提取 key/status/summary/assignee/updated。

    MCP 返回的 status/assignee 可能是 dict（name/displayName）或纯字符串。

    Args:
        issue: MCP `search_issues`/`get_issue` 返回的 issue 字典。

    Returns:
        {"key","status","summary","assignee","updated"}，缺失字段为空串。
    """
    fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
    if not isinstance(fields, dict):
        fields = {}

    def _name(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("name") or value.get("displayName") or "")
        return str(value or "")

    return {
        "key": str(issue.get("key", "") or ""),
        "status": _name(fields.get("status")),
        "summary": str(fields.get("summary") or ""),
        "assignee": _name(fields.get("assignee")),
        "updated": str(fields.get("updated") or ""),
    }


def is_focus_status(status: str) -> bool:
    """该状态是否需要重点监控（发 Jira 评论）。"""
    return status in FOCUS_STATUSES


def is_closed_status(status: str) -> bool:
    """该状态是否视为已闭环。"""
    return status in CLOSED_STATUSES


# ── 单题状态推进 ─────────────────────────────────────────────────────────────


def apply_seen(state: ClJiraState, info: Dict[str, str], *,
               first_scan: bool, now: datetime) -> str:
    """把本轮看到的 Jira 状态写入队列记录，返回处置结论。

    Args:
        state: 队列记录（就地修改，调用方负责 commit）。
        info: parse_issue_fields 的结果。
        first_scan: 这是该题的首次入队（无历史状态可比）。
        now: 当前时间。

    Returns:
        "new"       首次入队
        "changed"   状态发生变化
        "unchanged" 状态未变
    """
    status = info.get("status", "")
    state.summary = info.get("summary", "") or state.summary
    state.assignee = info.get("assignee", "") or state.assignee
    state.last_seen_at = now
    if info.get("updated"):
        state.issue_updated = info["updated"]

    if first_scan or not state.status:
        state.status = status
        state.prev_status = ""
        state.status_changed_at = now
        state.queued = True
        # 首轮已是 Closed 的历史记录：跳过 CL 确认（大概率早已闭环），
        # 只归档不打扰。若之后状态再变化会被唤醒。
        state.queue_reason = "initial"
        state.archived = is_closed_status(status)
        return "new"

    if status == state.status:
        return "unchanged"

    state.prev_status = state.status
    state.status = status
    state.status_changed_at = now
    state.queue_reason = "transition"
    state.fail_count = 0
    # 状态变了说明"已无待办"的结论不再成立 -> 清除 ack，重新开始提醒
    state.ack_at = None
    state.ack_reason = ""
    if state.archived:
        state.archived = False  # 归档题重新活跃 -> 唤醒并补做 CL 确认
    return "changed"


def should_refresh_cl(state: ClJiraState, outcome: str, info: Dict[str, str],
                      prev_updated: str = "") -> bool:
    """判断该题本轮是否需要重新做 CL 确认。

    两道省流：①只对**焦点状态**（Resolved/Verified/Closed）查 CL —— 题还在
    Open/In Progress 时根本没有待验证的改动，查了也是白查（实测某项目首次
    入队 155 个题里只有 43 个是焦点，能省掉七成 MCP 调用）；②状态没变且
    Jira 的 `updated` 也没变的题，复用上一次的 CL 结果。

    Args:
        prev_updated: **本轮之前**记录的 issue.updated。必须由调用方在
            `apply_seen` 之前取好——apply_seen 会把 state.issue_updated 覆盖成
            新值，再拿它比较就永远相等了（实测 TV-953261：CL 已合并且有新评论，
            但因为状态没变 + 这个比较失效，CL 数据一直停在旧结果上）。
    """
    if state.archived or not state.queued:
        return False
    if not (is_focus_status(state.status) or is_closed_status(state.status)):
        return False
    if outcome in ("new", "changed"):
        return True
    if state.cl_checked_at is None:
        return True
    return bool(info.get("updated")) and info["updated"] != prev_updated


def apply_ack(state: ClJiraState, reason: str, now: datetime) -> None:
    """标记"经办人已说明该题 CL 处理完毕"：停止提醒，但保留在队列供 PM 复核。"""
    state.ack_at = now
    state.ack_reason = reason[:500]


def clear_ack(state: ClJiraState) -> None:
    """清除 ack（状态再变化、或经办人新回复推翻了旧结论时重新武装提醒）。"""
    state.ack_at = None
    state.ack_reason = ""


def needs_cl_action(state: ClJiraState) -> bool:
    """该题是否还有 CL 侧待办：有未合并 CL，或压根没登记 CL。

    注意：这是**数据侧**判断；经办人已用评论说明"CL 都处理完了"时由 `ack_at`
    单独拦截提醒（见 `needs_reminder`），不改这里的计数。
    """
    return state.cl_unmerged > 0 or state.cl_noregister


def _recount_cl(state: ClJiraState, items: List[Dict[str, Any]]) -> None:
    """按给定 CL 明细重算 cl_* 计数字段。"""
    merged = sum(1 for it in items if is_merged(it.get("status")))
    state.cl_items_json = json.dumps(items, ensure_ascii=False)
    state.cl_total = len(items)
    state.cl_merged = merged
    state.cl_unmerged = len(items) - merged


def apply_cl_result(state: ClJiraState, group: Optional[Dict[str, Any]],
                    now: datetime,
                    suppressed_keys: Optional[Set[Tuple[str, str, str]]] = None) -> None:
    """把 CL 确认结果写入队列记录。

    Args:
        state: 队列记录。
        group: get_cl_status_for_issues 返回的对应 jira_group；None 表示无 CL。
        now: 当前时间。
        suppressed_keys: 用户标记"不关注"的 (instance, change_id, jira_key) 集合，
            命中的 CL 不参与计数，避免已确认无需关注的 CL 继续触发提醒。
    """
    state.cl_checked_at = now
    if not group:
        state.cl_total = 0
        state.cl_merged = 0
        state.cl_unmerged = 0
        state.cl_noregister = True
        state.cl_items_json = "[]"
        return

    items = _filter_suppressed(group.get("items", []) or [], state.issue_key, suppressed_keys)
    _recount_cl(state, items)
    # 全部 CL 都被标记不关注时不算"未登记"（那是明确无需关注，不是漏提）
    state.cl_noregister = len(items) == 0 and not (group.get("items") or [])


def _filter_suppressed(items: List[Dict[str, Any]], issue_key: str,
                       suppressed_keys: Optional[Set[Tuple[str, str, str]]]) -> List[Dict[str, Any]]:
    """剔除用户标记"不关注"的 CL。"""
    if not suppressed_keys:
        return list(items)
    return [
        it for it in items
        if (it.get("instance"), it.get("change_id"), issue_key) not in suppressed_keys
    ]


def apply_suppression(state: ClJiraState,
                      suppressed_keys: Optional[Set[Tuple[str, str, str]]]) -> None:
    """按最新的"不关注"标记重新过滤已有 CL 明细（就地修改）。"""
    if not suppressed_keys or not state.cl_items_json:
        return
    try:
        items = json.loads(state.cl_items_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return
    kept = _filter_suppressed(items, state.issue_key, suppressed_keys)
    if len(kept) == len(items):
        return
    _recount_cl(state, kept)


def should_exit_queue(state: ClJiraState) -> bool:
    """是否满足退出队列条件：已闭环 且 CL 侧无待办。"""
    return is_closed_status(state.status) and not needs_cl_action(state)


def needs_reminder(state: ClJiraState, now: datetime,
                   interval_seconds: int = REMIND_INTERVAL_SECONDS) -> Optional[str]:
    """判断该题此刻是否需要提醒 owner，返回提醒状态；不需要则 None。

    规则：
      Resolved                      -> 提醒走验证流程
      Verified + 未合并/未登记 CL   -> 提醒提交并合并 CL
      Verified + CL 侧无待办        -> 不提醒（无待办），仅跟踪到 Closed
      Closed                        -> 不提醒 owner（只进 PM summary）
      任意状态 + 经办人已说明(ack)   -> 不提醒（保留在 PM summary 供复核）
      任意状态 + 整条被标不关注       -> 不提醒（PM 在页面主动停止该题的一切动作）
    """
    if not state.queued or state.archived:
        return None
    if state.ack_at is not None:
        return None  # 经办人已在评论里说明 CL 处理完毕
    if state.issue_key in _jira_suppressed:
        return None  # 整条 Jira 被标不关注
    if not is_focus_status(state.status):
        return None
    if state.status == "Verified" and not needs_cl_action(state):
        return None
    last = ensure_aware(state.last_reminded_at)
    if last is not None and (now - last) < timedelta(seconds=interval_seconds):
        return None
    return state.status


# ── 提醒文案与发送 ───────────────────────────────────────────────────────────


def extract_username(assignee: str) -> str:
    """Jira @ 用 [~username]。非 username 形态（如中文 displayName）返回 ''。"""
    a = (assignee or "").strip()
    return a if re.fullmatch(r"[A-Za-z0-9_.\-]+", a) else ""


def format_unmerged_cls(state: ClJiraState) -> str:
    """列出该题未合并的 CL（提醒文案附件）。"""
    try:
        items = json.loads(state.cl_items_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return ""
    lines = [
        f"- {it.get('instance', '')} {it.get('change_id', '')}"
        f" [{it.get('status', '')}] {it.get('subject', '')}"
        for it in items if not is_merged(it.get("status"))
    ]
    return "\n".join(lines)


def build_reminder_text(state: ClJiraState, username: str = "") -> str:
    """按状态与 CL 情况生成提醒文案。"""
    mention = f"[~{username}] " if username else ""
    if state.status == "Resolved":
        text = (f"{mention}{state.issue_key} 已处于 Resolved 状态，"
                f"请及时走验证流程。")
    elif state.cl_noregister:
        text = (f"{mention}{state.issue_key} 已处于 Verified 状态，"
                f"但未在 Jira 中登记关联 CL。如有代码改动需要合并，"
                f"请登记 CL 链接并及时合并。")
    else:
        text = (f"{mention}{state.issue_key} 已处于 Verified 状态，"
                f"请确保 CL 已经提交并最终合并。")
    cl_list = format_unmerged_cls(state)
    if cl_list:
        text += f"\n\n未合并 CL:\n{cl_list}"
    return text


async def fetch_jira_username(user_id: int, issue_key: str) -> str:
    """取 Jira 的 username 形态 assignee（快照里可能是 displayName，无法 @）。"""
    from app.mcp.capability import capability
    try:
        issue = await capability.call(
            "jira", "get_issue", {"issue_key": issue_key}, user_id, timeout=_MCP_TIMEOUT)
        if isinstance(issue, dict):
            fields = issue.get("fields", {}) or {}
            assignee = fields.get("assignee")
            if isinstance(assignee, str):
                return extract_username(assignee)
            if isinstance(assignee, dict):
                return extract_username(
                    assignee.get("name") or assignee.get("key")
                    or assignee.get("displayName", ""))
    except Exception as e:
        logger.warning("CL scan: fetch_jira_username(%s) failed: %s", issue_key, e)
    return ""


# dry-run 提醒预览落盘目录（相对 backend/）。日志级别默认 WARNING，
# logger.info 不会输出，所以灰度期必须单独落盘供 PM review 完整名单。
_DRY_RUN_LOG_DIR = Path("data/logs")


def append_dry_run_log(user_id: int, issue_key: str, status: str, text: str) -> None:
    """把 dry-run 下"本应发出"的提醒追加到预览文件（按用户分文件）。

    写盘失败不影响扫描（告警日志本身不是关键路径）。
    """
    try:
        _DRY_RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = _DRY_RUN_LOG_DIR / f"cl_scan_dryrun_{user_id}.log"
        stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {issue_key} ({status})\n{text}\n{'-' * 60}\n")
    except OSError as e:
        logger.warning("CL scan: 写 dry-run 预览失败: %s", e)


async def send_reminder(user_id: int, state: ClJiraState, dry_run: bool = False) -> bool:
    """给 owner 发 Jira 评论提醒。dry_run=True 时只落盘到预览文件。

    Returns:
        True 表示已发送（或 dry_run 下视为已发送），调用方可写去重时间戳。
    """
    from app.mcp.capability import capability
    username = extract_username(state.assignee)
    if not username:
        username = await fetch_jira_username(user_id, state.issue_key)
    text = build_reminder_text(state, username)
    if dry_run:
        append_dry_run_log(user_id, state.issue_key, state.status, text)
        return True
    try:
        await capability.call("jira", "add_comment",
                              {"issue_key": state.issue_key, "comment": text},
                              user_id, timeout=_MCP_TIMEOUT)
        logger.info("CL scan: reminder sent to %s (%s)", state.issue_key, state.status)
        return True
    except Exception as e:
        logger.warning("CL scan: add_comment failed for %s: %s", state.issue_key, e)
        return False


# ── 主流程 ───────────────────────────────────────────────────────────────────


async def _fetch_jql_issues(user_id: int, jql: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """执行 JQL，返回 (issues, error)。

    注意：实测 Jira MCP 完全忽略 maxResults（传 1 也返回全部命中项），
    因此监控范围只能靠 JQL 自身限定。
    """
    from app.mcp.capability import capability
    try:
        resp = await capability.call("jira", "search_issues", {
            "jql": jql, "maxResults": 500, "fields": _SEARCH_FIELDS,
        }, user_id, timeout=_MCP_TIMEOUT * 4)
    except Exception as e:
        return [], f"Jira 查询失败: {str(e)[:200]}"
    if not isinstance(resp, dict):
        return [], f"Jira 响应异常: {str(resp)[:200]}"
    issues = [i for i in (resp.get("issues") or []) if isinstance(i, dict)]
    return issues, None


async def _recheck_issue(user_id: int, issue_key: str) -> Tuple[Optional[Dict[str, str]], bool]:
    """复查单个不在 JQL 结果里的题。

    Returns:
        (info, failed)：failed=True 表示查询失败（调用方累计 fail_count）。
    """
    from app.mcp.capability import capability
    try:
        issue = await capability.call(
            "jira", "get_issue", {"issue_key": issue_key}, user_id, timeout=_MCP_TIMEOUT)
    except Exception as e:
        logger.warning("CL scan: get_issue(%s) failed: %s", issue_key, e)
        return None, True
    if not isinstance(issue, dict) or not issue.get("key"):
        return None, True
    return parse_issue_fields(issue), False


async def run_daily_scan(user_id: int, *, dry_run: bool = False) -> Dict[str, Any]:
    """执行一轮 CL 监控扫描（每日调度 / 手动刷新共用）。

    Args:
        user_id: 用户 ID。
        dry_run: True 时照常扫描并更新队列，但 Jira 提醒评论只写日志不发。

    Returns:
        统计信息 {"projects","new","changed","unchanged","disappeared",
                  "archived","cl_checked","reminded","exited","errors"}。
    """
    from app.services.dashboard_service import build_cl_jql, get_cl_monitoring_config

    stats: Dict[str, Any] = {
        "projects": 0, "new": 0, "changed": 0, "unchanged": 0,
        "disappeared": 0, "archived": 0, "cl_checked": 0,
        "reminded": 0, "exited": 0, "suppressed_by_rd": 0, "acked": 0, "errors": [],
    }
    config = await get_cl_monitoring_config(user_id)
    projects = [p for p in config.get("projects", [])
                if p.get("enabled", True) and p.get("cl_monitoring_enabled", True)]
    if not projects:
        return stats
    print(f"[CL scan] user {user_id} start ({len(projects)} project(s), "
          f"dry_run={dry_run})", flush=True)

    now = _now()
    suppressed_list = await _load_suppressed(user_id)
    from app.services.cl_suppression_service import _suppressed_keys
    suppressed_keys = _suppressed_keys(suppressed_list)
    # 整条 Jira 标不关注（{"instance": "_jira", ...}）：本轮不再提醒这些题
    _jira_suppressed.clear()
    _jira_suppressed.update(
        e.get("jira_key") for e in suppressed_list
        if isinstance(e, dict) and e.get("instance") == "_jira"
    )

    async with AsyncSessionLocal() as db:
        # 队列全量（含已退出），避免退出题被当成"新题"重复入队
        all_states = await queue.load_states(db, user_id)
        by_key: Dict[str, ClJiraState] = {s.issue_key: s for s in all_states}

        # ── ①② 取当前集合 + 与队列 diff ───────────────────────────────
        current: Dict[str, Dict[str, Any]] = {}   # issue_key -> {info, project}
        for proj in projects:
            name = proj.get("name", "未命名项目")
            jql = build_cl_jql(proj)
            if not jql:
                stats["errors"].append(f"{name}: 未配置 JQL 查询条件")
                continue
            issues, err = await _fetch_jql_issues(user_id, jql)
            if err:
                # 查询失败的项目整轮跳过，保留队列中已有状态（绝不清零）
                stats["errors"].append(f"{name}: {err}")
                continue
            stats["projects"] += 1
            for issue in issues:
                info = parse_issue_fields(issue)
                if info["key"]:
                    current[info["key"]] = {"info": info, "project": name}

        need_cl_keys: Set[str] = set()
        for issue_key, payload in current.items():
            info, project_name = payload["info"], payload["project"]
            state = by_key.get(issue_key)
            first_scan = state is None
            if state is None:
                state = await queue.upsert_state(db, user_id, issue_key, project_name)
                by_key[issue_key] = state
            elif project_name and state.project_name != project_name:
                state.project_name = project_name
            # prev_updated 必须在 apply_seen 之前取（后者会覆盖 issue_updated）
            prev_updated = state.issue_updated
            outcome = apply_seen(state, info, first_scan=first_scan, now=now)
            stats[outcome] += 1
            if state.archived:
                stats["archived"] += 1
            if should_refresh_cl(state, outcome, info, prev_updated):
                need_cl_keys.add(issue_key)

        # ── ③ 消失即复查：队列中不在本轮结果里的题 ──────────────────────
        # 归档题（首轮即 Closed 的历史记录）不复查：它们已被安全停放，
        # 只有重新出现在 JQL 结果里且状态变化时才会被唤醒。
        gone_keys = [k for k, s in by_key.items()
                     if s.queued and not s.archived and k not in current]
        for issue_key in gone_keys:
            state = by_key[issue_key]
            info, failed = await _recheck_issue(user_id, issue_key)
            if failed:
                state.fail_count = (state.fail_count or 0) + 1
                if state.fail_count >= _MAX_FAIL_COUNT:
                    state.archived = True
                    logger.warning("CL scan: %s 连续 %d 轮查询失败，已归档",
                                   issue_key, state.fail_count)
                continue
            stats["disappeared"] += 1
            prev_updated = state.issue_updated
            outcome = apply_seen(state, info, first_scan=False, now=now)
            if outcome == "changed":
                stats["changed"] += 1
                if should_refresh_cl(state, outcome, info, prev_updated):
                    need_cl_keys.add(issue_key)
            if state.archived:
                stats["archived"] += 1

        # ── ④ CL 确认（只对焦点题）────────────────────────────────────
        await _confirm_cls(user_id, by_key, need_cl_keys, current, now, stats,
                           suppressed_keys)

        # ── ⑤ 用队列重建页面缓存（RD 检测依赖它读评论）─────────────────
        from app.services.cl_snapshot_service import rebuild_from_queue
        await rebuild_from_queue(db, user_id)
        await db.commit()

        # ── ⑥ 经办人回复检测 -> CL 级抑制 + Jira 级"已结算"──────────────
        # 放在 CL 确认之后：经办人常在某题 Closed 后才回复"不需要合并"，
        # 此时页面上已有该题的最新 CL 明细可供比对。
        try:
            from app.services.cl_auto_notify_service import scan_rd_replies_for_suppression
            rd = await scan_rd_replies_for_suppression(user_id)
            if rd.get("suppressed"):
                stats["suppressed_by_rd"] = rd["suppressed"]
            if rd.get("suppressed") or rd.get("analyzed"):
                suppressed_keys = await _load_suppressed_keys(user_id)
                for state in by_key.values():
                    apply_suppression(state, suppressed_keys)
            # 本轮的"已结算"结论：命中的写 ack；重新分析但不再命中的清除 ack
            acks = rd.get("acks") or {}
            for jira_key in rd.get("analyzed") or []:
                state = by_key.get(jira_key)
                if state is None:
                    continue
                if jira_key in acks:
                    apply_ack(state, acks[jira_key], now)
                else:
                    clear_ack(state)
            stats["acked"] = len(acks)
        except Exception as e:
            logger.warning("CL scan: 经办人回复检测失败: %s", e)
            stats["errors"].append(f"回复检测失败: {str(e)[:150]}")

        # ── ⑦ 提醒与退出 ─────────────────────────────────────────────
        # 归档题：不提醒也不退出，静默停放到状态变化唤醒
        for state in by_key.values():
            if state.archived or not state.queued:
                continue
            remind_status = needs_reminder(state, now)
            if remind_status and await send_reminder(user_id, state, dry_run=dry_run):
                state.last_reminded_at = now
                state.last_reminded_status = remind_status
                stats["reminded"] += 1
            if should_exit_queue(state):
                state.queued = False
                state.exited_at = now
                stats["exited"] += 1

        # ── ⑧ 收尾：提醒/退出后计数可能变化，重建一次页面缓存 ───────────
        await rebuild_from_queue(db, user_id)
        # 扫描出错时（JQL 写错、MCP 不可达等）把错误带到页面上，
        # 否则用户只会看到一份"没变化"的旧数据，完全察觉不到配置写错了
        if stats["errors"]:
            from app.services.cl_snapshot_service import save_snapshots_in
            await save_snapshots_in(
                db, [{"project": "error", "error": "；".join(stats["errors"][:3])}], user_id)
        await db.commit()

    # 用 print 而非 logger：应用未配置 logging，默认级别 WARNING 会吞掉 info，
    # 导致扫描结果在 prod.log 里完全不可见
    print(f"[CL scan] user {user_id}: "
          f"{ {k: v for k, v in stats.items() if k != 'errors'} }", flush=True)
    if stats["errors"]:
        print(f"[CL scan] user {user_id} errors: {stats['errors']}", flush=True)
    return stats


async def _load_suppressed(user_id: int) -> List[Dict]:
    """加载该用户的"不关注"标记原始列表（含逐 CL 和整条 Jira 两种）。"""
    try:
        from app.services.settings_service import get_cl_suppressed
        return list(await get_cl_suppressed(user_id) or [])
    except Exception as e:
        logger.warning("CL scan: 加载不关注标记失败: %s", e)
        return []


async def _load_suppressed_keys(user_id: int) -> Set[Tuple[str, str, str]]:
    """加载该用户标记"不关注"的 CL 键集合（供 CL 明细过滤）。"""
    from app.services.cl_suppression_service import _suppressed_keys
    return _suppressed_keys(await _load_suppressed(user_id))


async def _confirm_cls(user_id: int, by_key: Dict[str, ClJiraState],
                       need_keys: Set[str], current: Dict[str, Dict[str, Any]],
                       now: datetime, stats: Dict[str, Any],
                       suppressed_keys: Optional[Set[Tuple[str, str, str]]] = None) -> None:
    """对需要刷新的题批量确认 CL 合并状态。

    批量调用 get_cl_status_for_issues（内部已限并发），比逐题调用快得多。
    无任何 CL 的题不会出现在返回的 jira_groups 里 -> 标记 cl_noregister；
    但**评论拉取失败**的题必须跳过（保留上次结果），否则一次 Jira 抖动就会
    把一大批题误判成"未登记 CL"。
    """
    if not need_keys:
        return

    normalized: List[Dict[str, Any]] = []
    for key in need_keys:
        state = by_key.get(key)
        if state is None:
            continue
        payload = current.get(key)
        info = payload["info"] if payload else {
            "key": key, "summary": state.summary,
            "status": state.status, "assignee": state.assignee,
        }
        normalized.append({
            "key": key,
            "fields": {"summary": info.get("summary", ""),
                       "status": info.get("status", ""),
                       "assignee": info.get("assignee", "")},
        })
    if not normalized:
        return

    try:
        data = await get_cl_status_for_issues(normalized, user_id=user_id)
    except Exception as e:
        logger.warning("CL scan: CL 确认失败: %s", e)
        stats["errors"].append(f"CL 确认失败: {str(e)[:150]}")
        return

    failed = set(data.get("failed_keys") or [])
    groups = {g.get("jira_key"): g for g in data.get("jira_groups", [])}
    for key in need_keys:
        state = by_key.get(key)
        if state is None or key in failed:
            continue
        apply_cl_result(state, groups.get(key), now, suppressed_keys)
        stats["cl_checked"] += 1
