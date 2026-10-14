"""
FocusSet 服务 — PM 关注集合（P-B/C：monitoring_optimization）。

支持：创建/更新关注集合（按 user_id+type+identifier 去重 upsert）、
刷新成员（snapshot）、看板列表 + 健康度计算、取消关注。
健康度 = fix rate 达成 + 距截止天数 + 停滞成员（方案 8.3）。
"""

import asyncio
import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.focus_set import FocusSet, FocusSetMember
from app.services.urgency import stagnation_factor

logger = __import__("logging").getLogger(__name__)

# 视为"已完成"的状态（用于 fix rate 计算）
_DONE_STATUSES = {"Resolved", "Verified", "Closed", "Done", "已解决", "已完成", "已验证", "已关闭"}

# 业务统一用北京时区口径（review #4：原 _BJS_TZ=UTC 名不副实，与 date.today() 本地口径
# 混用在早晚边界差一天；统一为 +08:00 并经 _today() 单一入口取值）
_BJS_TZ = timezone(timedelta(hours=8))


def _today() -> date:
    """业务"今天"的统一口径（北京时区），健康度/停滞/基线/提醒共用。"""
    return datetime.now(_BJS_TZ).date()


# ── 健康度计算（纯函数，可单测）─────────────────────────────────────────────


def _is_done_status(status: Optional[str]) -> bool:
    """判断 Jira 状态是否视为已完成。"""
    return (status or "") in _DONE_STATUSES


def compute_health(
    deadline: Optional[date],
    metric_target: Optional[float],
    members: List[FocusSetMember],
    stale_days: int = 7,
    baseline_total: Optional[int] = None,
    baseline_members: Optional[List[Dict[str, Any]]] = None,
    last_done_count: Optional[int] = None,
    metric_type: Optional[str] = None,
) -> Dict[str, Any]:
    """计算关注集合健康度：指标达成 + 倒计时 + 状态分布 + 瓶颈 + 周基线变化。

    对 epic 类型关注集合，若存在 baseline_total，则 fix rate 分母固定为周一基线总数，
    新增/移除 issue 不影响分母。

    metric_type 语义（review #3，原死配置激活）：
      - "fix_rate"（默认/None）：按 metric_target（如 0.8）判定达成；
      - "all_fix"：全部完成才算达标——未显式设 target 时按 1.0 计。

    Returns:
        {"total", "done", "progress", "target", "days_left", "diff_to_target",
         "status_dist", "stagnant_members", "status": green|yellow|red,
         "baseline_total", "weekly_changes", "done_delta"}
    """
    total = len(members)
    done = sum(1 for m in members if _is_done_status(m.status))

    # epic 周基线：有基线时用基线总数作分母，否则用当前总数
    denominator = baseline_total if baseline_total and baseline_total > 0 else total
    progress = round(done / denominator, 4) if denominator else 0.0

    days_left = None
    if deadline is not None:
        days_left = (deadline - _today()).days

    # 生效目标：all_fix 未设 target 时按全部完成（1.0）计
    effective_target = metric_target
    if metric_type == "all_fix" and effective_target is None:
        effective_target = 1.0

    diff_to_target = None
    achieved = None
    if effective_target is not None and denominator:
        diff_to_target = math.ceil(denominator * effective_target) - done
        achieved = progress >= effective_target

    status_dist: Dict[str, int] = {}
    for m in members:
        s = m.status or "未知"
        status_dist[s] = status_dist.get(s, 0) + 1

    stagnant_members = [
        {"key": m.issue_key, "summary": m.summary, "status": m.status,
         "assignee": m.assignee, "created": m.created,
         "last_comment_time": m.last_comment_time, "stagnant_days": m.stagnant_days}
        for m in members if m.stagnant
    ]

    # 本周变化：以周一基线状态为参照
    baseline_map = {b.get("key", ""): b.get("status", "") for b in (baseline_members or []) if b.get("key")}
    current_map = {m.issue_key: m.status for m in members}

    def _issue_stub(k: str) -> Dict[str, str]:
        info = next((m for m in members if m.issue_key == k), None)
        return {"key": k, "summary": info.summary if info else "", "status": current_map.get(k, baseline_map.get(k, ""))}

    newly_done = [_issue_stub(k) for k, s in current_map.items()
                  if _is_done_status(s) and not _is_done_status(baseline_map.get(k))]
    reopened = [_issue_stub(k) for k, s in current_map.items()
                if not _is_done_status(s) and _is_done_status(baseline_map.get(k))]
    added = [_issue_stub(k) for k in current_map if k not in baseline_map]
    removed = [{"key": k, "summary": "", "status": baseline_map.get(k, "")} for k in baseline_map if k not in current_map]

    # 今日新增完成数：与上次刷新时的完成数对比（有值时才计算）
    done_delta = None
    if last_done_count is not None:
        done_delta = done - last_done_count

    # 状态判定：red(超期或严重不达标) / yellow(接近或停滞) / green(达标)
    if days_left is not None and days_left < 0:
        status = "red"
    elif achieved is True:
        status = "green"
    elif achieved is False:
        status = "red" if (days_left is not None and days_left <= 3) else "yellow"
    elif total == 0:
        status = "yellow"
    elif stagnant_members:
        status = "yellow"
    else:
        status = "green"

    return {
        "total": total,
        "done": done,
        "progress": progress,
        "target": effective_target,
        "metric_type": metric_type,
        "achieved": achieved,
        "days_left": days_left,
        "diff_to_target": diff_to_target,
        "status_dist": status_dist,
        "stagnant_members": stagnant_members,
        "status": status,
        "baseline_total": baseline_total,
        "weekly_changes": {
            "newly_done": newly_done,
            "reopened": reopened,
            "added": added,
            "removed": removed,
        },
        "done_delta": done_delta,
    }


