"""
Alert scanner — background task that periodically evaluates risk rules.

Used by main.py lifespan to start a daily scan loop.
Can also be called manually via POST /api/alerts/scan.
"""

import asyncio
import logging
from typing import List

from sqlalchemy import select
from app.models import User
from app.db.session import AsyncSessionLocal
from app.services.alert_service import generate_alerts
from app.services.dashboard_snapshot_service import load_or_refresh as _load_dashboard

logger = logging.getLogger(__name__)

# Scan interval: 24 hours (daily check)
SCAN_INTERVAL_HOURS = 24


async def _get_all_active_user_ids() -> List[int]:
    """Get all active user IDs."""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User.id).where(User.is_active == True))
            return [row[0] for row in result.fetchall()]
    except Exception as e:
        logger.warning("Failed to get active users for alert scan: %s", e)
        return []


async def _scan_for_user(uid: int) -> List[int]:
    """Run alert scan for a single user. Returns newly created/updated alert IDs."""
    try:
        dashboard = await _load_dashboard(user_id=uid)
        if dashboard is None:
            logger.debug("Alert scan: no dashboard data for user %s, skipping", uid)
            return []

        try:
            # Use latest CL cache — same source as CL monitoring page.
            # No force-refresh: alert data should match what the monitoring page shows.
            from app.services.cl_snapshot_service import load_latest_snapshots as _load_cl_cache
            cl_data = await _load_cl_cache(user_id=uid)
        except Exception:
            logger.warning("Alert scan: CL data unavailable for user %s, proceeding with dashboard only", uid)
            cl_data = None

        return await generate_alerts(
            dashboard_summary=dashboard,
            cl_snapshots=cl_data if isinstance(cl_data, list) else None,
            user_id=uid,
        )
    except Exception as e:
        logger.error("Alert scan failed for user %s: %s", uid, e, exc_info=True)
        return []


async def run_alert_scan() -> List[int]:
    """Run a single alert scan cycle for all active users concurrently.

    1. Load latest dashboard summary (from cache, no force-refresh)
    2. Load latest CL snapshots (from cache, no force-refresh)
    3. Evaluate all 5 rules via generate_alerts()
    4. Return list of newly created alert IDs

    Exceptions are caught and logged — never propagated to caller.
    """
    user_ids = await _get_all_active_user_ids()
    if not user_ids:
        logger.warning("Alert scan skipped — no active users")
        return []

    sem = asyncio.Semaphore(5)

    async def _wrapped(uid):
        async with sem:
            return await _scan_for_user(uid)

    results = await asyncio.gather(*(_wrapped(uid) for uid in user_ids))
    all_created = [aid for r in results for aid in r]
    logger.info("Alert scan finished for %d user(s): %d new alert(s)", len(user_ids), len(all_created))
    return all_created
