"""
Tests for app.core.config module.

Test Cases:
- CFG-01: Default configuration values
- CFG-02: Environment variable loading
- CFG-03: Missing required config handling
"""

import os
import pytest
from pydantic_settings import BaseSettings


class TestConfig:
    """Test configuration management."""

    def test_default_config_values(self):
        """CFG-01: All config items have sensible defaults."""
        from app.core.config import Settings

        settings = Settings()

        assert settings.APP_NAME == "Project Agent"
        assert settings.APP_VERSION == "0.1.0"
        assert settings.DEBUG is True
        assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 60 * 24 * 7
        assert settings.COMMERCIAL_LLM_PROVIDER == "deepseek"
        assert settings.COMMERCIAL_LLM_MODEL == "deepseek-chat"
        assert settings.UPLOAD_DIR == "uploads"
        assert settings.MAX_UPLOAD_SIZE == 50 * 1024 * 1024

    def test_env_override(self, monkeypatch):
        """CFG-02: Environment variables correctly override defaults."""
        from app.core.config import Settings

        monkeypatch.setenv("APP_NAME", "Test Agent")
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("COMMERCIAL_LLM_MODEL", "gpt-4")

        settings = Settings()

        assert settings.APP_NAME == "Test Agent"
        assert settings.DEBUG is False
        assert settings.COMMERCIAL_LLM_MODEL == "gpt-4"

    def test_database_url_default(self):
        """CFG-01: Default database URL is set（开发默认 SQLite）。"""
        from app.core.config import Settings

        settings = Settings()
        assert settings.DATABASE_URL != ""
        assert settings.DATABASE_URL.startswith("sqlite")

    def test_secret_key_default(self):
        """CFG-01: Secret key has default value."""
        from app.core.config import Settings

        settings = Settings()
        assert settings.SECRET_KEY == "change-me-in-production"

    def test_optional_fields_types(self):
        """CFG-03: Optional fields have correct types."""
        from app.core.config import Settings

        settings = Settings()
        assert isinstance(settings.LOCAL_LLM_API_BASE, str)
        assert isinstance(settings.LOCAL_LLM_API_KEY, str)

    def test_data_masking_level(self):
        """CFG-01: Data masking level defaults to normal."""
        from app.core.config import Settings

        settings = Settings()
        assert settings.DATA_MASKING_LEVEL == "normal"
        assert settings.ENABLE_AUDIT_LOG is True

    def test_mcp_config_not_from_yaml(self):
        """CFG-03: MCP config no longer loads from config.yaml.

        2026-06-24 起 MCP 服务器 URL/凭据彻底移除 config.yaml 依赖，
        统一按用户从 DB user_settings(key="mcp") 读取。已删除 load_mcp_config。
        """
        import app.mcp.client as mcp_client_module
        assert not hasattr(mcp_client_module, "load_mcp_config"), \
            "load_mcp_config 已废弃，MCP 配置从用户 DB 设置读取"