# ── Epic 周基线辅助函数 ────────────────────────────────────────────────────────


def _split_goal_tag(identifier: str) -> tuple:
    """把 identifier 拆成 (基础标识, 目标标签)。

    支持同一 filter/epic 建多个不同目标的集合（如 `TV-918177#week80` 与
    `TV-918177#month100` 是两个独立集合，互不 upsert 覆盖）。零 schema 变更：
    全量 identifier 参与唯一索引，JQL/解析只用基础标识。

    Returns:
        (base, tag): tag 无后缀时为 ""。
    """
    s = str(identifier or "").strip()
    if "#" in s:
        base, tag = s.split("#", 1)
        return base.strip(), tag.strip()
    return s, ""


def _parse_epic_identifier(identifier: str) -> str:
    """从 epic key 或 Jira browse URL 中解析出 epic key（剥掉 #goal 目标后缀）。

    支持：
      - TV-918177
      - TV-918177#week80（多目标集合，取 # 前作为 epic key）
      - https://jira.example.com/browse/TV-918177
      - https://jira.example.com/browse/TV-918177?filter=123
    """
    s, _tag = _split_goal_tag(identifier)
    # 去掉查询参数
    if "?" in s:
        s = s.split("?")[0]
    # 去掉末尾斜杠，取最后一段
    s = s.rstrip("/")
    if "/" in s:
        s = s.split("/")[-1]
    return s


def _build_epic_jql(identifier: str) -> str:
    """根据 epic key 生成用于拉取 epic 下 issue 的 JQL。"""
    key = _parse_epic_identifier(identifier)
    return f'"Epic Link" = {key}'


async def update_weekly_baseline(focus_set_id: int, issues: List[Dict[str, Any]]) -> None:
    """对 epic 类型 FocusSet 固化本周基线。

    规则：
      - 每周一刷新时重置基线为当前 epic 成员列表。
      - 基线**过期自动重建**（review #5）：baseline_date 早于"最近一个周一"即重建——
        周一那轮刷新若因 MCP 故障失败，周二及之后的成功轮会补建，避免全周分母
        停留在上周导致 fix rate 口径失真。
      - 非周一但尚无基线时（例如周三新建），立即建立当前列表作为临时基线。
      - filter/manual 类型不触发。
    """
    today = _today()
    async with AsyncSessionLocal() as db:
        fs = await db.get(FocusSet, focus_set_id)
        if fs is None or fs.type != "epic":
            return
        most_recent_monday = today - timedelta(days=today.weekday())
        if fs.baseline_date is None or fs.baseline_date < most_recent_monday:
            fs.baseline_total = len(issues)
            fs.baseline_date = today
            fs.baseline_members = [
                {"key": str(i.get("key", "")), "status": str(i.get("status", ""))}
                for i in issues if i.get("key")
            ]
        await db.commit()


