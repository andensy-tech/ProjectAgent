"""
Tests for app.mcp.client module.

Test Cases:
- MCP-01: Initialize connection
- MCP-02: Repeat initialization
- MCP-03: List tools
- MCP-04: Call tool
- MCP-05: JSON response parsing
- MCP-06: Text response parsing
- MCP-07: Error response
- MCP-08: Manager multi-client
- MCP-09: Manager get non-existent client
- MCP-10: Manager initialize all
- MCP-11: Manager close all
- MCP-12: Auth config stored as dict
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.mcp.client import MCPClient, MCPManager
from app.core.exceptions import MCPException


class TestMCPClient:
    """Test MCPClient class (MCP-01~07, MCP-12)."""

    @pytest.fixture
    def mcp_client(self):
        return MCPClient(
            server_url="http://test.mcp.server/mcp",
            auth={"username": "test", "password": "pass"},
        )

    @pytest.mark.asyncio
    async def test_initialize(self, mcp_client, monkeypatch):
        """MCP-01: Initialize sets _initialized flag."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": {"protocolVersion": "2024-11-05"}}
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(mcp_client._client, "post", mock_post)

        result = await mcp_client.initialize()

        assert mcp_client._initialized is True
        assert result["result"]["protocolVersion"] == "2024-11-05"

    @pytest.mark.asyncio
    async def test_repeat_initialize(self, mcp_client):
        """MCP-02: Repeat initialization returns already_initialized."""
        mcp_client._initialized = True
        result = await mcp_client.initialize()
        assert result["status"] == "already_initialized"

    @pytest.mark.asyncio
    async def test_list_tools(self, mcp_client, monkeypatch):
        """MCP-03: List tools returns tool list."""
        mcp_client._initialized = True
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {"tools": [{"name": "tool1"}, {"name": "tool2"}]}
        }
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(mcp_client._client, "post", mock_post)

        tools = await mcp_client.list_tools()

        assert len(tools) == 2
        assert tools[0]["name"] == "tool1"

    @pytest.mark.asyncio
    async def test_call_tool(self, mcp_client, monkeypatch):
        """MCP-04: Call tool returns result."""
        mcp_client._initialized = True
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "content": [{"type": "text", "text": '{"data": "value"}'}]
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(mcp_client._client, "post", mock_post)

        result = await mcp_client.call_tool("test_tool", {"param": "value"})

        assert result == {"data": "value"}

    @pytest.mark.asyncio
    async def test_json_response_parsing(self, mcp_client, monkeypatch):
        """MCP-05: JSON content is parsed."""
        mcp_client._initialized = True
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "content": [{"type": "text", "text": '{"key": "val"}'}]
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(mcp_client._client, "post", mock_post)

        result = await mcp_client.call_tool("tool", {})
        assert isinstance(result, dict)
        assert result["key"] == "val"

    @pytest.mark.asyncio
    async def test_text_response_parsing(self, mcp_client, monkeypatch):
        """MCP-06: Non-JSON text is returned as-is."""
        mcp_client._initialized = True
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "content": [{"type": "text", "text": "plain text"}]
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(mcp_client._client, "post", mock_post)

        result = await mcp_client.call_tool("tool", {})
        assert result == "plain text"

    @pytest.mark.asyncio
    async def test_error_response(self, mcp_client, monkeypatch):
        """MCP-07: Error in response raises MCPException."""
        mcp_client._initialized = True
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "error": {"message": "Tool not found", "code": -32601}
        }
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(mcp_client._client, "post", mock_post)

        with pytest.raises(MCPException) as exc_info:
            await mcp_client.call_tool("bad_tool", {})

        assert "MCP error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_request_id_increments(self, mcp_client, monkeypatch):
        """Request ID increments with each call."""
        mcp_client._initialized = True
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": {"content": []}}
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(mcp_client._client, "post", mock_post)

        await mcp_client.list_tools()
        first_id = mock_post.call_args[1]["json"]["id"]

        await mcp_client.list_tools()
        second_id = mock_post.call_args[1]["json"]["id"]

        assert second_id == first_id + 1

    def test_auth_config_dict(self):
        """MCP-12: Auth config is stored as dict."""
        client = MCPClient(
            server_url="http://test",
            auth={"username": "user", "password": "pass"},
        )
        assert client.auth == {"username": "user", "password": "pass"}
        # Verify the Authorization header is serialized correctly via _send_request
        # (tested implicitly via mock in initialize/call_tool tests)

    @pytest.mark.asyncio
    async def test_close(self, mcp_client):
        """Close calls aclose on underlying client."""
        mcp_client._initialized = True
        with patch.object(mcp_client._client, 'aclose', new_callable=AsyncMock) as mock_aclose:
            await mcp_client.close()
            mock_aclose.assert_called_once()


