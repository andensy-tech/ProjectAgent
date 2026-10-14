"""
Trend service — periodic metric collection + trend data queries.

Provides:
  - collect_snapshot()    — called every 5 min: fetch dashboard + CL data, persist to DB
  - get_trends()          — time-series points for chart rendering
  - get_trend_summary()   — aggregated trend direction + percentage change
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.metric_snapshot import MetricSnapshot
from app.services.dashboard_service import get_dashboard_summary

logger = logging.getLogger(__name__)


# ── Snapshot collection ──────────────────────────────────────────────────────


async def collect_snapshot(user_id: Optional[int] = None) -> None:
    """Collect current dashboard + CL metrics and persist a snapshot.

    Called every 5 minutes from main.py lifespan background task.
    Errors are logged and suppressed (never propagated).

    数据源优先用 dashboard 缓存快照（由每小时的后台刷新任务维护），
    避免每 5 分钟对 Jira MCP 发起实时查询——高频轮询曾把内网
    MCP 服务器拖死。仅当没有任何缓存时才实时查询（首次使用）。

    Args:
        user_id: If provided, collects snapshot using that user's config.
    """
    dashboard: Optional[Dict[str, Any]] = None
    try:
        from app.services.dashboard_snapshot_service import load_latest as _load_dash
        dashboard = await _load_dash(user_id=user_id)
    except Exception as e:
        logger.warning("Trend snapshot: dashboard cache load failed: %s", e)
    if dashboard is None:
        try:
            dashboard = await get_dashboard_summary(user_id=user_id)
        except Exception as e:
            logger.warning("Trend snapshot: dashboard fetch failed: %s", e)
            return

    # Load CL snapshot for unmerged_cls per project
    unmerged_by_project: Dict[str, int] = {}
    try:
        from app.services.cl_snapshot_service import load_latest_snapshots as load_cl
        cl_snapshots = await load_cl(user_id=user_id)
        for snap in cl_snapshots:
            try:
                data = json.loads(snap.data_json) if hasattr(snap, 'data_json') else {}
                if isinstance(data, dict):
                    unmerged_by_project[snap.project_name] = data.get('unmerged_cls', 0)
            except (json.JSONDecodeError, TypeError):
                pass
    except Exception as e:
        logger.warning("Trend snapshot: CL data load failed: %s", e)

    now = datetime.now(timezone.utc)
    total = dashboard.get("total", {})
    total_unmerged = sum(unmerged_by_project.values())

    async with AsyncSessionLocal() as db:
        # Global snapshot (project_name="")
        db.add(MetricSnapshot(
            project_name="",
            user_id=user_id,
            snapshot_time=now,
            total_issues=total.get("issues", 0),
            p0_count=total.get("p0", 0),
            stale_count=total.get("stale", 0),
            eta_expiring=total.get("eta_expiring", 0),
            unmerged_cls=total_unmerged,
        ))

        # Per-project snapshots
        for proj in dashboard.get("projects", []):
            if not proj.get("enabled", True):
                continue
            db.add(MetricSnapshot(
                project_name=proj["name"],
                user_id=user_id,
                snapshot_time=now,
                total_issues=proj.get("issues", 0),
                p0_count=proj.get("p0", 0),
                stale_count=proj.get("stale", 0),
                eta_expiring=proj.get("eta_expiring", 0),
                unmerged_cls=unmerged_by_project.get(proj["name"], 0),
            ))

        await db.commit()

        # Best-effort cleanup: delete snapshots older than 90 days
        try:
            cutoff = now - timedelta(days=90)
            from sqlalchemy import delete as sa_delete
            stmt_del = sa_delete(MetricSnapshot).where(MetricSnapshot.snapshot_time < cutoff)
            await db.execute(stmt_del)
            await db.commit()
        except Exception:
            await db.rollback()  # cleanup is best-effort

    logger.debug("Trend snapshot collected at %s", now.isoformat())


# ── Trend data queries ───────────────────────────────────────────────────────

_SERIES_KEYS = ["total_issues", "p0_count", "stale_count", "eta_expiring", "unmerged_cls"]


async def get_trends(project: str = "", days: int = 7,
                     user_id: Optional[int] = None) -> Dict[str, Any]:
    """Return time-series data points for trend chart.

    Args:
        project: Project name to filter ("" for global).
        days: Number of days of history to include.
        user_id: If provided, only returns data for this user.

    Returns:
        {"project": str, "days": int, "points": [{snapshot_time, total_issues, ...}]}
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    async with AsyncSessionLocal() as db:
        conditions = [
            MetricSnapshot.project_name == project,
            MetricSnapshot.snapshot_time >= since,
        ]
        if user_id is not None:
            conditions.append(MetricSnapshot.user_id == user_id)
        stmt = (
            select(MetricSnapshot)
            .where(*conditions)
            .order_by(MetricSnapshot.snapshot_time.asc())
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()

    points: List[Dict[str, Any]] = []
    for row in rows:
        st = row.snapshot_time
        if st.tzinfo is None:
            st = st.replace(tzinfo=timezone.utc)
        points.append({
            "snapshot_time": st.isoformat(),
            "total_issues": row.total_issues,
            "p0_count": row.p0_count,
            "stale_count": row.stale_count,
            "eta_expiring": row.eta_expiring,
            "unmerged_cls": row.unmerged_cls,
        })

    # Deduplicate by date (keep last point per day if many points exist)
    if len(points) > days * 2:
        points = _deduplicate_points(points)

    return {"project": project, "days": days, "points": points}


async def get_trend_summary(project: str = "", days: int = 7,
                            user_id: Optional[int] = None) -> Dict[str, Any]:
    """Return trend direction and percentage change for each metric.

    Compares the first and last data points in the window.

    Returns:
        {
            "project": str, "days": int,
            "metrics": {
                "total_issues": {"direction": "↑", "percent": 5.2, "start": 100, "end": 105},
                "p0_count": {...},
                ...
            }
        }
    """
    data = await get_trends(project=project, days=days, user_id=user_id)
    points = data.get("points", [])

    if len(points) < 2:
        return _empty_summary(project, days)

    first = points[0]
    last = points[-1]

    metrics: Dict[str, Any] = {}
    for key in _SERIES_KEYS:
        start_val = first.get(key, 0)
        end_val = last.get(key, 0)
        if start_val == 0:
            direction = "→" if end_val == 0 else "↑"
            percent = 100.0 if end_val > 0 else 0.0
        else:
            pct = round((end_val - start_val) / start_val * 100, 1)
            direction = "↑" if pct > 1 else ("↓" if pct < -1 else "→")
            percent = pct
        metrics[key] = {
            "direction": direction,
            "percent": percent,
            "start": start_val,
            "end": end_val,
        }

    return {
        "project": project,
        "days": days,
        "point_count": len(points),
        "metrics": metrics,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────


def _deduplicate_points(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """If there are many points, keep last-per-day to reduce chart noise."""
    by_day: Dict[str, Dict[str, Any]] = {}
    for p in points:
        day = (p.get("snapshot_time") or "")[:10]
        by_day[day] = p  # last write wins
    return list(by_day.values())


def _empty_summary(project: str, days: int) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    for key in _SERIES_KEYS:
        metrics[key] = {"direction": "→", "percent": 0.0, "start": 0, "end": 0}
    return {
        "project": project,
        "days": days,
        "point_count": 0,
        "metrics": metrics,
    }