# ── CRUD（DB）────────────────────────────────────────────────────────────────


async def create_or_update_focus_set(
    user_id: int,
    focus_type: str,
    identifier: str,
    name: str,
    jql: Optional[str] = None,
    deadline: Optional[date] = None,
    metric_type: Optional[str] = None,
    metric_target: Optional[float] = None,
    priority_semantics: Optional[str] = None,
    description: Optional[str] = None,
) -> FocusSet:
    """按 (user_id, type, identifier) upsert 关注集合。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(FocusSet).where(
                FocusSet.user_id == user_id,
                FocusSet.type == focus_type,
                FocusSet.identifier == identifier,
            )
        )
        fs = result.scalar_one_or_none()
        if fs is None:
            fs = FocusSet(
                user_id=user_id, type=focus_type, identifier=identifier,
                name=name, jql=jql, deadline=deadline,
                metric_type=metric_type, metric_target=metric_target,
                priority_semantics=priority_semantics, description=description,
            )
            db.add(fs)
        else:
            fs.name = name
            fs.active = True
            if jql is not None:
                fs.jql = jql
            if deadline is not None:
                fs.deadline = deadline
            if metric_type is not None:
                fs.metric_type = metric_type
            if metric_target is not None:
                fs.metric_target = metric_target
            if priority_semantics is not None:
                fs.priority_semantics = priority_semantics
            if description is not None:
                fs.description = description
        await db.commit()
        await db.refresh(fs)
        return fs


async def refresh_focus_set_members(focus_set_id: int, issues: List[Dict[str, Any]],
                                    user_id: Optional[int] = None,
                                    stale_days: int = 7) -> int:
    """刷新关注集合成员（替换式 snapshot），返回成员数。

    **停滞基准（2026-08-13）**：优先最后评论时间（last_comment_time），
    无评论时回退 issue.updated —— 停滞天数 = 今天 − 最后评论时间。
    仅对 updated 距今 > stale_days 的成员拉取评论（其余评论时间必然更近，必定不停滞，省 MCP 调用）。

    Args:
        focus_set_id: 关注集合 ID
        issues: 成员列表（含 key/summary/status/priority/assignee/created/updated）
        user_id: 用户 ID（拉取评论用），None 时跳过评论拉取（降级用 updated）
        stale_days: 停滞阈值天数
    """
    today = _today()

    # 0) 读旧快照建 map（review #2：updated 未变的成员其评论必然未变——Jira 加评论
    #    会更新 issue.updated——复用旧 last_comment_time 跳过 MCP 拉取，大幅降低
    #    get_issue_comments 调用量（该 MCP 不分页全量返回，调用昂贵））
    existing_map: Dict[str, FocusSetMember] = {}
    async with AsyncSessionLocal() as read_db:
        existing_rows = (await read_db.execute(
            select(FocusSetMember).where(FocusSetMember.focus_set_id == focus_set_id)
        )).scalars().all()
        existing_map = {m.issue_key: m for m in existing_rows}

    def _reuse_comment_time(key: str, new_updated: str) -> Optional[str]:
        old = existing_map.get(key)
        if old is not None and old.updated_at == new_updated and old.last_comment_time:
            return old.last_comment_time
        return None

    # 1) 停滞候选（updated 距今 > stale_days）并发拉取最后评论时间；
    #    updated 未变且已有评论时间的直接复用，不再拉取
    candidates = {
        iss.get("key"): iss
        for iss in issues
        if (today - _parse_updated_date(iss.get("updated", ""))).days > stale_days
    }
    last_comments: Dict[str, str] = {}
    to_fetch: Dict[str, str] = {}  # key → new_updated
    for key, iss in candidates.items():
        reused = _reuse_comment_time(key, iss.get("updated", ""))
        if reused:
            last_comments[key] = reused
        else:
            to_fetch[key] = iss.get("updated", "")

    if user_id and to_fetch:
        sem = asyncio.Semaphore(8)

        async def _fetch(key: str) -> None:
            async with sem:
                try:
                    ts = await _fetch_last_comment_time(user_id, key)
                    if ts:
                        last_comments[key] = ts
                except Exception:
                    pass  # 评论拉取失败降级用 updated

        await asyncio.gather(*(_fetch(k) for k in to_fetch))

    # 2) 替换式写入快照；上一日完成数从旧快照算（供日报 delta）
    previous_done = sum(1 for m in existing_map.values() if _is_done_status(m.status))
    async with AsyncSessionLocal() as db:
        fs = await db.get(FocusSet, focus_set_id)
        if fs is not None:
            fs.last_done_count = previous_done

        await db.execute(delete(FocusSetMember).where(FocusSetMember.focus_set_id == focus_set_id))
        now = datetime.now(_BJS_TZ)
        for iss in issues:
            key = iss.get("key", "")
            updated = iss.get("updated", "")
            last_comment = last_comments.get(key) or ""
            # 停滞基准：最后评论时间，无评论回退 updated
            last_active = last_comment or updated
            stagnant_days = max((today - _parse_updated_date(last_active)).days, 0) if last_active else 0
            member = FocusSetMember(
                focus_set_id=focus_set_id,
                issue_key=key,
                summary=iss.get("summary", "") or "",
                status=iss.get("status", "") or "",
                priority=iss.get("priority", "") or "",
                assignee=iss.get("assignee", "") or "",
                created=iss.get("created", "") or "",
                last_comment_time=last_comment or None,
                updated_at=updated,
                stagnant=stagnant_days > stale_days,
                stagnant_days=stagnant_days,
                snapshot_time=now,
            )
            db.add(member)
        await db.commit()
        return len(issues)


async def _fetch_last_comment_time(user_id: int, issue_key: str) -> Optional[str]:
    """获取 Jira issue 最后一条评论的创建时间（ISO）。无评论/失败返回 None。"""
    from app.mcp.capability import capability

    resp = await capability.call(
        "jira", "get_issue_comments", {"issue_key": issue_key}, user_id, timeout=15,
    )
    comments = resp.get("comments", []) if isinstance(resp, dict) else []
    times = [c.get("created", "") for c in comments if isinstance(c, dict) and c.get("created")]
    return max(times) if times else None


def _parse_updated_date(updated: str) -> date:
    """解析 Jira ISO 时间 → 日期。兼容带/不带冒号的 UTC 偏移（+00:00 / +0000）。"""
    try:
        dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        try:
            dt = datetime.strptime(updated, "%Y-%m-%dT%H:%M:%S.%f%z")
        except (ValueError, TypeError):
            return _today()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.date()


async def list_focus_sets(user_id: int, active_only: bool = True,
                          stale_days: int = 7) -> List[Dict[str, Any]]:
    """列出用户的关注集合（含健康度 + 成员分布 + 停滞成员 + epic 周基线）。

    review #6：一次 join 查询拉全部成员再内存分组，消除"每集合单独查成员"的 N+1。
    """
    async with AsyncSessionLocal() as db:
        stmt = select(FocusSet).where(FocusSet.user_id == user_id)
        if active_only:
            stmt = stmt.where(FocusSet.active.is_(True))
        sets = (await db.execute(stmt.order_by(FocusSet.updated_at.desc()))).scalars().all()
        if not sets:
            return []

        # 一次查询该用户这些集合的全部成员，内存按 focus_set_id 分组
        set_ids = [fs.id for fs in sets]
        all_members = (await db.execute(
            select(FocusSetMember).where(FocusSetMember.focus_set_id.in_(set_ids))
        )).scalars().all()
        members_by_set: Dict[int, List[FocusSetMember]] = {}
        for m in all_members:
            members_by_set.setdefault(m.focus_set_id, []).append(m)

        result: List[Dict[str, Any]] = []
        for fs in sets:
            members = members_by_set.get(fs.id, [])
            health = compute_health(
                fs.deadline, fs.metric_target, list(members), stale_days,
                baseline_total=fs.baseline_total,
                baseline_members=fs.baseline_members,
                last_done_count=fs.last_done_count,
                metric_type=fs.metric_type,
            )
            health["baseline_date"] = fs.baseline_date.isoformat() if fs.baseline_date else None

            # 成员明细（完整，供看板卡片展示/展开）
            member_list = [
                {
                    "key": m.issue_key,
                    "summary": m.summary,
                    "status": m.status,
                    "priority": m.priority,
                    "assignee": m.assignee,
                    "created": m.created,
                    "last_comment_time": m.last_comment_time,
                    "stagnant": m.stagnant,
                    "stagnant_days": m.stagnant_days,
                }
                for m in members
            ]
            # 按负责人分布（TOP，供 PM 催办参考）
            assignee_dist: Dict[str, int] = {}
            for m in members:
                a = m.assignee or "未分配"
                assignee_dist[a] = assignee_dist.get(a, 0) + 1
            top_assignees = sorted(
                assignee_dist.items(), key=lambda kv: kv[1], reverse=True,
            )[:6]

            result.append({
                "id": fs.id,
                "type": fs.type,
                "identifier": fs.identifier,
                "name": fs.name,
                "jql": fs.jql,
                "description": fs.description,
                "deadline": fs.deadline.isoformat() if fs.deadline else None,
                "metric": {"type": fs.metric_type, "target": fs.metric_target}
                if fs.metric_type else None,
                "priority_semantics": fs.priority_semantics,
                "health": health,
                "members": member_list,
                "assignee_dist": [{"name": k, "count": v} for k, v in top_assignees],
            })
        return result


async def get_focus_set_members(focus_set_id: int) -> List[Dict[str, Any]]:
    """获取关注集合的全部成员（供 LLM 风险精判使用）。"""
    async with AsyncSessionLocal() as db:
        members = (await db.execute(
            select(FocusSetMember).where(FocusSetMember.focus_set_id == focus_set_id)
        )).scalars().all()
        return [
            {
                "key": m.issue_key,
                "summary": m.summary,
                "status": m.status,
                "priority": m.priority,
                "assignee": m.assignee,
                "updated": m.updated_at,
                "stagnant": m.stagnant,
                "stagnant_days": m.stagnant_days,
            }
            for m in members
        ]


async def dismiss_focus_set(user_id: int, focus_set_id: int) -> bool:
    """取消关注（active=False）。返回是否成功。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(FocusSet).where(FocusSet.id == focus_set_id, FocusSet.user_id == user_id)
        )
        fs = result.scalar_one_or_none()
        if fs is None:
            return False
        fs.active = False
        await db.commit()
        return True


