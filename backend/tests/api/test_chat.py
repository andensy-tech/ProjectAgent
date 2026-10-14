"""
API integration tests for Chat endpoints.

Test Cases:
- API-CH-04: GET /api/chat/conversations lists conversations
- API-CH-05: GET /api/chat/conversations/{id} returns detail
- API-CH-06: GET /api/chat/conversations/{id} 404 for missing
- API-CH-07: DELETE /api/chat/conversations/{id} deletes conversation
- API-CH-08: DELETE /api/chat/conversations/{id} 404 for missing
- API-CH-09: Chat endpoints require auth

Note: /api/chat/message (SSE streaming) is tested via integration tests.
      /api/chat/message/sync and /api/chat/confirm were removed in Phase 3 cleanup.
"""

import pytest


class TestListConversations:
    """Test GET /api/chat/conversations (API-CH-04)."""

    @pytest.mark.asyncio
    async def test_list_conversations(self, async_client, db_session):
        """API-CH-04: Returns paginated conversation list."""
        from app.crud.conversation import create_conversation

        await create_conversation(db_session, user_id=1, title="Conv 1")
        await create_conversation(db_session, user_id=1, title="Conv 2")
        await db_session.commit()

        response = await async_client.get("/api/chat/conversations")

        assert response.status_code == 200
        data = response.json()
        assert "conversations" in data
        assert len(data["conversations"]) >= 2

        for conv in data["conversations"]:
            assert "id" in conv
            assert "title" in conv
            assert "created_at" in conv
            assert "updated_at" in conv


class TestGetConversation:
    """Test GET /api/chat/conversations/{id} (API-CH-05~06)."""

    @pytest.mark.asyncio
    async def test_get_conversation_exists(self, async_client, db_session):
        """API-CH-05: Returns conversation with messages."""
        from app.crud.conversation import create_conversation, create_message

        conv = await create_conversation(db_session, user_id=1, title="Detail Test")
        await create_message(db_session, conv.id, "user", "Hello")
        await db_session.commit()

        response = await async_client.get(f"/api/chat/conversations/{conv.id}")

        assert response.status_code == 200
        data = response.json()
        assert "conversation" in data
        assert "messages" in data
        assert data["conversation"]["id"] == conv.id
        assert len(data["messages"]) >= 1

    def test_get_conversation_not_found(self, test_client):
        """API-CH-06: 404 for non-existent conversation."""
        response = test_client.get("/api/chat/conversations/99999")

        assert response.status_code == 404
        assert "detail" in response.json()


class TestDeleteConversation:
    """Test DELETE /api/chat/conversations/{id} (API-CH-07~08)."""

    @pytest.mark.asyncio
    async def test_delete_conversation(self, async_client, db_session):
        """API-CH-07: Deletes existing conversation."""
        from app.crud.conversation import create_conversation

        conv = await create_conversation(db_session, user_id=1, title="To Delete")
        await db_session.commit()

        response = await async_client.delete(f"/api/chat/conversations/{conv.id}")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

    def test_delete_conversation_not_found(self, test_client):
        """API-CH-08: 404 for non-existent conversation."""
        response = test_client.delete("/api/chat/conversations/99999")

        assert response.status_code == 404
        assert "detail" in response.json()


class TestChatMessage:
    """Test POST /api/chat/message (SSE streaming)."""

    @staticmethod
    def _parse_sse(text: str):
        import json
        return [
            json.loads(line[6:])
            for line in text.strip().splitlines()
            if line.startswith("data: ")
        ]

    @pytest.mark.asyncio
    async def test_send_message_streams_response(self, async_client, monkeypatch):
        """SSE 流式返回 text + complete 事件。"""
        from types import SimpleNamespace

        from app.services.agent.types import AgentResponse

        async def fake_process_message(**kwargs):
            yield AgentResponse(type="text", content="你好，这是助手回复")
            yield AgentResponse(type="complete", content="")

        monkeypatch.setattr(
            "app.api.chat.agent_engine",
            SimpleNamespace(process_message=fake_process_message),
        )

        resp = await async_client.post("/api/chat/message", json={"message": "你好"})
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("text/event-stream")

        events = self._parse_sse(resp.text)
        types = [e.get("type") for e in events]
        assert "text" in types
        assert "complete" in types
        # text 事件包含助手回复内容
        text_event = next(e for e in events if e.get("type") == "text")
        assert "助手回复" in text_event.get("content", "")

    @pytest.mark.asyncio
    async def test_send_message_persists_conversation(self, async_client, monkeypatch, db_session):
        """发送消息后自动创建并持久化会话。"""
        from types import SimpleNamespace

        from app.crud.conversation import list_conversations
        from app.services.agent.types import AgentResponse

        async def fake_process_message(**kwargs):
            yield AgentResponse(type="text", content="回复内容")
            yield AgentResponse(type="complete", content="")

        monkeypatch.setattr(
            "app.api.chat.agent_engine",
            SimpleNamespace(process_message=fake_process_message),
        )

        resp = await async_client.post("/api/chat/message", json={"message": "汇总一下"})
        assert resp.status_code == 200

        convs = await list_conversations(db_session, user_id=1)
        assert len(convs) >= 1
        assert convs[0].title.startswith("汇总一下")


class TestChatAuthRequired:
    """Test auth requirement for chat endpoints (API-CH-09)."""

    def test_chat_requires_auth(self, test_client_unauthenticated, monkeypatch):
        """API-CH-09: Chat endpoints reject unauthenticated requests (DEBUG=False)."""
        from app.core.config import settings
        monkeypatch.setattr(settings, "DEBUG", False)

        client = test_client_unauthenticated

        response = client.post("/api/chat/message", json={"message": "test"})
        assert response.status_code == 401

        response = client.get("/api/chat/conversations")
        assert response.status_code == 401

        response = client.get("/api/chat/conversations/1")
        assert response.status_code == 401

        response = client.delete("/api/chat/conversations/1")
        assert response.status_code == 401
