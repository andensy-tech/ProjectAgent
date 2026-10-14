"""
CL 监控页面缓存服务 — 页面读的"每个项目一行"快照。

V2（2026-09-16）起快照不再由"整轮重查"直接写入，而是扫描结束后
用追踪队列（`cl_jira_states`）重建：

  - load_latest_snapshots()  — 读缓存（页面默认路径，快）
  - rebuild_from_queue()     — 扫描后按队列重建（写入入口）
  - load_or_refresh()        — 缓存过期/手动刷新时跑一次扫描

快照超过 24 小时视为过期（SNAPSHOT_TTL_HOURS）。
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.cl_jira_state import ClJiraState
from app.models.cl_monitor import ClMonitorSnapshot

logger = logging.getLogger(__name__)

# Default snapshot TTL: 24 hours
SNAPSHOT_TTL_HOURS = 24


async def _get_db() -> AsyncSession:
    """Get a fresh DB session."""
    async with AsyncSessionLocal() as session:
        yield session


async def load_latest_snapshots(user_id: Optional[int] = None) -> Optional[List[Dict[str, Any]]]:
    """Load the latest snapshot from DB, one row per project.

    Args:
        user_id: When provided, filters snapshots for a specific user.

    Returns deserialized project list, or None if no snapshot exists.
    """
    async with AsyncSessionLocal() as db:
        # Get the most recent snapshot for each project using GROUP BY max(id)
        # (SQLite does not support DISTINCT ON, so we use GROUP BY)
        subq = (
            select(
                ClMonitorSnapshot.project_name,
                func.max(ClMonitorSnapshot.id).label('max_id'),
            )
        )
        if user_id is not None:
            subq = subq.where(ClMonitorSnapshot.user_id == user_id)
        subq = subq.group_by(ClMonitorSnapshot.project_name).subquery()
        stmt = (
            select(ClMonitorSnapshot)
            .join(subq, ClMonitorSnapshot.id == subq.c.max_id)
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()

        if not rows:
            return None

        projects = []
        for row in rows:
            try:
                data = json.loads(row.data_json)
                data["_queried_at"] = row.queried_at.isoformat()
                projects.append(data)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning("Failed to decode snapshot for %s: %s", row.project_name, e)
                continue

        # 实时过滤用户标记不关注的 CL（标记后无需等快照刷新即生效）。
        # 所有消费方（前端、R3 告警、trend 采集）都走此函数，过滤后计数统一下降。
        if user_id is not None and projects:
            try:
                from app.services.settings_service import get_cl_suppressed
                from app.services.cl_suppression_service import _suppressed_keys, filter_suppressed_projects
                suppressed = await get_cl_suppressed(user_id)
                if suppressed:
                    projects = filter_suppressed_projects(projects, _suppressed_keys(suppressed))
                    # 整条 Jira 标不关注（前端操作列，reason='jira_ack'）：从页面直接移除
                    jira_suppressed = {
                        e.get("jira_key") for e in suppressed
                        if isinstance(e, dict) and e.get("instance") == "_jira"
                    }
                    if jira_suppressed:
                        for proj in projects:
                            groups = proj.get("jira_groups")
                            if isinstance(groups, list):
                                proj["jira_groups"] = [
                                    g for g in groups if g.get("jira_key") not in jira_suppressed
                                ]
                                # 重算项目级计数
                                proj["total_cls"] = sum(g.get("total_cls", 0) for g in proj["jira_groups"])
                                proj["merged_cls"] = sum(g.get("merged_cls", 0) for g in proj["jira_groups"])
                                proj["unmerged_cls"] = sum(g.get("unmerged_cls", 0) for g in proj["jira_groups"])
                                proj["total_jiras"] = len(proj["jira_groups"])
                                proj["jiras_unmerged"] = sum(1 for g in proj["jira_groups"] if g.get("unmerged_cls", 0) > 0)
                                proj["jiras_merged"] = proj["total_jiras"] - proj["jiras_unmerged"]
            except Exception as e:
                logger.warning("Suppression filter failed for user %s: %s", user_id, e)

        return projects


async def is_stale(project_name: str, db: AsyncSession, ttl_hours: int = SNAPSHOT_TTL_HOURS,
                   user_id: Optional[int] = None) -> bool:
    """Check if a project's snapshot is older than TTL."""
    stmt = (
        select(ClMonitorSnapshot)
        .where(ClMonitorSnapshot.project_name == project_name)
        .order_by(ClMonitorSnapshot.id.desc())
        .limit(1)
    )
    if user_id is not None:
        stmt = stmt.where(ClMonitorSnapshot.user_id == user_id)
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return True
    qa = row.queried_at
    if qa.tzinfo is None:
        qa = qa.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - qa
    return age > timedelta(hours=ttl_hours)


