"""
FeishuBindCode — 飞书账号绑定验证码模型

用于飞书账号与 Web 账号的绑定流程：
1. Web 端发起绑定 → 生成 6 位验证码存入此表
2. 用户将验证码发送给飞书机器人 → 验证通过后更新 User.feishu_open_id
"""

from datetime import datetime
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class FeishuBindCode(Base):
    """飞书绑定验证码表"""

    __tablename__ = "feishu_bind_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(6), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
