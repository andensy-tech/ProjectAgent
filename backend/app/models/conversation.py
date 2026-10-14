"""
Conversation and message models for chat history.
"""

from datetime import datetime
from sqlalchemy import String, Text, ForeignKey, Integer, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin


class Conversation(Base, TimestampMixin):
    """Chat conversation."""
    
    __tablename__ = "conversations"
    
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=True)
    
    # Messages stored as JSON for flexibility
    # Each message: {role: "user"|"assistant", content: str, type: "text"|"image"|"file", timestamp: str}
    messages: Mapped[dict] = mapped_column(JSON, default=list)
    
    # Working memory snapshot
    context_vars: Mapped[dict] = mapped_column(JSON, default=dict)
    entities: Mapped[dict] = mapped_column(JSON, default=dict)
    
    def __repr__(self) -> str:
        return f"<Conversation {self.id}: {self.title}>"


class Message(Base, TimestampMixin):
    """Individual chat message (alternative to JSON storage)."""
    
    __tablename__ = "messages"
    
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant" | "system"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(20), default="text")  # text | image | file | card | action
    
    # For file/image messages
    file_url: Mapped[str] = mapped_column(String(500), nullable=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=True)
    
    # Extra data (e.g., execution results, card data)
    extra_data: Mapped[dict] = mapped_column(JSON, default=dict)
    
    def __repr__(self) -> str:
        return f"<Message {self.id}: {self.role}>"
