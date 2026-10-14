"""
Trends API — metric trend data endpoints.

Endpoints:
  GET /api/trends           — time-series data points for chart
  GET /api/trends/summary   — aggregated trend direction + percentage
"""

import logging
from typing import Any, Dict
from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_active_user
from app.services.trend_service import get_trends, get_trend_summary

router = APIRouter(prefix="/api/trends", tags=["trends"])
logger = logging.getLogger(__name__)


@router.get("")
async def trends_list(
    project: str = Query("", description="Project name filter ('' for global)"),
    days: int = Query(7, description="Lookback window in days"),
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Return time-series data points for trend chart."""
    user_id = current_user.get("user_id")
    return await get_trends(project=project, days=days, user_id=user_id)


@router.get("/summary")
async def trends_summary(
    project: str = Query("", description="Project name filter ('' for global)"),
    days: int = Query(7, description="Lookback window in days"),
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Return trend direction and percentage change for each metric."""
    user_id = current_user.get("user_id")
    return await get_trend_summary(project=project, days=days, user_id=user_id)
