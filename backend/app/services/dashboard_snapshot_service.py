"""
Dashboard snapshot service — persists dashboard summary to the database.

Provides:
  - load_latest()       — return cached data (fast)
  - save(data)          — persist fresh results
  - refresh()           — live query + persist
  - load_or_refresh()   — serve cache or auto-refresh
  - any_stale()         — check if snapshot is older than TTL

Snapshots are considered stale after 24 hours (configurable).
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.dashboard_snapshot import DashboardSnapshot
from app.services.dashboard_service import get_dashboard_summary

logger = logging.getLogger(__name__)

SNAPSHOT_TTL_HOURS = 24

# Live refresh timeout (Jira queries can take long)
_REFRESH_TIMEOUT = 120  # seconds

# Serialize writes to avoid SQLite concurrent write contention
_save_lock = asyncio.Lock()


async def load_latest(user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Load the latest dashboard snapshot from DB.

    Args:
        user_id: When provided, filters snapshot for a specific user.

    Returns deserialized data, or None if no snapshot exists.
    """
    async with AsyncSessionLocal() as db:
        stmt = (
            select(DashboardSnapshot)
            .order_by(DashboardSnapshot.id.desc())
            .limit(1)
        )
        if user_id is not None:
            stmt = stmt.where(DashboardSnapshot.user_id == user_id)
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        try:
            data = json.loads(row.data_json)
            data["_queried_at"] = row.queried_at.isoformat()
            return data
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Failed to decode dashboard snapshot: %s", e)
            return None


async def save(data: Dict[str, Any], user_id: Optional[int] = None) -> None:
    """Persist dashboard summary to DB with per-project trend comparison.

    Args:
        data: Dashboard summary dict.
        user_id: When provided, associates snapshot with a specific user.
    """
    async with _save_lock:
        now = datetime.now(timezone.utc)

        current_snapshot = data.pop("_issue_snapshot", {})

        # Load previous snapshot data for trend comparison
        prev_snapshot: Dict[str, Any] = {}
        prev_data = await load_latest(user_id)
        if prev_data:
            prev_snapshot = prev_data.get("_issue_snapshot", {}) or {}

        # Detect format: old flat {issue_key: data} vs new grouped {project: {issue_key: data}}
        def _is_flat(snap: dict) -> bool:
            if not snap:
                return False
            # Old flat format has issue-level fields ("status") at top level
            v = next(iter(snap.values()))
            return isinstance(v, dict) and "status" in v

        # Compute per-project trends (or fall back to global-only for old flat format)
        project_trends: Dict[str, Any] = {}
        global_prev: Dict[str, Dict[str, str]] = {}
        global_current: Dict[str, Dict[str, str]] = {}

        if _is_flat(prev_snapshot) or _is_flat(current_snapshot):
            # Old flat format — skip per-project, compute global only
            global_prev = prev_snapshot if isinstance(prev_snapshot, dict) else {}
            if _is_flat(current_snapshot):
                global_current = current_snapshot  # type: ignore[assignment]
            else:
                # Flatten grouped format to flat
                flat_cur: Dict[str, Dict[str, str]] = {}
                for _v in current_snapshot.values():
                    if isinstance(_v, dict):
                        for _ik, _iv in _v.items():
                            if isinstance(_iv, dict):
                                flat_cur[_ik] = _iv  # type: ignore[assignment]
                global_current = flat_cur
        else:
            # Both are grouped — compute per-project trends
            all_project_names = set(list(current_snapshot.keys()) + list(prev_snapshot.keys()))
            for proj in sorted(all_project_names):
                prev_proj = prev_snapshot.get(proj, {}) if isinstance(prev_snapshot, dict) else {}
                curr_proj = current_snapshot.get(proj, {}) if isinstance(current_snapshot, dict) else {}
                if prev_proj or curr_proj:
                    pt = _compute_trend(prev_proj, curr_proj)
                    project_trends[proj] = pt
                    global_prev.update(prev_proj)
                    global_current.update(curr_proj)

        # Compute global trend too
        trend: Dict[str, Any] = {"new_jiras": [], "removed_jiras": [], "status_changes": []}
        if prev_snapshot and global_current:
            trend = _compute_trend(global_prev, global_current)

        # Build stored data: strip internal keys, then add back trend + snapshot
        store_data: Dict[str, Any] = {k: v for k, v in data.items() if not k.startswith("_")}
        store_data["_trend"] = trend
        store_data["project_trends"] = project_trends
        store_data["_issue_snapshot"] = current_snapshot

        async with AsyncSessionLocal() as db:
            snapshot = DashboardSnapshot(
                data_json=json.dumps(store_data, ensure_ascii=False),
                queried_at=now,
                user_id=user_id,
            )
            db.add(snapshot)
            await db.commit()

        # Mutate original data so caller (e.g. _do_refresh) also has trend
        data["_trend"] = trend
        data["project_trends"] = project_trends
        data["_issue_snapshot"] = current_snapshot

        logger.info("Dashboard snapshot saved (queried_at=%s)", now.isoformat())


