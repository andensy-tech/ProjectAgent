"""
Memory system models for user profiles, project memories, and learning records.
"""

from datetime import datetime
from sqlalchemy import String, Text, JSON, ForeignKey, DateTime, DECIMAL, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class UserProfile(Base, TimestampMixin):
    """User profile for long-term memory."""

    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=True)  # 项目经理 / 开发经理等
    
    # Preferences as JSON arrays
    preferred_projects: Mapped[list] = mapped_column(JSON, default=list)
    preferred_skills: Mapped[list] = mapped_column(JSON, default=list)
    
    communication_style: Mapped[str] = mapped_column(String(50), default="professional")
    alert_threshold: Mapped[str] = mapped_column(String(20), default="medium")
    
    # Learned preferences (dynamic)
    learned_preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    
    def __repr__(self) -> str:
        return f"<UserProfile {self.user_id}>"


class ProjectMemory(Base, TimestampMixin):
    """Project memory for long-term storage."""
    
    __tablename__ = "project_memories"
    
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    
    # Key project information
    key_milestones: Mapped[list] = mapped_column(JSON, default=list)
    key_decisions: Mapped[list] = mapped_column(JSON, default=list)
    risk_history: Mapped[list] = mapped_column(JSON, default=list)
    team_members: Mapped[list] = mapped_column(JSON, default=list)
    
    def __repr__(self) -> str:
        return f"<ProjectMemory {self.project_id}>"


class LearningRecord(Base, TimestampMixin):
    """Learning record for evolution."""

    __tablename__ = "learning_records"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # What was learned
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=True)

    # Performance metrics
    success_rate: Mapped[float] = mapped_column(DECIMAL(5, 2), default=0)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<LearningRecord {self.pattern[:50]}...>"


class MemoryLog(Base, TimestampMixin):
    """Daily session log entry for memory maintenance.

    Each row records one conversation turn. These logs feed into the
    periodic distillation process that extracts valuable patterns into
    learning_records and learned_preferences.
    """

    __tablename__ = "memory_logs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # "2026-06-01"
    session_id: Mapped[str] = mapped_column(String(100), nullable=True)
    turn_index: Mapped[int] = mapped_column(Integer, default=0)

    # Turn content summary
    user_message_preview: Mapped[str] = mapped_column(String(500), nullable=True)
    assistant_summary: Mapped[str] = mapped_column(String(500), nullable=True)
    key_topics: Mapped[list] = mapped_column(JSON, default=list)
    tool_usage: Mapped[list] = mapped_column(JSON, default=list)

    # Distillation tracking
    distilled: Mapped[bool] = mapped_column(Boolean, default=False)

    def __repr__(self) -> str:
        return f"<MemoryLog {self.date} turn#{self.turn_index} distilled={self.distilled}>"


class SessionMemory(Base, TimestampMixin):
    """Session memory for current login session."""

    __tablename__ = "session_memories"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    session_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Session state
    active_project: Mapped[str] = mapped_column(String(100), nullable=True)
    active_config: Mapped[str] = mapped_column(String(100), nullable=True)
    
    # Cached skill results
    skill_results: Mapped[dict] = mapped_column(JSON, default=dict)
    
    # Session preferences (temporary overrides)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    
    # Expiration
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    
    def __repr__(self) -> str:
        return f"<SessionMemory {self.session_id}>"
