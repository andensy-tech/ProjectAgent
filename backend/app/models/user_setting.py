"""
UserSetting model — per-user key-value settings storage.

Replaces the global user_settings.json file for multi-user support.
Keys: "dashboard", "cl_monitoring", "llm", "mcp", etc.
Values: JSON-encoded dicts.
"""

from sqlalchemy import String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class UserSetting(Base, TimestampMixin):
    """Per-user settings stored as key-value pairs."""

    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-encoded

    def __repr__(self) -> str:
        return f"<UserSetting user={self.user_id} key={self.key}>"
