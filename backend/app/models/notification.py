"""
Notification model — 站内消息中心（P-F：monitoring_optimization）。

预警生成/关注集合到期/系统提醒时写入，前端顶栏铃铛 + 红点 + 消息列表展示。
与飞书共用触发逻辑（站内 + 可选即时推送）。
"""

from typing import Optional
from sqlalchemy import String, Text, Boolean, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class Notification(Base, TimestampMixin):
    """站内消息。"""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(20), default="system")  # alert | focus_set | system
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=True)
    link: Mapped[str] = mapped_column(String(500), nullable=True)  # 跳转链接（Jira/看板）
    level: Mapped[str] = mapped_column(String(10), nullable=True)  # HIGH/MEDIUM/LOW
    read: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index("ix_notification_user_read", "user_id", "read"),
    )
