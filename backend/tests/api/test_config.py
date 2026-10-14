"""
API integration tests for Configuration endpoints.

Test Cases:
- API-C-01: GET /api/config/llm returns masked config
- API-C-02: GET /api/config/mcp returns server URLs
- API-C-03: GET /api/config/system returns app info
- API-C-04: Config endpoints require auth
"""

import json

import pytest


class TestConfigLLM:
    """Test GET /api/config/llm (API-C-01)."""

    def test_get_llm_config(self, test_client):
        """API-C-01: Returns LLM config with masked API keys."""
        response = test_client.get("/api/config/llm")

        assert response.status_code == 200
        data = response.json()
        assert "commercial" in data
        assert "local" in data
        assert "routing" in data
        # API keys should be masked
        assert data["commercial"]["api_key"] in ("***", "")
        assert "provider" in data["commercial"]
        assert "model" in data["commercial"]


class TestConfigMCP:
    """Test GET /api/config/mcp (API-C-02)."""

    @pytest.mark.asyncio
    async def test_get_mcp_config(self, async_client, db_session):
        """API-C-02: Returns the user's configured MCP servers with masked passwords.

        Mirrors a real user flow: the user saved MCP servers via the settings
        page (stored as UserSetting mcp JSON), then GET returns them.
        """
        from app.models import UserSetting

        mcp_payload = {
            "mcpServers": {
                "jira": {
                    "url": "http://jira.example/mcp",
                    "headers": {"Authorization": json.dumps({"username": "u", "password": "secret"})},
                },
                "confluence": {"url": "http://confluence.example/mcp"},
                "gerrit_gerrit1": {"url": "http://gerrit.example/mcp"},
                "gerrit_gerrit2": {"url": "http://gerrit2.example/mcp"},
            }
        }
        db_session.add(UserSetting(user_id=1, key="mcp", value=json.dumps(mcp_payload)))
        await db_session.commit()

        response = await async_client.get("/api/config/mcp")

        assert response.status_code == 200
        data = response.json()
        servers = data["mcpServers"]
        assert "jira" in servers
        assert "confluence" in servers
        assert "gerrit_gerrit1" in servers
        assert "gerrit_gerrit2" in servers
        assert "url" in servers["jira"]
        # Password must never leak in the response
        assert "secret" not in json.dumps(data)
        assert "***" in json.dumps(servers["jira"].get("headers", {}))


class TestConfigSystem:
    """Test GET /api/config/system (API-C-03)."""

    def test_get_system_config(self, test_client):
        """API-C-03: Returns system configuration."""
        response = test_client.get("/api/config/system")

        assert response.status_code == 200
        data = response.json()
        assert "app_name" in data
        assert "app_version" in data
        assert "debug" in data
        assert isinstance(data["debug"], bool)


class TestConfigAuthRequired:
    """Test auth requirement for config endpoints (API-C-04)."""

    def test_config_requires_auth(self, test_client_unauthenticated, monkeypatch):
        """API-C-04: Config endpoints reject unauthenticated requests (DEBUG=False)."""
        from app.core.config import settings
        monkeypatch.setattr(settings, "DEBUG", False)

        client = test_client_unauthenticated

        for endpoint in ["/api/config/llm", "/api/config/mcp", "/api/config/system"]:
            response = client.get(endpoint)
            assert response.status_code == 401, f"{endpoint} should require auth"


class TestConfigProject:
    """GET/POST /api/config/project (API-C-05~06)."""

    @pytest.mark.asyncio
    async def test_get_default_project(self, async_client):
        """新用户无配置时返回默认项目配置结构。"""
        resp = await async_client.get("/api/config/project")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    @pytest.mark.asyncio
    async def test_save_project_config(self, async_client):
        """保存项目配置后返回保存结果。"""
        payload = {"name": "AML123", "version": "v1", "jira_url": "https://jira.example.com"}
        resp = await async_client.post("/api/config/project", json=payload)
        assert resp.status_code == 200
        assert resp.json().get("name") == "AML123"

    @pytest.mark.asyncio
    async def test_project_config_roundtrip(self, async_client):
        """保存后重新读取，配置持久化。"""
        await async_client.post("/api/config/project", json={"name": "AML123"})
        resp = await async_client.get("/api/config/project")
        assert resp.status_code == 200
        assert resp.json().get("name") == "AML123"


class TestConfigCiBuild:
    """GET/POST /api/config/ci_build（敏感字段脱敏与 *** 保留旧值）。"""

    _payload = {
        "branch": "android-u-tv-test-release",
        "target": "CHIPX1",
        "ota_flavor": "URT2",
        "default_build_type": "userdebug",
        "auth": {
            "enabled": True,
            "client_id": "cid.apps.googleusercontent.com",
            "client_secret": "super-secret-client-secret",
            "project_id": "",
        },
        "ftp": {
            "host": "ftp.example.com",
            "port": 21,
            "username": "ftpuser",
            "password": "super-secret-ftp-password",
            "remote_dir": "/ci_builds",
        },
    }

    @pytest.mark.asyncio
    async def test_get_ci_build_default(self, async_client):
        """新用户无配置时返回默认结构。"""
        resp = await async_client.get("/api/config/ci_build")
        assert resp.status_code == 200
        data = resp.json()
        assert data["branch"] == ""
        assert data["default_build_type"] == "userdebug"
        assert data["ftp"]["port"] == 21

    @pytest.mark.asyncio
    async def test_ci_build_masking(self, async_client):
        """GET 返回时 client_secret / FTP 密码必须脱敏为 ***。"""
        resp = await async_client.post("/api/config/ci_build", json=self._payload)
        assert resp.status_code == 200

        resp = await async_client.get("/api/config/ci_build")
        assert resp.status_code == 200
        data = resp.json()
        assert data["auth"]["client_secret"] == "***"
        assert data["ftp"]["password"] == "***"
        # 明文绝不允许出现在响应里
        assert "super-secret-client-secret" not in json.dumps(data)
        assert "super-secret-ftp-password" not in json.dumps(data)
        # 非敏感字段原样返回
        assert data["auth"]["client_id"] == "cid.apps.googleusercontent.com"
        assert data["ftp"]["host"] == "ftp.example.com"

    @pytest.mark.asyncio
    async def test_ci_build_placeholder_keeps_old_value(self, async_client):
        """POST 时敏感字段传 "***" 保留旧值（不回写 ***）。"""
        await async_client.post("/api/config/ci_build", json=self._payload)

        update = {
            "branch": "new-branch",
            "target": "CHIPX1",
            "auth": {"enabled": True, "client_id": "cid.apps.googleusercontent.com",
                     "client_secret": "***", "project_id": ""},
            "ftp": {"host": "ftp.example.com", "port": 21, "username": "ftpuser",
                    "password": "***", "remote_dir": "/new_dir"},
        }
        resp = await async_client.post("/api/config/ci_build", json=update)
        assert resp.status_code == 200
        data = resp.json()
        assert data["branch"] == "new-branch"
        assert data["ftp"]["remote_dir"] == "/new_dir"
        assert data["auth"]["client_secret"] == "***"
        assert data["ftp"]["password"] == "***"

        # 后端明文存储仍是原值（工具进程内使用）
        from app.services.settings_service import get_ci_build_config_raw
        raw = await get_ci_build_config_raw(1)
        assert raw["auth"]["client_secret"] == "super-secret-client-secret"
        assert raw["ftp"]["password"] == "super-secret-ftp-password"
