"""
ClMonitorSnapshot model — persisted CL monitoring query results.

Each row stores one snapshot of a project's CL status data.
The latest row per project is used for fast page loads;
a background daily task refreshes the data.
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, Integer, Index, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class ClMonitorSnapshot(Base, TimestampMixin):
    """Persisted snapshot of CL monitoring query results (per-user)."""

    __tablename__ = "cl_monitor_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    project_name: Mapped[str] = mapped_column(String(200), nullable=False)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
    queried_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    __table_args__ = (
        Index("idx_cl_snapshot_project", "project_name"),
        Index("idx_cl_snapshot_user_project", "user_id", "project_name"),
        {"extend_existing": True},
    )
