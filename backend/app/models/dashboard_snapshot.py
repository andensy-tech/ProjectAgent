"""
DashboardSnapshot model — persisted dashboard summary data.

A single row stores the complete dashboard summary JSON.
The latest row is used for fast page loads;
a background daily task refreshes the data.
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import Text, DateTime, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class DashboardSnapshot(Base, TimestampMixin):
    """Persisted snapshot of dashboard summary (per-user)."""

    __tablename__ = "dashboard_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
    queried_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    __table_args__ = {"extend_existing": True}
