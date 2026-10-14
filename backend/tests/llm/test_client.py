"""
Tests for app.services.llm.client module.

Test Cases:
- LLM-01: Commercial LLM not configured
- LLM-02: Local LLM not configured
- LLM-03: Normal completion
- LLM-04: JSON mode response
- LLM-05: JSON parse failure
- LLM-06: Streaming completion
- LLM-07: Embedding call
- LLM-08: Embedding failure
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.llm.client import LLMClient
from app.core.exceptions import LLMException


class TestLLMClientConfiguration:
    """Test LLM client configuration (LLM-01~02)."""

    def test_commercial_client_not_configured(self, monkeypatch):
        """LLM-01: Commercial LLM not configured raises LLMException."""
        monkeypatch.setattr("app.services.llm.client.settings.COMMERCIAL_LLM_API_KEY", "")
        client = LLMClient()

        with pytest.raises(LLMException) as exc_info:
            _ = client.commercial_client

        assert "Commercial LLM not configured" in str(exc_info.value)
        assert exc_info.value.provider == "deepseek"

    def test_local_client_not_configured(self, monkeypatch):
        """LLM-02: Local LLM not configured raises LLMException."""
        monkeypatch.setattr("app.services.llm.client.settings.LOCAL_LLM_API_BASE", "")
        client = LLMClient()

        with pytest.raises(LLMException) as exc_info:
            _ = client.local_client

        assert "Local LLM not configured" in str(exc_info.value)
        assert exc_info.value.provider == "local"

    def test_commercial_client_configured(self, monkeypatch):
        """Commercial client available when configured."""
        monkeypatch.setattr("app.services.llm.client.settings.COMMERCIAL_LLM_API_KEY", "test-key")
        monkeypatch.setattr("app.services.llm.client.settings.COMMERCIAL_LLM_API_BASE", "https://api.test.com")
        client = LLMClient()

        assert client._commercial_client is not None

    def test_local_client_configured(self, monkeypatch):
        """Local client available when configured."""
        monkeypatch.setattr("app.services.llm.client.settings.LOCAL_LLM_API_BASE", "https://local.test.com")
        client = LLMClient()

        assert client._local_client is not None


class TestLLMCompletion:
    """Test LLM completion methods (LLM-03~06)."""

    @pytest.fixture
    def client(self, monkeypatch):
        """Create LLMClient with mocked AsyncOpenAI."""
        monkeypatch.setattr("app.services.llm.client.settings.COMMERCIAL_LLM_API_KEY", "test-key")
        monkeypatch.setattr("app.services.llm.client.settings.COMMERCIAL_LLM_API_BASE", "https://api.test.com")
        monkeypatch.setattr("app.services.llm.client.settings.COMMERCIAL_LLM_MODEL", "test-model")
        return LLMClient()

    @pytest.mark.asyncio
    async def test_normal_completion(self, client, monkeypatch):
        """LLM-03: Normal completion returns non-empty string."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello, world!"

        mock_create = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(
            client.commercial_client.chat.completions,
            "create",
            mock_create,
        )

        result = await client.complete(
            messages=[{"role": "user", "content": "Hi"}],
            model_type="commercial",
        )

        assert result == "Hello, world!"
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_json_mode(self, client, monkeypatch):
        """LLM-04: JSON mode returns parseable JSON."""
        json_content = json.dumps({"key": "value"})
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json_content

        mock_create = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(
            client.commercial_client.chat.completions,
            "create",
            mock_create,
        )

        result = await client.complete(
            messages=[{"role": "user", "content": "Return JSON"}],
            model_type="commercial",
            json_mode=True,
        )

        assert result == json_content
        # Check that response_format was passed
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs.get("response_format") == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_json_parse_failure(self, client, monkeypatch):
        """LLM-05: Invalid JSON in structured mode raises LLMException."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "not valid json"

        mock_create = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(
            client.commercial_client.chat.completions,
            "create",
            mock_create,
        )

        with pytest.raises(LLMException) as exc_info:
            await client.complete_structured(
                messages=[{"role": "user", "content": "Test"}],
                model_type="commercial",
            )

        assert "Failed to parse LLM response as JSON" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_streaming_completion(self, client, monkeypatch):
        """LLM-06: Streaming returns complete string."""
        async def mock_stream():
            chunks = [
                MagicMock(choices=[MagicMock(delta=MagicMock(content="Hello"))]),
                MagicMock(choices=[MagicMock(delta=MagicMock(content=", "))]),
                MagicMock(choices=[MagicMock(delta=MagicMock(content="world!"))]),
            ]
            for chunk in chunks:
                yield chunk

        mock_create = AsyncMock(return_value=mock_stream())
        monkeypatch.setattr(
            client.commercial_client.chat.completions,
            "create",
            mock_create,
        )

        result = await client.complete(
            messages=[{"role": "user", "content": "Hi"}],
            model_type="commercial",
            stream=True,
        )

        assert result == "Hello, world!"

    @pytest.mark.asyncio
    async def test_empty_response(self, client, monkeypatch):
        """Empty content returns empty string."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        mock_create = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(
            client.commercial_client.chat.completions,
            "create",
            mock_create,
        )

        result = await client.complete(
            messages=[{"role": "user", "content": "Hi"}],
            model_type="commercial",
        )

        assert result == ""

    @pytest.mark.asyncio
    async def test_completion_with_max_tokens(self, client, monkeypatch):
        """max_tokens is passed to API."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"

        mock_create = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(
            client.commercial_client.chat.completions,
            "create",
            mock_create,
        )

        await client.complete(
            messages=[{"role": "user", "content": "Hi"}],
            model_type="commercial",
            max_tokens=100,
        )

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs.get("max_tokens") == 100


class TestLLMEmbedding:
    """Test embedding functionality (LLM-07~08)."""

    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setattr("app.services.llm.client.settings.COMMERCIAL_LLM_API_KEY", "test-key")
        monkeypatch.setattr("app.services.llm.client.settings.COMMERCIAL_LLM_API_BASE", "https://api.test.com")
        return LLMClient()

    @pytest.mark.asyncio
    async def test_embedding_success(self, client, monkeypatch):
        """LLM-07: Embedding returns vector list."""
        mock_response = MagicMock()
        mock_response.data = [
            MagicMock(embedding=[0.1, 0.2, 0.3]),
            MagicMock(embedding=[0.4, 0.5, 0.6]),
        ]

        mock_create = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(
            client.commercial_client.embeddings,
            "create",
            mock_create,
        )

        result = await client.embed(
            texts=["hello", "world"],
            model_type="commercial",
        )

        assert len(result) == 2
        assert result[0] == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_embedding_failure(self, client, monkeypatch):
        """LLM-08: Embedding failure raises LLMException."""
        mock_create = AsyncMock(side_effect=Exception("API error"))
        monkeypatch.setattr(
            client.commercial_client.embeddings,
            "create",
            mock_create,
        )

        with pytest.raises(LLMException) as exc_info:
            await client.embed(
                texts=["hello"],
                model_type="commercial",
            )

        # _validate_response 保护：本地大模型异常时给出明确中文提示，不静默回退
        assert "商用大模型调用失败" in str(exc_info.value)
        assert "API error" in str(exc_info.value)


class TestCompleteStructured:
    """Test structured completion."""

    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setattr("app.services.llm.client.settings.COMMERCIAL_LLM_API_KEY", "test-key")
        monkeypatch.setattr("app.services.llm.client.settings.COMMERCIAL_LLM_API_BASE", "https://api.test.com")
        return LLMClient()

    @pytest.mark.asyncio
    async def test_structured_with_system_message(self, client, monkeypatch):
        """JSON instruction appended to existing system message."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "ok"}'

        mock_create = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(
            client.commercial_client.chat.completions,
            "create",
            mock_create,
        )

        result = await client.complete_structured(
            messages=[
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Test"},
            ],
            model_type="commercial",
        )

        assert result == {"result": "ok"}
        call_messages = mock_create.call_args[1]["messages"]
        assert "You must respond with valid JSON" in call_messages[0]["content"]

    @pytest.mark.asyncio
    async def test_structured_without_system_message(self, client, monkeypatch):
        """JSON instruction inserted as system message."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "ok"}'

        mock_create = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(
            client.commercial_client.chat.completions,
            "create",
            mock_create,
        )

        result = await client.complete_structured(
            messages=[{"role": "user", "content": "Test"}],
            model_type="commercial",
        )

        call_messages = mock_create.call_args[1]["messages"]
        assert call_messages[0]["role"] == "system"
        assert "valid JSON" in call_messages[0]["content"]
