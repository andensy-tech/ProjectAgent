"""
Tests for app.services.llm.router module.

Test Cases:
- RTR-01: INTENT_PARSING → commercial
- RTR-02: RISK_ANALYSIS → commercial
- RTR-03: DATA_PARSING → local
- RTR-04: TEXT_FORMATTING → local
- RTR-05: Custom routing override
- RTR-06: Unknown task defaults to commercial
- RTR-07: route_and_complete calls correct client
- RTR-08: route_and_complete_structured returns JSON
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.llm.router import LLMRouter, TaskType, DEFAULT_ROUTING


class TestRoutingRules:
    """Test routing rule assignments (RTR-01~06)."""

    def test_intent_parsing_routing(self):
        """RTR-01: INTENT_PARSING routes to commercial."""
        router = LLMRouter()
        assert router.get_provider(TaskType.INTENT_PARSING) == "commercial"

    def test_risk_analysis_routing(self):
        """RTR-02: RISK_ANALYSIS routes to commercial."""
        router = LLMRouter()
        assert router.get_provider(TaskType.RISK_ANALYSIS) == "commercial"

    def test_data_parsing_routing(self):
        """RTR-03: DATA_PARSING routes to local."""
        router = LLMRouter()
        assert router.get_provider(TaskType.DATA_PARSING) == "local"

    def test_text_formatting_routing(self):
        """RTR-04: TEXT_FORMATTING routes to local."""
        router = LLMRouter()
        assert router.get_provider(TaskType.TEXT_FORMATTING) == "local"

    def test_custom_routing_override(self, monkeypatch):
        """RTR-05: Custom routing configuration overrides defaults."""
        # Settings doesn't have LLM_ROUTING attribute, so we patch the router directly
        router = LLMRouter()
        router.routing_rules[TaskType.INTENT_PARSING] = "local"
        assert router.get_provider(TaskType.INTENT_PARSING) == "local"

    def test_unknown_task_defaults_commercial(self):
        """RTR-06: Unknown task type defaults to commercial."""
        router = LLMRouter()
        # Create a fake task type not in routing rules
        assert router.get_provider("unknown_task") == "commercial"

    def test_all_semantic_tasks_commercial(self):
        """All semantic understanding tasks route to commercial."""
        router = LLMRouter()
        semantic_tasks = [
            TaskType.INTENT_PARSING,
            TaskType.RISK_ANALYSIS,
            TaskType.REPORT_GENERATION,
            TaskType.REQUIREMENT_UNDERSTANDING,
            TaskType.QUESTION_ANSWERING,
            TaskType.SUMMARIZATION,
        ]
        for task in semantic_tasks:
            assert router.get_provider(task) == "commercial"

    def test_all_data_tasks_local(self):
        """All data processing tasks route to local."""
        router = LLMRouter()
        data_tasks = [
            TaskType.DATA_PARSING,
            TaskType.TEXT_FORMATTING,
            TaskType.STRUCTURE_EXTRACTION,
            TaskType.OCR_POSTPROCESS,
            TaskType.DATA_MASKING,
            TaskType.EMBEDDING,
        ]
        for task in data_tasks:
            assert router.get_provider(task) == "local"


class TestRoutingExecution:
    """Test routing execution methods (RTR-07~08)."""

    @pytest.fixture
    def router(self):
        return LLMRouter()

    @pytest.mark.asyncio
    async def test_route_and_complete(self, router, monkeypatch):
        """RTR-07: route_and_complete calls correct client."""
        mock_complete = AsyncMock(return_value="response text")
        monkeypatch.setattr(
            "app.services.llm.router.llm_client.complete",
            mock_complete,
        )

        result = await router.route_and_complete(
            task_type=TaskType.INTENT_PARSING,
            messages=[{"role": "user", "content": "test"}],
        )

        assert result == "response text"
        mock_complete.assert_called_once()
        call_kwargs = mock_complete.call_args[1]
        assert call_kwargs["model_type"] == "commercial"

    @pytest.mark.asyncio
    async def test_route_and_complete_local(self, router, monkeypatch):
        """route_and_complete routes data task to local."""
        mock_complete = AsyncMock(return_value="local response")
        monkeypatch.setattr(
            "app.services.llm.router.llm_client.complete",
            mock_complete,
        )

        await router.route_and_complete(
            task_type=TaskType.DATA_PARSING,
            messages=[{"role": "user", "content": "test"}],
        )

        call_kwargs = mock_complete.call_args[1]
        assert call_kwargs["model_type"] == "local"

    @pytest.mark.asyncio
    async def test_route_and_complete_structured(self, router, monkeypatch):
        """RTR-08: route_and_complete_structured returns parsed JSON."""
        mock_structured = AsyncMock(return_value={"key": "value"})
        monkeypatch.setattr(
            "app.services.llm.router.llm_client.complete_structured",
            mock_structured,
        )

        result = await router.route_and_complete_structured(
            task_type=TaskType.INTENT_PARSING,
            messages=[{"role": "user", "content": "test"}],
        )

        assert result == {"key": "value"}
        mock_structured.assert_called_once()

    @pytest.mark.asyncio
    async def test_route_and_complete_with_params(self, router, monkeypatch):
        """Additional parameters are passed through."""
        mock_complete = AsyncMock(return_value="ok")
        monkeypatch.setattr(
            "app.services.llm.router.llm_client.complete",
            mock_complete,
        )

        await router.route_and_complete(
            task_type=TaskType.SUMMARIZATION,
            messages=[{"role": "user", "content": "test"}],
            temperature=0.5,
            max_tokens=200,
            model="custom-model",
        )

        call_kwargs = mock_complete.call_args[1]
        assert call_kwargs["temperature"] == 0.5
        assert call_kwargs["max_tokens"] == 200
        assert call_kwargs["model"] == "custom-model"


class TestRoutingInfo:
    """Test routing info retrieval."""

    def test_get_routing_info(self):
        """get_routing_info returns all mappings."""
        router = LLMRouter()
        info = router.get_routing_info()

        assert isinstance(info, dict)
        assert "intent_parsing" in info
        assert "data_parsing" in info
        assert info["intent_parsing"] == "commercial"
        assert info["data_parsing"] == "local"
