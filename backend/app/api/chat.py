"""
Chat API - Main interaction endpoint for Agent.
Supports conversation persistence via database.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_active_user, get_current_db_user
from app.models import User
from app.api.upload import get_uploaded_file_info
from app.db import get_db
from app.db.session import AsyncSessionLocal
from app.services.agent import agent_engine
from app.services.agent.types import AgentResponse
from app.crud.conversation import (
    create_conversation,
    get_conversation,
    list_conversations,
    update_conversation_title,
    delete_conversation,
    create_message,
    get_messages,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)

# Beijing timezone (UTC+8)
_BJS_TZ = timezone(timedelta(hours=8))


def _as_bjs_iso(dt: Optional[datetime]) -> Optional[str]:
    """Convert naive datetime (from SQLite) to Beijing-time ISO string.

    SQLite's CURRENT_TIMESTAMP stores UTC without timezone info.
    We treat naive datetimes as UTC, convert to Asia/Shanghai, then
    produce a timezone-aware ISO string so the frontend can parse correctly.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BJS_TZ).isoformat()


class ChatMessageRequest(BaseModel):
    """Chat message request."""
    conversation_id: Optional[int] = None
    message: str
    message_type: str = "text"  # text | image | file
    file_id: Optional[str] = None


@router.post("/message")
async def send_message(
    request: ChatMessageRequest,
    current_user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Send a message to the Agent and get streaming response.
    Automatically creates conversations and persists messages.
    """
    user_id = current_user.id

    async def event_generator():
        conv_id = request.conversation_id
        first_message = True

        # Use a separate DB session for message persistence to avoid sharing
        # the injected `db` session with the agent engine (which may do its own
        # memory DB operations). Sharing the same session can cause SQLite
        # "database is locked" errors when concurrent writes overlap.
        async with AsyncSessionLocal() as msg_db:
            # Resolve file attachment if file_id is provided
            file_attachments = None
            if request.file_id:
                file_info = get_uploaded_file_info(request.file_id)
                if file_info:
                    file_attachments = [file_info]

            try:
                async for response in agent_engine.process_message(
                    message=request.message,
                    conversation_id=str(conv_id) if conv_id else None,
                    user_id=user_id,
                    db=db,
                    file_attachments=file_attachments,
                ):
                    # Yield response to client FIRST — DB writes must not block SSE
                    yield f"data: {response_to_json(response, conv_id)}\n\n"

                    # THEN perform DB writes (create conversation, save messages)
                    # using a dedicated mesage_db session, separate from the
                    # engine's db session to avoid SQLite write lock contention.
                    if first_message and response.type != "error":
                        if not conv_id:
                            title = request.message[:100]
                            new_conv = await create_conversation(
                                msg_db, user_id=current_user.id, title=title
                            )
                            conv_id = new_conv.id

                        # Save user message (both new and existing conversations)
                        await create_message(
                            msg_db,
                            conversation_id=conv_id,
                            role="user",
                            content=request.message,
                            message_type=request.message_type,
                        )

                        first_message = False

                    # Save assistant messages (only meaningful content)
                    # file: Agent 产出的文件卡片（含签名下载链接），刷新会话后仍可下载
                    if conv_id and response.type in ("text", "card", "file"):
                        await create_message(
                            msg_db,
                            conversation_id=conv_id,
                            role="assistant",
                            content=response.content or "",
                            message_type=response.type,
                            extra_data=response.data or {},
                        )
            except Exception as e:
                logger.error("SSE generator error: %s", e, exc_info=True)
                yield f"data: {response_to_json(AgentResponse(type='error', content=f'处理出错: {str(e)}'), conv_id)}\n\n"
                yield f"data: {response_to_json(AgentResponse(type='complete'), conv_id)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )


@router.get("/conversations")
async def list_user_conversations(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db),
):
    """List user's conversations."""
    convs = await list_conversations(db, user_id=current_user.id, limit=limit, offset=offset)
    return {
        "conversations": [
            {
                "id": c.id,
                "title": c.title or "新对话",
                "created_at": _as_bjs_iso(c.created_at),
                "updated_at": _as_bjs_iso(c.updated_at),
            }
            for c in convs
        ]
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation_detail(
    conversation_id: int,
    current_user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific conversation with its messages."""
    conv = await get_conversation(db, conversation_id, user_id=current_user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = await get_messages(db, conversation_id=conversation_id)

    return {
        "conversation": {
            "id": conv.id,
            "title": conv.title or "新对话",
            "created_at": _as_bjs_iso(conv.created_at),
        },
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "type": m.message_type,
                "data": m.extra_data or {},
                "created_at": _as_bjs_iso(m.created_at),
            }
            for m in messages
        ],
    }


@router.delete("/conversations/{conversation_id}")
async def delete_user_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a conversation."""
    success = await delete_conversation(db, conversation_id, user_id=current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted"}


def response_to_json(response: AgentResponse, conversation_id: int = None) -> str:
    """Convert AgentResponse to JSON string."""
    import json
    data = {
        "type": response.type,
        "content": response.content,
        "data": response.data,
        "actions": response.actions,
    }
    if conversation_id:
        data["conversation_id"] = conversation_id
    return json.dumps(data, ensure_ascii=False)
