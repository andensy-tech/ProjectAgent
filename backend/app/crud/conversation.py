"""
CRUD operations for Conversation and Message models.
"""

import asyncio
import logging
from typing import List, Optional
from sqlalchemy import select, desc, delete as sa_delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.conversation import Conversation, Message

logger = logging.getLogger(__name__)


# ── Conversation CRUD ────────────────────────────────────────────────────


async def create_conversation(
    db: AsyncSession,
    user_id: int,
    title: Optional[str] = None,
) -> Conversation:
    """Create a new conversation (with retry for SQLite lock contention)."""
    for attempt in range(3):
        try:
            conversation = Conversation(
                user_id=user_id,
                title=title or "新对话",
                messages=[],
                context_vars={},
                entities={},
            )
            db.add(conversation)
            await db.commit()
            await db.refresh(conversation)
            return conversation
        except OperationalError as e:
            if "database is locked" not in str(e):
                raise
            if attempt >= 2:
                logger.warning("create_conversation failed after 3 retries: %s", e)
                raise
            wait = 0.5 * (2 ** attempt)
            logger.info("DB locked creating conversation, retry %d/3 in %.1fs", attempt + 1, wait)
            await asyncio.sleep(wait)
            await db.rollback()


async def get_conversation(
    db: AsyncSession,
    conversation_id: int,
    user_id: Optional[int] = None,
) -> Optional[Conversation]:
    """Get a conversation by ID."""
    stmt = select(Conversation).where(Conversation.id == conversation_id)
    if user_id is not None:
        stmt = stmt.where(Conversation.user_id == user_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_conversations(
    db: AsyncSession,
    user_id: int,
    limit: int = 50,
    offset: int = 0,
) -> List[Conversation]:
    """List conversations for a user."""
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(desc(Conversation.updated_at))
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def update_conversation_title(
    db: AsyncSession,
    conversation_id: int,
    title: str,
) -> Optional[Conversation]:
    """Update conversation title."""
    conversation = await get_conversation(db, conversation_id)
    if conversation:
        conversation.title = title
        await db.commit()
        await db.refresh(conversation)
    return conversation


async def delete_conversation(
    db: AsyncSession,
    conversation_id: int,
    user_id: int,
) -> bool:
    """Delete a conversation and its messages."""
    conversation = await get_conversation(db, conversation_id, user_id)
    if conversation:
        # Must delete child messages first to avoid orphaned rows
        await db.execute(sa_delete(Message).where(Message.conversation_id == conversation_id))
        await db.delete(conversation)
        await db.commit()
        return True
    return False


# Message CRUD

async def create_message(
    db: AsyncSession,
    conversation_id: int,
    role: str,
    content: str,
    message_type: str = "text",
    extra_data: dict = None,
) -> Message:
    """Create a new message (with retry for SQLite lock contention)."""
    for attempt in range(3):
        try:
            message = Message(
                conversation_id=conversation_id,
                role=role,
                content=content,
                message_type=message_type,
                extra_data=extra_data or {},
            )
            db.add(message)
            await db.commit()
            await db.refresh(message)
            return message
        except OperationalError as e:
            if "database is locked" not in str(e):
                raise
            if attempt >= 2:
                logger.warning("create_message failed after 3 retries: %s", e)
                raise
            wait = 0.5 * (2 ** attempt)
            logger.info("DB locked creating message, retry %d/3 in %.1fs", attempt + 1, wait)
            await asyncio.sleep(wait)
            await db.rollback()


async def get_messages(
    db: AsyncSession,
    conversation_id: int,
    limit: int = 100,
    offset: int = 0,
) -> List[Message]:
    """Get messages for a conversation."""
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def add_message_to_conversation_json(
    db: AsyncSession,
    conversation_id: int,
    role: str,
    content: str,
    message_type: str = "text",
    extra_data: dict = None,
) -> Optional[Conversation]:
    """Add a message to conversation's JSON messages array (legacy format)."""
    conversation = await get_conversation(db, conversation_id)
    if not conversation:
        return None
    
    import datetime
    message_entry = {
        "role": role,
        "content": content,
        "type": message_type,
        "timestamp": datetime.datetime.now().isoformat(),
    }
    if extra_data:
        message_entry["extra_data"] = extra_data
    
    if not conversation.messages:
        conversation.messages = []
    conversation.messages.append(message_entry)
    
    await db.commit()
    await db.refresh(conversation)
    return conversation