async def any_stale(db: Optional[AsyncSession] = None, ttl_hours: int = SNAPSHOT_TTL_HOURS,
                    user_id: Optional[int] = None) -> bool:
    """Check if any project's snapshot is stale (or no snapshots exist at all)."""
    if db is not None:
        return await _any_stale_with_session(db, ttl_hours, user_id)
    async with AsyncSessionLocal() as sess:
        return await _any_stale_with_session(sess, ttl_hours, user_id)


async def _any_stale_with_session(db: AsyncSession, ttl_hours: int,
                                  user_id: Optional[int] = None) -> bool:
    """Check staleness with an existing session."""
    stmt = select(ClMonitorSnapshot)
    if user_id is not None:
        stmt = stmt.where(ClMonitorSnapshot.user_id == user_id)
    stmt = stmt.limit(1)
    result = await db.execute(stmt)
    first = result.scalar_one_or_none()
    if first is None:
        return True  # no data at all

    # Check the most recent snapshot across all projects
    stmt = (
        select(ClMonitorSnapshot)
        .order_by(ClMonitorSnapshot.queried_at.desc())
        .limit(1)
    )
    if user_id is not None:
        stmt = stmt.where(ClMonitorSnapshot.user_id == user_id)
    result = await db.execute(stmt)
    newest = result.scalar_one_or_none()
    if newest is None:
        return True
    qa = newest.queried_at
    if qa.tzinfo is None:
        qa = qa.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - qa
    return age > timedelta(hours=ttl_hours)


async def save_snapshots(results: List[Dict[str, Any]], user_id: Optional[int] = None) -> None:
    """Persist query results to DB (one row per project).

    Args:
        results: List of project CL status dicts.
        user_id: When provided, associates snapshots with a specific user.
    """
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        await save_snapshots_in(db, results, user_id)
        await db.commit()
    logger.info("Saved %d project snapshots (queried_at=%s)", len(results), now.isoformat())


async def save_snapshots_in(db: AsyncSession, results: List[Dict[str, Any]],
                            user_id: Optional[int] = None) -> None:
    """Persist query results using an existing session（调用方负责 commit）。"""
    now = datetime.now(timezone.utc)
    for proj in results:
        project_name = proj.get("project", "unknown")
        # Remove transient fields before storing
        store_data = {k: v for k, v in proj.items() if not k.startswith("_")}
        db.add(ClMonitorSnapshot(
            project_name=project_name,
            data_json=json.dumps(store_data, ensure_ascii=False),
            queried_at=now,
            user_id=user_id,
        ))


def build_project_payload(project_name: str, states: List["ClJiraState"]) -> Dict[str, Any]:
    """用追踪队列的记录组装前端需要的项目视图（纯函数，便于测试）。

    与旧快照的差异：除"有 CL 的题"外，**未登记 CL 的焦点题也会出现**
    （cl_noregister=True），否则这类最容易被遗漏的问题在页面上完全不可见。

    Args:
        project_name: 项目名。
        states: 该项目的队列记录（调用方已过滤 queued/archived）。

    Returns:
        与旧 ``jira_groups`` 结构兼容的项目字典（附加 V2 字段）。
    """
    from app.services.cl_queue_service import (
        CLOSED_STATUSES, FOCUS_STATUSES, ensure_aware,
    )

    watched = set(FOCUS_STATUSES) | set(CLOSED_STATUSES)
    groups: List[Dict[str, Any]] = []
    for s in states:
        if s.status not in watched:
            continue
        try:
            items = json.loads(s.cl_items_json or "[]")
        except (json.JSONDecodeError, TypeError):
            items = []
        groups.append({
            "jira_key": s.issue_key,
            "summary": s.summary,
            "assignee": s.assignee,
            "status": s.status,
            "prev_status": s.prev_status,
            "status_changed_at": (s.status_changed_at.isoformat()
                                  if s.status_changed_at else None),
            "queue_reason": s.queue_reason,
            "last_reminded_at": (s.last_reminded_at.isoformat()
                                 if s.last_reminded_at else None),
            # 已闭环但仍在队列里盯着的题：经办人常在 Closed 后才回复 CL 处置情况，
            # RD 检测据此决定是否纳入扫描（曾因缺这个字段漏掉 Closed 题的回复）
            "tracking": s.status in CLOSED_STATUSES,
            "ack_at": (ensure_aware(s.ack_at).isoformat() if s.ack_at else None),
            "ack_reason": s.ack_reason or "",
            "total_cls": s.cl_total,
            "merged_cls": s.cl_merged,
            "unmerged_cls": s.cl_unmerged,
            "cl_unregistered": s.cl_noregister,
            "items": items,
        })

    merged_total = sum(g["merged_cls"] for g in groups)
    unmerged_total = sum(g["unmerged_cls"] for g in groups)
    return {
        "project": project_name,
        "total_cls": merged_total + unmerged_total,
        "merged_cls": merged_total,
        "unmerged_cls": unmerged_total,
        "total_jiras": len(groups),
        "jiras_unmerged": sum(1 for g in groups if g["unmerged_cls"] > 0),
        "jiras_merged": sum(1 for g in groups if g["unmerged_cls"] == 0),
        "jira_groups": groups,
    }