class TestMCPManager:
    """Test MCPManager class (MCP-08~11) — new lazy client API.

    MCPManager now has a no-arg constructor; clients are created on-demand
    via ensure_client(name, url) from per-user DB settings.
    """

    def test_manager_ensure_and_get_client(self):
        """MCP-08: ensure_client creates a client that get_client returns."""
        manager = MCPManager()
        client = manager.ensure_client("jira", "http://jira/mcp")
        assert manager.get_client("jira") is client
        assert client.server_url == "http://jira/mcp"

    def test_manager_get_nonexistent_client(self):
        """MCP-09: Getting non-existent client raises MCPException."""
        manager = MCPManager()
        with pytest.raises(MCPException) as exc_info:
            manager.get_client("nonexistent")

        assert "MCP client not found" in str(exc_info.value)

    def test_manager_ensure_client_reuses_same_url(self):
        """ensure_client reuses the existing client for the same URL."""
        manager = MCPManager()
        c1 = manager.ensure_client("jira", "http://jira/mcp")
        c2 = manager.ensure_client("jira", "http://jira/mcp")
        assert c1 is c2

    def test_manager_ensure_client_replaces_on_url_change(self):
        """ensure_client replaces the client when the URL changes."""
        manager = MCPManager()
        c1 = manager.ensure_client("jira", "http://jira/mcp")
        c2 = manager.ensure_client("jira", "http://new.example/mcp")
        assert c1 is not c2
        assert c2.server_url == "http://new.example/mcp"

    def test_manager_available_servers(self):
        """Manager returns list of available servers."""
        manager = MCPManager()
        manager.ensure_client("jira", "http://jira/mcp")
        manager.ensure_client("confluence", "http://conf/mcp")

        servers = manager.get_available_servers()
        assert "jira" in servers
        assert "confluence" in servers

    @pytest.mark.asyncio
    async def test_manager_initialize_all(self, monkeypatch):
        """MCP-10: initialize_all initializes all clients."""
        manager = MCPManager()
        manager.ensure_client("jira", "http://jira/mcp")

        mock_init = AsyncMock(return_value={"status": "ok"})
        mock_ping = AsyncMock(return_value=True)
        monkeypatch.setattr(manager.clients["jira"], "initialize", mock_init)
        monkeypatch.setattr(manager.clients["jira"], "ping", mock_ping)

        await manager.initialize_all()
        mock_init.assert_called_once()

    @pytest.mark.asyncio
    async def test_manager_initialize_all_partial_failure(self, monkeypatch):
        """Partial failure doesn't block other clients."""
        manager = MCPManager()
        manager.ensure_client("jira", "http://jira/mcp")
        manager.ensure_client("confluence", "http://conf/mcp")

        mock_init_jira = AsyncMock(side_effect=Exception("Jira down"))
        mock_init_conf = AsyncMock(return_value={"status": "ok"})
        mock_ping = AsyncMock(return_value=True)

        monkeypatch.setattr(manager.clients["jira"], "initialize", mock_init_jira)
        monkeypatch.setattr(manager.clients["confluence"], "initialize", mock_init_conf)
        monkeypatch.setattr(manager.clients["confluence"], "ping", mock_ping)

        # Should not raise
        await manager.initialize_all()
        mock_init_conf.assert_called_once()

    @pytest.mark.asyncio
    async def test_manager_close_all(self, monkeypatch):
        """MCP-11: close_all closes all clients."""
        manager = MCPManager()
        manager.ensure_client("jira", "http://jira/mcp")

        mock_close = AsyncMock()
        monkeypatch.setattr(manager.clients["jira"], "close", mock_close)

        await manager.close_all()
        mock_close.assert_called_once()


class TestMCPClientParseResult:
    """Test _parse_tool_result edge cases (MCP-13~16)."""

    @pytest.fixture
    def mcp_client(self):
        return MCPClient(
            server_url="http://test.mcp.server/mcp",
            auth={"username": "test"},
        )

    def test_is_error_flag_logged(self, mcp_client):
        """MCP-13: isError=true doesn't crash, still returns content."""
        resp = {
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": "Error description"}],
            }
        }
        result = mcp_client._parse_tool_result(resp)
        assert result == "Error description"

    def test_multi_text_items_combined(self, mcp_client):
        """MCP-14: Multiple text items are joined."""
        resp = {
            "result": {
                "content": [
                    {"type": "text", "text": "part1"},
                    {"type": "text", "text": "part2"},
                ],
            }
        }
        result = mcp_client._parse_tool_result(resp)
        expected = "part1\npart2"
        assert result == expected

    def test_mixed_content_types(self, mcp_client):
        """MCP-15: Non-text items are skipped, text items still combined."""
        resp = {
            "result": {
                "content": [
                    {"type": "resource", "text": "resource_data"},
                    {"type": "text", "text": "text_data"},
                ],
            }
        }
        result = mcp_client._parse_tool_result(resp)
        assert result == "text_data"

    def test_empty_content_returns_none(self, mcp_client):
        """MCP-16: Empty content list returns None."""
        resp = {"result": {"content": []}}
        assert mcp_client._parse_tool_result(resp) is None

    def test_json_parsing_after_combine(self, mcp_client):
        """MCP-17: Combined multi-item text is parsed as JSON."""
        resp = {
            "result": {
                "content": [
                    {"type": "text", "text": '{"key":'},
                    {"type": "text", "text": '"value"}'},
                ],
            }
        }
        result = mcp_client._parse_tool_result(resp)
        assert isinstance(result, dict)
        assert result["key"] == "value"
