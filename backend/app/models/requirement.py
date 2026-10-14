"""
Requirement knowledge base models.
"""

from datetime import datetime
from sqlalchemy import String, Text, JSON, ForeignKey, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class Requirement(Base, TimestampMixin):
    """Requirement record in knowledge base."""
    
    __tablename__ = "requirements"
    
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    req_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    
    # Requirement content
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    req_type: Mapped[str] = mapped_column(String(20), nullable=False)  # NEW/CHANGE/CONFIRM/FEEDBACK
    priority: Mapped[str] = mapped_column(String(10), nullable=True)  # P0/P1/P2/P3
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")  # DRAFT/CONFIRMING/CONFIRMED/REJECTED/IMPLEMENTED
    
    # Source information
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)  # JIRA/WECHAT/EMAIL/TEAMS/DOC/MEETING/VERBAL
    source_ref: Mapped[str] = mapped_column(String(200), nullable=True)
    source_file: Mapped[str] = mapped_column(String(500), nullable=True)
    raised_by: Mapped[str] = mapped_column(String(100), nullable=True)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Vector search
    vector_id: Mapped[str] = mapped_column(String(100), nullable=True)
    
    # Extra metadata
    extra_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    
    def __repr__(self) -> str:
        return f"<Requirement {self.req_id}: {self.title}>"


class RequirementChange(Base, TimestampMixin):
    """Requirement change history."""
    
    __tablename__ = "requirement_changes"
    
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), nullable=False)
    
    change_type: Mapped[str] = mapped_column(String(20), nullable=False)  # ADD/MODIFY/DELETE/CONFIRM/REJECT
    field_name: Mapped[str] = mapped_column(String(50), nullable=True)
    old_value: Mapped[str] = mapped_column(Text, nullable=True)
    new_value: Mapped[str] = mapped_column(Text, nullable=True)
    
    source_type: Mapped[str] = mapped_column(String(30), nullable=True)
    source_ref: Mapped[str] = mapped_column(String(200), nullable=True)
    changed_by: Mapped[str] = mapped_column(String(100), nullable=True)
    
    def __repr__(self) -> str:
        return f"<RequirementChange {self.change_type}>"


class RequirementRelation(Base, TimestampMixin):
    """Relationship between requirements."""
    
    __tablename__ = "requirement_relations"
    
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    from_req_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), nullable=False)
    to_req_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), nullable=False)
    
    relation_type: Mapped[str] = mapped_column(String(30), nullable=False)  # DEPENDS_ON/RELATED_TO/DUPLICATES/SUPERSEDES
    
    def __repr__(self) -> str:
        return f"<RequirementRelation {self.from_req_id} -> {self.to_req_id}>"
