"""
组件测试：Gerrit 查询工具的纯逻辑部分（app/tools/gerrit_tool.py）。

用户场景：web chat 查询 CL 合并状态。认证信息与 REST URL 解析是查询的
前置步骤，这里用真实生产函数验证解析逻辑（无需网络）。
"""

from app.tools.gerrit_tool import (
    _build_auth_header,
    _get_gerrit_client_name,
    _get_rest_base,
    _resolve_auth,
)


class TestGetGerritClientName:
    """实例名 → MCP 客户端名规范化（gerrit_{hostname}）。"""

    def test_standard_instance(self):
        # host 经实例映射表解析为 MCP 客户端名
        assert _get_gerrit_client_name("gerrit-2.example.com") == "gerrit_gerrit2"

    def test_second_instance(self):
        assert _get_gerrit_client_name("gerrit-1.example.com") == "gerrit_gerrit1"

    def test_unknown_instance_passthrough(self):
        # 未知实例名直接作为后缀，保证命名约定一致
        assert _get_gerrit_client_name("custom") == "gerrit_custom"


class TestBuildAuthHeader:
    """HTTP Basic Auth 头构建。"""

    def test_basic_auth_encoding(self):
        header = _build_auth_header("user1", "secret")
        # Base64("user1:secret")
        import base64
        assert header["Authorization"] == "Basic " + base64.b64encode(b"user1:secret").decode()


class TestGetRestBase:
    """Gerrit REST API 基地址解析。"""

    def test_prefers_server_url(self):
        # 真实 Gerrit Web URL 优先于 MCP 代理 URL
        assert _get_rest_base("http://10.0.0.1:8118/mcp", "https://gerrit-2.example.com") == \
            "https://gerrit-2.example.com"

    def test_strips_mcp_suffix(self):
        # 无 server_url 时从 MCP URL 剥离 /mcp
        assert _get_rest_base("http://10.0.0.1:8118/mcp", "") == "http://10.0.0.1:8118"

    def test_no_trailing_slash(self):
        assert _get_rest_base("http://x:8080/mcp/", "") == "http://x:8080"


class TestResolveAuth:
    """从用户 MCP 配置提取 Gerrit 认证信息。"""

    def test_exact_match(self):
        params = {"instance": "gerrit1"}
        mcp_auth = {
            "gerrit_gerrit1": {
                "url": "http://10.0.0.1:8115/mcp",
                "auth": {
                    "server_url": "https://gerrit-1.example.com",
                    "username": "test.user",
                    "password": "p@ss",
                },
            },
        }
        base_url, username, password, server_url = _resolve_auth(params, mcp_auth)
        assert base_url == "http://10.0.0.1:8115/mcp"
        assert server_url == "https://gerrit-1.example.com"
        assert username == "test.user"
        assert password == "p@ss"

    def test_fuzzy_match_nonstandard_name(self):
        # 用户配置为非标准名（含实例名）应被模糊匹配到
        params = {"instance": "gerrit2"}
        mcp_auth = {
            "gerrit_gerrit2-custom": {
                "url": "http://10.0.0.1:8118/mcp",
                "auth": {"username": "u", "password": "p", "server_url": "https://gerrit-2.example.com"},
            },
        }
        base_url, username, password, server_url = _resolve_auth(params, mcp_auth)
        assert base_url == "http://10.0.0.1:8118/mcp"
        assert username == "u"

    def test_no_config_returns_empty(self):
        params = {"instance": "gerrit1"}
        base_url, username, password, server_url = _resolve_auth(params, {})
        assert (base_url, username, password, server_url) == ("", "", "", "")

    def test_legacy_flat_auth(self):
        # 旧格式：auth 直接用 username/password
        params = {"instance": "gerrit1"}
        mcp_auth = {
            "gerrit_gerrit1": {
                "url": "http://x/mcp",
                "auth": {"user": "legacy", "pass": "old"},
            },
        }
        base_url, username, password, server_url = _resolve_auth(params, mcp_auth)
        assert username == "legacy"
        assert password == "old"
