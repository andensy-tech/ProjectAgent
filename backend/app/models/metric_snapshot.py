"""
MetricSnapshot model — periodic metric snapshots for trend visualization.

Each row captures a point-in-time snapshot of dashboard metrics for a project
(or global summary with project_name=""). Collected every 5 minutes by
a background task in main.py.
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, Integer, Index, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class MetricSnapshot(Base, TimestampMixin):
    """Point-in-time metric snapshot for trend analysis."""

    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    project_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    snapshot_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    total_issues: Mapped[int] = mapped_column(default=0)
    p0_count: Mapped[int] = mapped_column(default=0)
    stale_count: Mapped[int] = mapped_column(default=0)
    eta_expiring: Mapped[int] = mapped_column(default=0)
    unmerged_cls: Mapped[int] = mapped_column(default=0)
    extra_metrics: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_snap_project_time", "project_name", "snapshot_time"),
        {"extend_existing": True},
    )
