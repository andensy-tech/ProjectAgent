"""
User model for authentication.
Multi-user with role separation (admin/user).
"""

from typing import Optional
from sqlalchemy import String, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    """User model - supports admin and regular users."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)

    # Force password change on first login (for admin seed and admin-reset)
    force_password_change: Mapped[bool] = mapped_column(Boolean, default=True)

    # Feishu binding: open_id used to identify which feishu user maps to this account
    feishu_open_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True, index=True)

    # User preferences (JSON for flexibility)
    preferences: Mapped[str] = mapped_column(String(2000), default="{}")

    def __repr__(self) -> str:
        return f"<User {self.username}>"
