"""MCP Capability Service — 统一 MCP 能力入口（所有非 skills 调用方）。

单一事实来源，提供：
  - get_servers()           能力清单（实时读 DB，保存配置即热启动，无需重启）
  - get_tool_definitions()  LLM function calling 工具定义（{server}__{tool}，per-user TTL 缓存）
  - call()                  统一工具调用（ensure client + per-user auth + call_tool）
  - get_auth()              取单个 server 的 per-user auth
  - discover()              全量发现工具并填充缓存（保存配置/预热用）
  - invalidate()            配置变更后失效缓存（热启动关键）

skills/ 目录下的技能脚本有意不经过本服务 —— 它们用 requests 直接调用 MCP
服务器，保持独立可被第三方 agent 执行。本服务仅负责为它们构造 _mcp_config。
"""
import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Tuple

from app.core.exceptions import MCPException
from app.mcp.client import mcp_manager

logger = logging.getLogger(__name__)


class MCPCapabilityService:
    """统一 MCP 能力入口（所有非 skills 调用方）。"""

    def __init__(self, ttl: int = 60):
        self._manager = mcp_manager
        self.ttl = ttl
        self._cache: Dict[int, List[Dict]] = {}       # user_id -> LLM 工具定义
        self._cache_ts: Dict[int, float] = {}
        self._locks: Dict[int, asyncio.Lock] = {}     # per-user 防重入

    # ── 能力清单（实时读 DB，热启动）──────────────────────────────

    async def get_servers(self, user_id) -> Dict[str, Dict]:
        """返回当前用户配置的 MCP 服务器能力清单。

        Returns:
            {"jira": {"url": str, "auth": dict}, ...}
        保留无 auth 但有 url 的服务器（auth 为空 dict），方便发现工具时标记状态。
        """
        from app.services.settings_service import get_mcp_auth_dict
        return await get_mcp_auth_dict(user_id, include_no_auth=True)

    # ── per-user auth ─────────────────────────────────────────────

    async def get_auth(self, server: str, user_id) -> dict:
        """获取 server 的 per-user auth。

        Returns:
            未配置返回 {}（阻止回退到全局 auth）；user_id=None 返回 None。
        """
        if user_id is None:
            return None
        servers = await self.get_servers(user_id)
        cfg = servers.get(server)
        if not cfg:
            return {}
        return cfg.get("auth", {}) or {}

    # ── 统一调用 ──────────────────────────────────────────────────

    async def call(self, server: str, tool: str, params: Dict | None,
                   user_id, timeout: int = 15) -> Any:
        """统一工具调用入口：ensure client + per-user auth + call_tool。

        Args:
            server: MCP 服务器名（如 "jira"）
            tool: MCP 工具名（如 "search_issues"）
            params: 工具参数 dict
            user_id: 用户 ID（决定 per-user auth）
            timeout: 请求超时秒数

        Returns:
            MCPClient.call_tool 的解析结果（dict/str/list）
        Raises:
            MCPException: 服务器未配置/URL 为空时
        """
        servers = await self.get_servers(user_id)
        cfg = servers.get(server)
        if not cfg:
            raise MCPException(f"MCP 服务器未配置或不存在: {server}", server=server)
        url = cfg.get("url", "")
        if not url:
            raise MCPException(f"MCP 服务器未配置 URL: {server}", server=server)
        auth = cfg.get("auth", {}) or {}
        client = self._manager.ensure_client(server, url, timeout=timeout)
        try:
            return await client.call_tool(tool, params or {}, auth_override=auth or None)
        except MCPException as e:
            msg = str(e)
            # 超时错误：明确提示"响应超时"而非含糊的 "Tool call failed"，避免 LLM 误导用户以为是"未配置 MCP"
            if any(k in msg for k in ("Timeout", "ReadTimeout", "timed out", "超时")):
                raise MCPException(
                    f"MCP 服务器 {server} 响应超时（超过 {timeout} 秒），"
                    f"服务器可能繁忙或不可达，请稍后重试或检查该 MCP 服务状态",
                    server=server,
                ) from e
            # 其他错误：用逻辑服务器名重包装（client 层携带的是 server_url，不含逻辑名）
            raise MCPException(msg, server=server) from e

    # ── LLM 工具定义（per-user TTL 缓存）──────────────────────────

    async def get_tool_definitions(self, user_id) -> List[Dict]:
        """获取用户所有 MCP 服务器的工具定义（OpenAI function calling 格式）。

        名称格式 {server}__{tool}。TTL 命中直接返回，否则 per-user lock 防重入后发现。
        """
        if user_id is None:
            return []
        if self._cache_hit(user_id):
            return self._cache[user_id]
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            if self._cache_hit(user_id):
                return self._cache[user_id]
            definitions, _ = await self._discover(user_id, timeout=15)
            self._cache[user_id] = definitions
            self._cache_ts[user_id] = time.time()
            return definitions

    # ── 全量发现（保存配置/预热用）────────────────────────────────

    async def discover(self, user_id, timeout: int = 5) -> Dict[str, Dict]:
        """发现所有服务器工具并填充 per-user 缓存。

        Returns:
            {"jira": {"status": "ok", "tools": 19},
             "gerrit": {"status": "error", "message": "连接超时"}}
        """
        definitions, status = await self._discover(user_id, timeout=timeout)
        self._cache[user_id] = definitions
        self._cache_ts[user_id] = time.time()
        return status

    # ── 热启动关键：配置变更后失效缓存 ────────────────────────────

    async def invalidate(self, user_id) -> None:
        """失效该用户工具缓存，下次 get_tool_definitions 重新发现。"""
        self._cache.pop(user_id, None)
        self._cache_ts.pop(user_id, None)
        self._locks.pop(user_id, None)

    async def close(self) -> None:
        """关闭所有 MCP client 连接。"""
        await self._manager.close_all()

    # ── helpers ───────────────────────────────────────────────────

    def _cache_hit(self, user_id: int) -> bool:
        return (
            user_id in self._cache
            and (time.time() - self._cache_ts.get(user_id, 0)) < self.ttl
        )

    async def _discover(self, user_id, timeout: int) -> Tuple[List[Dict], Dict[str, Dict]]:
        """并行发现所有服务器工具。返回 (工具定义列表, per-server 状态)。"""
        servers = await self.get_servers(user_id)
        definitions: List[Dict] = []
        status: Dict[str, Dict] = {}

        async def _one(name: str, cfg: dict):
            url = cfg.get("url", "")
            auth = cfg.get("auth", {}) or {}
            if not url:
                status[name] = {"status": "error", "message": "URL 为空"}
                return
            try:
                client = self._manager.ensure_client(name, url, timeout=timeout)
                tools = await client.list_tools(auth_override=auth or None)
                status[name] = {"status": "ok", "tools": len(tools or [])}
                for tool in tools or []:
                    tool_name = tool.get("name", "")
                    if not tool_name:
                        continue
                    input_schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
                    definitions.append({
                        "type": "function",
                        "function": {
                            "name": f"{name}__{tool_name}",
                            "description": tool.get("description", f"{name}: {tool_name}"),
                            "parameters": input_schema,
                        }
                    })
            except Exception as e:
                err_msg = str(e)[:100]
                status[name] = {"status": "error", "message": err_msg}
                logger.warning("MCP discover failed for %s: %s", name, e)

        await asyncio.gather(*[_one(n, c) for n, c in servers.items()])
        return definitions, status


# ── Skills 子进程 _mcp_config 构造 ─────────────────────────────────────


def build_mcp_config_dict(servers: Dict[str, Dict]) -> Dict[str, Dict]:
    """将 capability 的 {server: {url, auth}} 转为技能脚本可直接使用的标准 MCP 配置。

    Returns:
        {"jira": {"url": ..., "disabled": False, "timeout": 60,
                  "type": "streamableHttp", "headers": {"Authorization": "..."},
                  "server_url": ...(gerrit)}}
    """
    converted = {}
    for name, cfg in servers.items():
        auth = cfg.get("auth", {}) or {}
        url = cfg.get("url", "")
        if not url:
            continue
        auth_json = json.dumps(auth, ensure_ascii=False) if auth else "{}"
        entry = {
            "url": url,
            "disabled": False,
            "timeout": 60,
            "type": "streamableHttp",
            "headers": {"Authorization": auth_json},
        }
        if "gerrit" in name and auth and "server_url" in auth:
            entry["server_url"] = auth["server_url"]
        converted[name] = entry
    return converted


# 全局单例
capability = MCPCapabilityService()