async def rebuild_from_queue(db: AsyncSession, user_id: int) -> int:
    """用追踪队列重建页面缓存快照（每个项目一行）。

    扫描结束后调用，替代 V1 的"整轮重查写快照"。调用方负责 commit。

    Returns:
        写入的项目数。
    """
    from app.services.cl_queue_service import load_states

    states = await load_states(db, user_id, only_queued=True, include_archived=False)
    by_project: Dict[str, List[Any]] = {}
    for s in states:
        by_project.setdefault(s.project_name or "未命名项目", []).append(s)

    payloads = [build_project_payload(name, items) for name, items in by_project.items()]
    if payloads:
        await save_snapshots_in(db, payloads, user_id)
    return len(payloads)


async def refresh_all_snapshots(user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """执行一轮 CL 监控扫描，然后回读页面缓存。

    V2 起"刷新"= 跑一次 `run_daily_scan`（追踪队列 diff + 焦点 CL 确认 +
    提醒），页面缓存由队列重建。手动刷新与每日调度走同一条路径，
    避免两套逻辑产生不一致的数据口径。

    Args:
        user_id: 用户 ID。

    Returns:
        页面缓存中的项目列表（按项目最近一条快照组装）。
    """
    from app.core.config import settings
    from app.services.cl_scan_service import run_daily_scan

    logger.info("Starting live CL monitoring refresh...")
    # 手动刷新与每日调度必须共用同一个 dry-run 开关：这里若省略 dry_run，
    # 会走 run_daily_scan 的默认值 False，导致灰度期点「刷新」直接发出真实评论。
    stats = await run_daily_scan(user_id, dry_run=settings.CL_SCAN_DRY_RUN)
    if stats.get("errors"):
        logger.warning("CL refresh errors: %s", stats["errors"])
    logger.info("Live CL monitoring refresh complete — %s",
                {k: v for k, v in stats.items() if k != "errors"})
    return await load_latest_snapshots(user_id) or []


async def _run_scan_no_timeout(user_id: Optional[int],
                               cached: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """后台执行扫描；失败时回退到扫描前的缓存（避免页面空白）。

    故意**不**设超时：扫描一次要 1~3 分钟（焦点题多的话更久），若按旧实现的
    `asyncio.wait_for(120s)` 会 cancel 掉正在运行的扫描 —— 用户点「刷新」只看到
    "回退旧缓存"，以为自己没触发、又点一次，白白反复取消。
    """
    try:
        return await refresh_all_snapshots(user_id)
    except Exception as e:
        logger.warning("CL monitoring refresh failed: %s — serving cache", e)
        if cached:
            return cached
        return [{"project": "error", "error": f"CL 查询失败: {e}"}]


async def _refresh_with_cache_fallback(cached: Optional[List[Dict[str, Any]]],
                                       user_id: Optional[int]) -> List[Dict[str, Any]]:
    """执行扫描；失败时回退到扫描前的缓存（避免页面空白）。

    注意：这里**不**用 `asyncio.wait_for` 设超时 —— 扫描一次要 1~3 分钟，超时
    会 cancel 掉正在跑的扫描（用户点「刷新」只会看到回退缓存，还会再点）。
    """
    try:
        return await _run_scan_no_timeout(user_id, cached)
    except Exception as e:
        logger.warning("CL monitoring refresh failed: %s — serving cache", e)
        if cached:
            return cached
        return [{"project": "error", "error": f"CL 查询失败: {e}"}]


async def load_or_refresh(refresh: bool = False, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Load from cache, or refresh if requested/stale.

    Args:
        refresh: Force a scan regardless of cache state.
        user_id: When provided, loads/saves user-isolated snapshots.

    Returns:
        List of project CL status dicts.
    """
    if refresh:
        logger.info("Forcing live refresh for CL monitoring")
        cached = await load_latest_snapshots(user_id)
        return await _refresh_with_cache_fallback(cached, user_id)

    # Try loading from cache
    cached = await load_latest_snapshots(user_id)
    if cached is not None and not await any_stale(user_id=user_id):
        logger.debug("Serving CL monitoring from cache")
        return cached

    # Cache is stale or missing — refresh
    logger.info("CL monitoring cache is stale, refreshing...")
    return await _refresh_with_cache_fallback(cached, user_id)
