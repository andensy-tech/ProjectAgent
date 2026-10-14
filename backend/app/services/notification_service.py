"""
Notification 服务 — 站内消息中心（P-F：monitoring_optimization）。

预警生成 / 关注集合到期 / 系统提醒时写入，前端铃铛红点 + 消息列表。
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.notification import Notification

logger = __import__("logging").getLogger(__name__)


async def create_notification(
    user_id: int,
    title: str,
    content: Optional[str] = None,
    notif_type: str = "system",
    link: Optional[str] = None,
    level: Optional[str] = None,
) -> int:
    """创建一条站内消息。"""
    async with AsyncSessionLocal() as db:
        notif = Notification(
            user_id=user_id,
            type=notif_type,
            title=title,
            content=content,
            link=link,
            level=level,
            read=False,
        )
        db.add(notif)
        await db.commit()
        await db.refresh(notif)
        return notif.id


async def list_notifications(user_id: int, limit: int = 50,
                             unread_only: bool = False) -> List[Dict[str, Any]]:
    """列出用户的站内消息。"""
    async with AsyncSessionLocal() as db:
        stmt = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(Notification.read.is_(False))
        rows = (await db.execute(
            stmt.order_by(Notification.created_at.desc()).limit(limit)
        )).scalars().all()
        return [
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "content": n.content,
                "link": n.link,
                "level": n.level,
                "read": n.read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in rows
        ]


async def unread_count(user_id: int) -> int:
    """未读消息数。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(func.count(Notification.id)).where(
                Notification.user_id == user_id,
                Notification.read.is_(False),
            )
        )
        return result.scalar() or 0


async def mark_read(user_id: int, notification_id: int) -> bool:
    """标记单条已读。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(Notification)
            .where(Notification.id == notification_id,
                   Notification.user_id == user_id)
            .values(read=True)
        )
        await db.commit()
        return result.rowcount > 0


async def mark_all_read(user_id: int) -> int:
    """标记全部已读，返回更新的条数。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.read.is_(False))
            .values(read=True)
        )
        await db.commit()
        return result.rowcount or 0