# ── 成员拉取 + 后台刷新/提醒辅助（P0-1/P0-2，单一事实来源）───────────────────

_MEMBER_FIELDS = ["key", "summary", "status", "priority", "assignee", "created", "updated", "duedate"]
_PAGE_SIZE = 200  # 每页请求条数（MCP 服务端可能忽略，见 fetch_members_with_meta）
_MAX_PAGES = 10   # 分页循环上限（防御异常死循环，200×10=2000 条）


def _field_text(obj: Any) -> str:
    """从 Jira 字段值提取文本（兼容 dict {value/name} 或 str）。"""
    if isinstance(obj, dict):
        return str(obj.get("value") or obj.get("name") or "")
    return str(obj or "")


def _parse_issue_fields(iss: Dict[str, Any]) -> Dict[str, Any]:
    """解析单条 Jira issue 为关注集合成员字段（单一事实来源）。

    review #7：jira_resolve_filter 与 fetch_members_with_meta 共用，消除两份拷贝。
    """
    f = iss.get("fields", {}) or {}
    if not isinstance(f, dict):
        f = {}
    assignee = f.get("assignee", "")
    if isinstance(assignee, dict):
        assignee = assignee.get("displayName", assignee.get("name", ""))
    return {
        "key": iss.get("key", ""),
        "summary": str(f.get("summary", "") or ""),
        "status": _field_text(f.get("status")),
        "priority": _field_text(f.get("priority")),
        "assignee": str(assignee or ""),
        "created": str(f.get("created", "") or ""),
        "updated": str(f.get("updated", "") or ""),
        "duedate": str(f.get("duedate", "") or ""),
    }


