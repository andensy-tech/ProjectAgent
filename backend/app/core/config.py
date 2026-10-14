"""
Application configuration management.
Uses pydantic-settings for environment variable loading.
"""

import os
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field


# Find .env: check current dir, then parent dir (project root)
_env_path = Path(".env")
if not _env_path.exists():
    _env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    APP_NAME: str = "Project Agent"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = Field(default=False, alias="DEBUG")

    # Security
    SECRET_KEY: str = Field(default="change-me-in-production", alias="SECRET_KEY")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    
    # Server Ports
    BACKEND_PORT: int = Field(default=8000, alias="BACKEND_PORT")

    # Database（开发默认 SQLite，零依赖开箱即用；生产在 .env 覆盖为 PostgreSQL）
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./data/project_agent.db",
        alias="DATABASE_URL"
    )

    # Commercial LLM (for semantic understanding)
    COMMERCIAL_LLM_PROVIDER: str = Field(default="deepseek", alias="COMMERCIAL_LLM_PROVIDER")
    COMMERCIAL_LLM_API_BASE: str = Field(default="https://api.deepseek.com/v1", alias="COMMERCIAL_LLM_API_BASE")
    COMMERCIAL_LLM_API_KEY: str = Field(default="", alias="COMMERCIAL_LLM_API_KEY")
    COMMERCIAL_LLM_MODEL: str = Field(default="deepseek-chat", alias="COMMERCIAL_LLM_MODEL")
    
    # Local LLM (company internal, OpenAI-compatible API)
    # Used for data processing - sensitive data stays local
    LOCAL_LLM_API_BASE: str = Field(default="", alias="LOCAL_LLM_API_BASE")
    LOCAL_LLM_API_KEY: str = Field(default="", alias="LOCAL_LLM_API_KEY")
    LOCAL_LLM_MODEL: str = Field(default="", alias="LOCAL_LLM_MODEL")
    
    # Security & Privacy
    DATA_MASKING_LEVEL: str = Field(default="normal", alias="DATA_MASKING_LEVEL")
    ENABLE_AUDIT_LOG: bool = Field(default=True, alias="ENABLE_AUDIT_LOG")

    # CL 监控扫描（V2，2026-09-16）
    # dry-run：照常扫描并更新追踪队列，但 Jira 提醒评论只写日志不发。
    # 灰度期先开几天，核对入队数量/状态识别/CL 判定无误后再关闭。
    CL_SCAN_DRY_RUN: bool = Field(default=False, alias="CL_SCAN_DRY_RUN")
    # 扫描间隔（秒），默认每天一次
    CL_SCAN_INTERVAL_SECONDS: int = Field(default=86400, alias="CL_SCAN_INTERVAL_SECONDS")

    # Feishu Bot
    FEISHU_APP_ID: str = Field(default="", alias="FEISHU_APP_ID")
    FEISHU_APP_SECRET: str = Field(default="", alias="FEISHU_APP_SECRET")

    # File Upload
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024  # 50MB

    # Jira Custom Fields — comma-separated customfield_XXXXX IDs
    JIRA_CUSTOM_FIELD_USER: str = Field(
        default="customfield_10700,customfield_11002,customfield_10409,customfield_11104",
        alias="JIRA_CUSTOM_FIELD_USER",
    )
    JIRA_CUSTOM_FIELD_LIST: str = Field(
        default="customfield_10107,customfield_10300,customfield_10407,customfield_11703",
        alias="JIRA_CUSTOM_FIELD_LIST",
    )
    JIRA_CUSTOM_FIELD_CANNOT_SET: str = Field(
        default="customfield_10000,customfield_12506,customfield_12507",
        alias="JIRA_CUSTOM_FIELD_CANNOT_SET",
    )

    # Jira Epic Link custom field ID (used in jira_cl_monitor_skill.py)
    JIRA_CUSTOM_FIELD_EPIC_LINK: str = Field(
        default="customfield_10102",
        alias="JIRA_CUSTOM_FIELD_EPIC_LINK",
    )

    # Jira CL Monitor — default epic keys for merge monitor reports
    JIRA_MONITOR_EPICS: str = Field(
        default="TV-918177,TV-918179,TV-918961,TV-917062,TV-921171,TV-923009",
        alias="JIRA_MONITOR_EPICS",
    )

    # Admin password hash (bcrypt)
    ADMIN_PASSWORD_HASH: str = Field(
        default="$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.VTtYA.qGZvKG6G",
        alias="ADMIN_PASSWORD_HASH",
    )
    
    class Config:
        env_file = str(_env_path) if _env_path.exists() else ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


# Global settings instance
settings = Settings()
