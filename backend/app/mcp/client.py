"""
MCP Client - Unified client for MCP servers (Jira, Confluence, Gerrit)

Uses streamableHttp protocol with JSON-RPC 2.0.

MCP server configurations (URL + auth) are NOT loaded from any config file.
They come entirely from per-user DB settings (user_settings table, key="mcp").
Use get_mcp_auth_dict(user_id) in settings_service to resolve user config.
"""
import asyncio
import json
from typing import Dict, List, Optional, Any
import httpx
from app.core.exceptions import MCPException

logger = __import__('logging').getLogger(__name__)


# ── MCP Client ────────────────────────────────────────────────────────────


class MCPClient:
    """Client for MCP streamableHttp servers."""

    def __init__(self, server_url: str, auth: dict, timeout: int = 15):
        self.server_url = server_url
        self.auth = auth  # typed dict, not fragile JSON string
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._tools: List[Dict] = []
        self._request_id = 0

    async def initialize(self, auth_override: Optional[Dict] = None) -> Dict:
        """Initialize MCP connection (non-fatal on failure).

        Some MCP servers (e.g., some custom MCP servers) do not require the
        streamableHttp initialize handshake. If it fails, we log a warning
        and mark as initialized anyway — the server will still accept
        tools/list and tools/call requests.

        Args:
            auth_override: Optional per-request auth dict to use instead of self.auth.
        """
        if self._initialized:
            return {"status": "already_initialized"}

        payload = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "project-agent", "version": "0.1.0"},
            }
        }

        try:
            response = await self._send_request(payload, auth_override=auth_override)
            self._initialized = True
            return response
        except Exception as e:
            logger.warning("MCP initialize failed for %s (non-fatal): %s", self.server_url, e)
            self._initialized = True  # Proceed even if init failed
            return {"status": "init_skipped", "reason": str(e)}

    async def list_tools(self, auth_override: Optional[Dict] = None) -> List[Dict]:
        """List available tools from MCP server.

        Args:
            auth_override: Optional per-request auth dict.
        """
        if not self._initialized:
            async with self._init_lock:
                if not self._initialized:
                    await self.initialize(auth_override=auth_override)

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {},
        }

        try:
            response = await self._send_request(payload, auth_override=auth_override)
            tools = response.get("result", {}).get("tools", [])
            self._tools = tools
            return tools
        except Exception as e:
            err_msg = str(e)
            # 超时明确化：asyncio.TimeoutError 的 str 为空，必须显式提示，
            # 否则"Failed to list tools: "（空消息）会让前端/LLM 误判为服务器不存在
            if isinstance(e, (asyncio.TimeoutError, TimeoutError)) or "timeout" in err_msg.lower():
                err_msg = f"响应超时（超过 {self.timeout} 秒）"
            elif not err_msg:
                err_msg = f"[{type(e).__name__}]"
            raise MCPException(
                f"Failed to list tools: {err_msg}",
                server=self.server_url,
            )

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any] = None,
                        auth_override: Optional[Dict] = None) -> Any:
        """Call an MCP tool.

        Args:
            tool_name: Name of the tool to call.
            arguments: Tool arguments dict.
            auth_override: Optional per-request auth dict (e.g., per-user credentials).
                           Falls back to self.auth if not provided.
        """
        if not self._initialized:
            async with self._init_lock:
                if not self._initialized:
                    await self.initialize(auth_override=auth_override)

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            }
        }

        try:
            response = await self._send_request(payload, auth_override=auth_override)
            return self._parse_tool_result(response)
        except Exception as e:
            err_type = type(e).__name__
            err_msg = str(e) or f"[{err_type}] (no detail)"
            logger.warning("MCP call_tool failed: type=%s server=%s tool=%s msg=%s",
                           err_type, self.server_url, tool_name, err_msg[:150])
            raise MCPException(
                f"Tool call failed: {err_msg}",
                server=self.server_url,
            )

    def _build_auth_header(self, auth_override: Optional[Dict] = None) -> str:
        """将 auth 字典转为 JSON 字符串作为 Authorization header。

        某些 MCP 服务器使用自定义 JSON 格式的 Authorization header
        （非标准 Basic/Bearer），auth 原样序列化为 JSON 字符串后发送。
        兼容格式：{"username":"...","password":"..."} 或 {"token":"..."}

        Args:
            auth_override: Optional per-request auth. Falls back to self.auth.
        """
        auth = auth_override if auth_override is not None else self.auth
        if not auth:
            return ""
        return json.dumps(auth)

    async def ping(self, auth_override: Optional[Dict] = None) -> bool:
        """检查 MCP server 是否可达. 调用 tools/list 作为轻量健康探测."""
        try:
            await self.list_tools(auth_override=auth_override)
            return True
        except Exception as e:
            logger.warning("MCP ping failed for %s: %s", self.server_url, e)
            return False

    async def _send_request(self, payload: Dict, auth_override: Optional[Dict] = None) -> Dict:
        """Send JSON-RPC request to MCP server.

        Args:
            payload: JSON-RPC request payload.
            auth_override: Optional per-request auth dict (passed to _build_auth_header).
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": self._build_auth_header(auth_override=auth_override),
        }

        response = await self._client.post(
            self.server_url,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()

        data = response.json()

        if "error" in data:
            raise MCPException(
                f"MCP error: {data['error']}",
                server=self.server_url,
                details=data["error"],
            )

        return data

    def _parse_tool_result(self, response: Dict) -> Any:
        """Parse tool call result."""
        result = response.get("result", {})
        content = result.get("content", [])

        if not content:
            return None

        # Log warning if MCP flagged an error
        is_error = result.get("isError", False)
        if is_error:
            logger.warning("MCP tool returned isError=true from %s", self.server_url)

        # Combine all text items instead of reading only the first
        text_items = [item.get("text", "") for item in content if item.get("type") == "text"]

        if not text_items:
            return content

        combined = "\n".join(text_items)

        # Try to parse as JSON
        try:
            return json.loads(combined)
        except json.JSONDecodeError:
            return combined

    def _next_id(self) -> int:
        """Get next request ID."""
        self._request_id += 1
        return self._request_id

    async def close(self):
        """Close client connection."""
        await self._client.aclose()


# ── MCP Manager ───────────────────────────────────────────────────────────


class MCPManager:
    """Manages multiple MCP clients.

    Clients are NOT loaded from any config file. They are created on-demand
    via ensure_client() using URL + auth from per-user DB settings.
    """

    def __init__(self):
        self.clients: Dict[str, MCPClient] = {}

    def ensure_client(self, server_name: str, server_url: str,
                      timeout: int = 15) -> MCPClient:
        """Get or create an MCP client by name.

        If a client for this name already exists and its URL matches, returns it.
        Otherwise creates a new MCPClient with the given URL (no auth — auth is
        passed per-request via auth_override).

        Args:
            server_name: Logical name (e.g. "jira", "confluence", or any custom name configured by the user).
            server_url: MCP server URL.
            timeout: HTTP client timeout in seconds.

        Returns:
            The MCPClient instance.
        """
        existing = self.clients.get(server_name)
        if existing:
            if existing.server_url == server_url:
                # Update timeout if requesting longer than current
                if timeout > existing.timeout:
                    old_client = existing._client
                    existing.timeout = timeout
                    existing._client = httpx.AsyncClient(timeout=timeout)
                    # 关闭被替换的旧 client：不关闭的话其 keep-alive 连接
                    # 会一直保持 ESTAB，持续占用对端 MCP 服务器的连接资源
                    try:
                        asyncio.get_running_loop().create_task(old_client.aclose())
                    except RuntimeError:
                        pass  # 无运行中的事件循环（如同步测试环境）
                return existing
            else:
                # URL changed — URL wins (new client replaces old)
                logger.info("MCP client '%s' URL changed: %s -> %s", server_name, existing.server_url, server_url)
        client = MCPClient(server_url=server_url, auth={}, timeout=timeout)
        self.clients[server_name] = client
        return client

    def get_client(self, server_name: str) -> MCPClient:
        """Get MCP client by name. Raises if not created yet."""
        if server_name not in self.clients:
            raise MCPException(
                f"MCP client not found: {server_name}",
                server=server_name,
            )
        return self.clients[server_name]

    async def initialize_all(self):
        """Initialize all MCP clients and verify health."""
        for name, client in self.clients.items():
            try:
                await client.initialize()
                healthy = await client.ping()
                if healthy:
                    print(f"  ✅ MCP client '{name}' initialized and healthy")
                else:
                    print(f"  ⚠️  MCP client '{name}' initialized but ping failed")
            except Exception as e:
                print(f"  ⚠️  Failed to initialize MCP client '{name}': {e}")

    async def close_all(self):
        """Close all MCP clients."""
        for client in self.clients.values():
            await client.close()

    def get_available_servers(self) -> List[str]:
        """Get list of available MCP server names."""
        return list(self.clients.keys())


# Global MCP manager instance
mcp_manager = MCPManager()