async def fetch_members_with_meta(user_id: int, jql: str) -> Any:
    """按 JQL 拉取关注集合成员，返回 (members, meta)。

    meta = {"total": 服务端总数|None, "fetched": 实际取回数, "truncated": bool}

    **分页与截断（review #1）**：实测 MCP `search_issues` **忽略 startAt/maxResults**
    （每次全量返回，响应带 `total`）。若服务端返回数 < total 说明发生了截断——
    此时尝试 startAt 翻页补救（防御未来服务端支持）；若翻页返回重复（确认不支持）
    则停止并 **truncated=True + warning 日志（不静默）**，调用方须在结果中提示用户。
    """
    empty_meta = {"total": None, "fetched": 0, "truncated": False}
    if not jql:
        return [], empty_meta
    from app.mcp.capability import capability

    issues: List[Dict[str, Any]] = []
    seen_keys: set = set()
    start_at = 0
    total: Optional[int] = None

    for _ in range(_MAX_PAGES):
        resp = await capability.call(
            "jira", "search_issues",
            {"jql": jql, "startAt": start_at, "maxResults": _PAGE_SIZE, "fields": _MEMBER_FIELDS},
            user_id, timeout=30,
        )
        if not isinstance(resp, dict):
            break
        if isinstance(resp.get("total"), int):
            total = resp["total"]
        batch = [i for i in (resp.get("issues", []) or []) if isinstance(i, dict)]
        new_items = [i for i in batch if i.get("key") not in seen_keys]
        for i in new_items:
            seen_keys.add(i.get("key"))
            issues.append(_parse_issue_fields(i))

        # 取全了 → 结束
        if total is not None and len(issues) >= total:
            break
        # 本页全是重复（服务端忽略 startAt）或空 → 再翻无意义，结束
        if not new_items:
            break
        # 响应无 total 字段（老服务端不告知总数）→ 单次返回即视为全量，不翻页，
        # 避免对不支持分页的服务端重复调用
        if total is None:
            break
        # 防御：按服务端实际返回推进游标（部分支持分页的服务端也能工作）
        start_at += len(batch) if batch else _PAGE_SIZE

    truncated = total is not None and len(issues) < total
    if truncated:
        logger.warning(
            "focus_set 成员截断：jql=%s… fetched=%d < total=%d（MCP search_issues 不支持分页参数，"
            "filter 超 %d 成员时无法取回全部，fix rate 分母可能偏小）",
            str(jql)[:80], len(issues), total, _PAGE_SIZE,
        )
    return issues, {"total": total, "fetched": len(issues), "truncated": truncated}