def _compute_trend(
    prev: Dict[str, Dict[str, str]],
    current: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    """Compare two issue snapshots and return trend data.

    Args:
        prev: Previous issue snapshot {key: {status, summary, ...}}
        current: Current issue snapshot {key: {status, summary, ...}}

    Returns:
        {
            "summary_delta": {"issues": N, "p1": N, ...},
            "new_jiras": [{key, summary, status, priority, issuetype}, ...],
            "removed_jiras": [{key, summary, status, priority, issuetype}, ...],
            "status_changes": [{key, summary, from, to}, ...],
        }
    """
    prev_keys = set(prev.keys())
    current_keys = set(current.keys())

    new_keys = current_keys - prev_keys
    removed_keys = prev_keys - current_keys
    common_keys = current_keys & prev_keys

    # New jiras
    new_jiras = []
    for k in sorted(new_keys):
        info = current.get(k, {})
        new_jiras.append({
            "key": k,
            "summary": info.get("summary", ""),
            "status": info.get("status", ""),
            "priority": info.get("priority", ""),
            "issuetype": info.get("issuetype", ""),
        })

    # Removed (closed/resolved) jiras
    removed_jiras = []
    for k in sorted(removed_keys):
        info = prev.get(k, {})
        removed_jiras.append({
            "key": k,
            "summary": info.get("summary", ""),
            "status": info.get("status", ""),
            "priority": info.get("priority", ""),
            "issuetype": info.get("issuetype", ""),
        })

    # Status changes
    status_changes = []
    for k in sorted(common_keys):
        prev_status = (prev.get(k) or {}).get("status", "")
        curr_status = (current.get(k) or {}).get("status", "")
        if prev_status and curr_status and prev_status != curr_status:
            status_changes.append({
                "key": k,
                "summary": (current.get(k) or {}).get("summary", ""),
                "from": prev_status,
                "to": curr_status,
            })

    return {
        "new_jiras": new_jiras,
        "removed_jiras": removed_jiras,
        "status_changes": status_changes,
    }


async def any_stale(db: Optional[AsyncSession] = None, ttl_hours: int = SNAPSHOT_TTL_HOURS,
                    user_id: Optional[int] = None) -> bool:
    """Check if the latest snapshot is stale (or no snapshot exists)."""
    if db is not None:
        return await _any_stale_with_session(db, ttl_hours, user_id)
    async with AsyncSessionLocal() as sess:
        return await _any_stale_with_session(sess, ttl_hours, user_id)


async def _any_stale_with_session(db: AsyncSession, ttl_hours: int,
                                  user_id: Optional[int] = None) -> bool:
    """Check staleness with an existing session."""
    stmt = select(DashboardSnapshot)
    if user_id is not None:
        stmt = stmt.where(DashboardSnapshot.user_id == user_id)
    stmt = stmt.limit(1)
    result = await db.execute(stmt)
    first = result.scalar_one_or_none()
    if first is None:
        return True  # no data at all

    stmt = (
        select(DashboardSnapshot)
        .order_by(DashboardSnapshot.queried_at.desc())
        .limit(1)
    )
    if user_id is not None:
        stmt = stmt.where(DashboardSnapshot.user_id == user_id)
    result = await db.execute(stmt)
    newest = result.scalar_one_or_none()
    if newest is None:
        return True
    qa = newest.queried_at
    if qa.tzinfo is None:
        qa = qa.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - qa
    return age > timedelta(hours=ttl_hours)


async def _do_refresh(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Run a live dashboard query, persist result, and return it."""
    logger.info("Starting live dashboard refresh...")
    data = await get_dashboard_summary(user_id=user_id)
    await save(data, user_id)
    logger.info("Dashboard live refresh complete")
    return data


async def _timed_refresh(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Refresh with timeout guard; falls back to empty result on timeout."""
    try:
        return await asyncio.wait_for(
            _do_refresh(user_id),
            timeout=_REFRESH_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("Dashboard refresh timed out after %ss (user_id=%s)",
                       _REFRESH_TIMEOUT, user_id)
        from app.services.dashboard_service import _build_empty_result
        return _build_empty_result(["Jira 查询超时，请稍后重试"])


async def load_or_refresh(*, force_live: bool = False, _refresh: bool = False,
                          user_id: Optional[int] = None) -> Dict[str, Any]:
    """Load from cache, or refresh if requested/stale.

    Supports both shared (user_id=None) and per-user caching.
    When user_id is provided, checks cache specific to that user first.

    Args:
        force_live: Force live query regardless of cache state.
        user_id: When present, uses user-specific config and cache.

    Returns:
        Dashboard summary dict.
    """
    if force_live or _refresh:
        logger.info("Forcing live refresh for dashboard (user_id=%s)", user_id)
        return await _timed_refresh(user_id)

    cached = await load_latest(user_id)
    if cached is not None and not await any_stale(user_id=user_id):
        logger.debug("Serving dashboard from cache (user_id=%s)", user_id)
        return cached

    logger.info("Dashboard cache is stale for user_id=%s, refreshing...", user_id)
    return await _do_refresh(user_id)
