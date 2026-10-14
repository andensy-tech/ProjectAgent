"""
Notifications API — 站内消息中心（P-F）。

Endpoints:
  GET  /api/notifications              — 消息列表
  GET  /api/notifications/unread-count — 未读数（顶栏红点）
  POST /api/notifications/{id}/read    — 标记单条已读
  POST /api/notifications/read-all     — 全部已读
"""

from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException
from fastapi import Query as FQ

from app.api.deps import get_current_active_user
from app.services.notification_service import (
    list_notifications,
    mark_all_read,
    mark_read,
    unread_count,
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
async def notifications_list(
    limit: int = FQ(50, le=100),
    unread_only: bool = FQ(False),
    current_user: dict = Depends(get_current_active_user),
) -> List[Dict[str, Any]]:
    """消息列表。"""
    user_id = current_user.get("user_id")
    return await list_notifications(user_id, limit=limit, unread_only=unread_only)


@router.get("/unread-count")
async def notifications_unread_count(
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, int]:
    """未读数。"""
    user_id = current_user.get("user_id")
    return {"count": await unread_count(user_id)}


@router.post("/{notification_id}/read")
async def notifications_mark_read(
    notification_id: int,
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """标记单条已读。"""
    user_id = current_user.get("user_id")
    ok = await mark_read(user_id, notification_id)
    if not ok:
        raise HTTPException(status_code=404, detail="消息不存在")
    return {"success": True}


@router.post("/read-all")
async def notifications_mark_all_read(
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """全部已读。"""
    user_id = current_user.get("user_id")
    return {"success": True, "marked": await mark_all_read(user_id)}
