"""
Skill configuration model.
"""

from sqlalchemy import String, Text, JSON, Boolean, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class SkillConfig(Base, TimestampMixin):
    """Per-user skill parameter overrides.

    Each row stores a user's custom configuration for a skill.
    When executing a skill, the system loads the user's config and merges it with
    the skill's default config.yaml (on disk), with DB values taking precedence.

    Skills are shared across all users, but each user can configure their own
    MCP endpoints, API keys, or other parameters.
    """

    __tablename__ = "skill_configs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Override config as JSON (merged on top of skill's default config.yaml)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)

    description: Mapped[str] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_skillcfg_user_skill", "user_id", "skill_name", unique=True),
    )

    def __repr__(self) -> str:
        return f"<SkillConfig {self.skill_name} user={self.user_id}>"
