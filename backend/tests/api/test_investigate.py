"""
API integration tests for the AI investigation endpoint (app/api/investigate.py).

  POST /api/investigate — create a conversation and run the agent

The agent engine (LLM) is mocked; conversation persistence runs against the
real DB through the real HTTP endpoint.
"""

import pytest


class TestInvestigate:
    """POST /api/investigate."""

    @pytest.mark.asyncio
    async def test_start_investigation(self, async_client, monkeypatch):
        """提交调查请求并返回调查结果。"""
        from types import SimpleNamespace

        from app.services.agent.types import AgentResponse

        async def fake_process_message(**kwargs):
            yield AgentResponse(type="text", content="初步调查结果")

        monkeypatch.setattr(
            "app.api.investigate.agent_engine",
            SimpleNamespace(process_message=fake_process_message),
        )

        resp = await async_client.post(
            "/api/investigate",
            json={"message": "调查一下质量告警"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_investigation_with_context(self, async_client, monkeypatch):
        """带 context_data 的调查请求。"""
        from types import SimpleNamespace

        from app.services.agent.types import AgentResponse

        async def fake_process_message(**kwargs):
            yield AgentResponse(type="card", content="指标卡", data={"key": "value"})

        monkeypatch.setattr(
            "app.api.investigate.agent_engine",
            SimpleNamespace(process_message=fake_process_message),
        )

        resp = await async_client.post(
            "/api/investigate",
            json={"context_type": "metric", "context_data": {"metric": "total_issues"}},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_investigation_error_response(self, async_client, monkeypatch):
        """Agent 返回 error 时调查端点仍正常返回（不 500）。"""
        from types import SimpleNamespace

        from app.services.agent.types import AgentResponse

        async def fake_process_message(**kwargs):
            yield AgentResponse(type="error", content="调查失败")

        monkeypatch.setattr(
            "app.api.investigate.agent_engine",
            SimpleNamespace(process_message=fake_process_message),
        )

        resp = await async_client.post("/api/investigate", json={"message": "调查"})
        assert resp.status_code == 200
