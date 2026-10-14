"""
Alert model for risk warnings.
Multi-user: each alert belongs to a user (or is system-generated with user_id=None).

Note: 'project_id' column persists in DB from legacy schema (can't DROP COLUMN in SQLite).
The model still declares it with default=0 so SQLAlchemy INSERT doesn't violate NOT NULL.
Business logic uses project_name + user_id instead.
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, JSON, DateTime, Integer, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class Alert(Base, TimestampMixin):
    """Risk alert record, optionally scoped to a user."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # project_id persists from legacy schema; use project_name for display.
    # Kept with default=0 so INSERT doesn't violate DB NOT NULL constraint.
    project_id: Mapped[int] = mapped_column(Integer, default=0)

    # project_name used for display; no FK to projects table (projects are
    # user-configured via user_settings, not a DB table).
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    project_name: Mapped[str] = mapped_column(String(200), nullable=True, default="")

    # Rule information
    rule_id: Mapped[str] = mapped_column(String(50), nullable=False)
    rule_name: Mapped[str] = mapped_column(String(200), nullable=True)
    category: Mapped[str] = mapped_column(String(30), nullable=False)  # PROGRESS/QUALITY/SCOPE/RESOURCE/COMMUNICATION
    level: Mapped[str] = mapped_column(String(10), nullable=False)  # HIGH/MEDIUM/LOW

    # Alert content
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    impact: Mapped[str] = mapped_column(Text, nullable=True)
    suggestion: Mapped[str] = mapped_column(Text, nullable=True)

    # Evidence chain
    evidence: Mapped[list] = mapped_column(JSON, default=list)

    # Status: ACTIVE/ACKNOWLEDGED/RESOLVED/IGNORED
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str] = mapped_column(String(100), nullable=True)
    resolution_note: Mapped[str] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # Composite index for dedup queries: rule_id + project_name + status
        # and for user-scoped alert filtering.
        Index("ix_alerts_rule_user_status", "rule_id", "user_id", "status"),
        {"extend_existing": True},
    )

    def __repr__(self) -> str:
        return f"<Alert {self.rule_id}: {self.title}>"