async def fetch_members_by_jql(user_id: int, jql: str) -> List[Dict[str, Any]]:
    """通过 MCP search_issues 按 JQL 拉取关注集合成员（供创建/后台刷新复用）。

    兼容包装：截断检测见 fetch_members_with_meta（truncated 时打 warning 不静默）。
    """
    members, _meta = await fetch_members_with_meta(user_id, jql)
    return members


async def list_active_focus_sets(user_id: int) -> List[Dict[str, Any]]:
    """列出 active 关注集合的轻量信息（id/type/identifier/name/jql），供后台刷新。"""
    async with AsyncSessionLocal() as db:
        stmt = select(FocusSet).where(
            FocusSet.user_id == user_id, FocusSet.active.is_(True),
        )
        sets = (await db.execute(stmt)).scalars().all()
        return [
            {"id": fs.id, "type": fs.type, "identifier": fs.identifier,
             "name": fs.name, "jql": fs.jql}
            for fs in sets
        ]


async def get_focus_set_notify_state(user_id: int) -> dict:
    """返回关注集合提醒去重状态 {focus_set_id: {"achieved": bool, "overdue_date": "YYYY-MM-DD"}}."""
    from app.services.settings_service import load_user_settings
    settings_data = await load_user_settings(user_id)
    return settings_data.get("focus_set_notify") or {}


async def save_focus_set_notify_state(user_id: int, state: dict) -> dict:
    """全量覆写提醒去重状态（绕开 save_user_settings 的 dict 深合并，直接操作表）。"""
    from app.models import UserSetting

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(UserSetting).where(
                    UserSetting.user_id == user_id,
                    UserSetting.key == "focus_set_notify",
                )
            )
        ).scalar_one_or_none()
        payload = json.dumps(state, ensure_ascii=False)
        if row:
            row.value = payload
        else:
            db.add(UserSetting(user_id=user_id, key="focus_set_notify", value=payload))
        await db.commit()
    return state
