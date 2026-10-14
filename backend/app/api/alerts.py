"""
Alerts API — risk alert management endpoints.

Endpoints:
  GET    /api/alerts          — list alerts (with filters)
  GET    /api/alerts/{id}     — single alert detail
  PATCH  /api/alerts/{id}/status — update alert status
  POST   /api/alerts/scan     — manually trigger a scan
  GET    /api/alerts/summary  — aggregated counts
"""

import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import get_current_active_user
from app.services.alert_service import (
    list_alerts,
    get_alert,
    update_alert_status,
    get_alert_summary,
    generate_alerts,
)
from app.tasks.alert_scanner import run_alert_scan

router = APIRouter(prefix="/api/alerts", tags=["alerts"])
logger = logging.getLogger(__name__)


class AlertStatusUpdate(BaseModel):
    status: str  # ACKNOWLEDGED / RESOLVED / IGNORED
    resolution_note: Optional[str] = None


def _alert_to_dict(alert: Any) -> Dict[str, Any]:
    """Convert an Alert ORM object to a dict for JSON response."""
    return {
        "id": alert.id,
        "user_id": alert.user_id,
        "project_name": alert.project_name,
        "rule_id": alert.rule_id,
        "rule_name": alert.rule_name,
        "category": alert.category,
        "level": alert.level,
        "title": alert.title,
        "description": alert.description,
        "impact": alert.impact,
        "suggestion": alert.suggestion,
        "evidence": alert.evidence,
        "status": alert.status,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "resolved_by": alert.resolved_by,
        "resolution_note": alert.resolution_note,
        "created_at": alert.created_at.isoformat() if hasattr(alert, 'created_at') and alert.created_at else None,
        "updated_at": alert.updated_at.isoformat() if hasattr(alert, 'updated_at') and alert.updated_at else None,
    }


@router.get("")
async def alerts_list(
    status: str = Query("", description="Filter by status"),
    level: str = Query("", description="Filter by level (HIGH/MEDIUM/LOW)"),
    category: str = Query("", description="Filter by category (PROGRESS/QUALITY/SCOPE)"),
    project_name: str = Query("", description="Filter by project name"),
    limit: int = Query(50, description="Max results"),
    offset: int = Query(0, description="Result offset"),
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """List alerts with optional filters.

    Non-admin users only see their own alerts; admin sees all.
    """
    is_admin = current_user.get("is_superuser", False)
    user_id = current_user.get("user_id") if not is_admin else None
    alerts, total = await list_alerts(
        status=status or None,
        level=level or None,
        category=category or None,
        project_name=project_name or None,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )
    return {
        "alerts": [_alert_to_dict(a) for a in alerts],
        "total": total,
    }


@router.get("/summary")
async def alerts_summary(
    project_name: str = Query("", description="Filter by project name"),
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Return aggregated alert counts by status, level, and category.

    Non-admin users only see their own alerts; admin sees all.
    """
    is_admin = current_user.get("is_superuser", False)
    user_id = current_user.get("user_id") if not is_admin else None
    return await get_alert_summary(project_name=project_name or None, user_id=user_id)


@router.post("/scan")
async def alerts_manual_scan(
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Manually trigger a full alert scan cycle."""
    logger.info("Manual alert scan triggered by user '%s'", current_user.get("username", "?"))
    created = await run_alert_scan()
    return {"created": len(created), "alert_ids": created}


async def _assert_alert_ownership(alert_id: int, current_user: dict) -> None:
    """Check that the user has access to this alert (admin can see all)."""
    is_admin = current_user.get("is_superuser", False)
    if is_admin:
        return
    alert = await get_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="告警不存在")
    if alert.user_id is not None and alert.user_id != current_user.get("user_id"):
        raise HTTPException(status_code=403, detail="无权访问此告警")


@router.get("/{alert_id}")
async def alerts_detail(
    alert_id: int,
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Get a single alert by ID."""
    await _assert_alert_ownership(alert_id, current_user)
    alert = await get_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="告警不存在")
    return _alert_to_dict(alert)


@router.patch("/{alert_id}/status")
async def alerts_update_status(
    alert_id: int,
    body: AlertStatusUpdate,
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Update alert status (ACKNOWLEDGED / RESOLVED / IGNORED)."""
    await _assert_alert_ownership(alert_id, current_user)

    valid_statuses = {"ACKNOWLEDGED", "RESOLVED", "IGNORED"}
    if body.status.upper() not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"无效状态: {body.status}，可选: {', '.join(valid_statuses)}")

    username = current_user.get("username", "unknown")
    alert = await update_alert_status(
        alert_id,
        status=body.status.upper(),
        resolution_note=body.resolution_note,
        resolved_by=username,
    )
    if alert is None:
        raise HTTPException(status_code=404, detail="告警不存在")
    return _alert_to_dict(alert)
