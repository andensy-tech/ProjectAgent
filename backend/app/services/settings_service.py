"""
User settings service — stores runtime-overridable settings per user.

Supports two storage modes:
  1. File mode (legacy): global user_settings.json, no user_id needed (sync functions)
  2. DB mode (multi-user): per-user settings in user_settings table (async functions)

When user_id is provided, DB mode is used. Otherwise falls back to file mode.
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from sqlalchemy import select
from app.core.config import settings

SETTINGS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "user_settings.json",
)


# ── File-based (legacy, single-user) ────────────────────────────────────────


def _get_settings_path() -> str:
    return os.path.abspath(SETTINGS_FILE)


def _ensure_settings_dir():
    path = _get_settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)


def load_user_settings_file() -> Dict[str, Any]:
    """Load user settings from global JSON file (legacy)."""
    path = _get_settings_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_user_settings_file(data: Dict[str, Any]) -> Dict[str, Any]:
    """Save user settings to global JSON file (legacy)."""
    _ensure_settings_dir()
    path = _get_settings_path()
    existing = load_user_settings_file()
    for key, value in data.items():
        if isinstance(value, dict) and key in existing and isinstance(existing[key], dict):
            existing[key].update(value)
        else:
            existing[key] = value
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    return existing


# ── DB-backed (multi-user) ──────────────────────────────────────────────────


async def load_user_settings(user_id: int) -> Dict[str, Any]:
    """Load user settings from DB. Returns merged dict of all keys."""
    from app.db.session import AsyncSessionLocal
    from app.models import UserSetting

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSetting).where(UserSetting.user_id == user_id)
        )
        rows = result.scalars().all()
        merged: Dict[str, Any] = {}
        for row in rows:
            try:
                merged[row.key] = json.loads(row.value)
            except (json.JSONDecodeError, TypeError):
                merged[row.key] = {}
        return merged


async def save_user_settings(user_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    """Save user settings to DB (upsert by key). Returns merged settings."""
    from app.db.session import AsyncSessionLocal
    from app.models import UserSetting

    async with AsyncSessionLocal() as db:
        for key, value in data.items():
            result = await db.execute(
                select(UserSetting).where(
                    UserSetting.user_id == user_id,
                    UserSetting.key == key,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                # Deep merge for dict values
                try:
                    old_val = json.loads(existing.value)
                except (json.JSONDecodeError, TypeError):
                    old_val = {}
                if isinstance(value, dict) and isinstance(old_val, dict):
                    old_val.update(value)
                    existing.value = json.dumps(old_val)
                else:
                    existing.value = json.dumps(value)
            else:
                db.add(UserSetting(
                    user_id=user_id,
                    key=key,
                    value=json.dumps(value),
                ))
        await db.commit()
    return await load_user_settings(user_id)


# ── Unified accessors (try DB first, fall back to file) ─────────────────────


async def _get_settings(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Get all settings for a user (DB) or global (file)."""
    if user_id is not None:
        return await load_user_settings(user_id)
    return load_user_settings_file()


async def get_llm_config(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Get LLM configuration.

    Behavior depends on context:
    - user_id is None (legacy global mode): env defaults + user_settings.json overrides
    - user_id is not None (multi-user DB mode): only show values the user explicitly saved.
      No .env fallback — prevents new users from inheriting admin's env config.
    """
    user = await _get_settings(user_id)
    llm_user = user.get("llm", {})

    _empty = {"provider": "", "api_base": "", "api_key": "", "model": "", "configured": False}

    default_llm = llm_user.get("default_llm", "commercial")

    if user_id is not None:
        # Multi-user DB mode: only show explicitly saved values, no .env fallback
        return {
            "default_llm": default_llm,
            "commercial": {
                "provider": llm_user.get("commercial_provider", ""),
                "api_base": llm_user.get("commercial_api_base", ""),
                "api_key": "***" if llm_user.get("commercial_api_key") else "",
                "model": llm_user.get("commercial_model", ""),
                "configured": bool(llm_user.get("commercial_api_key")),
            },
            "local": {
                "api_base": llm_user.get("local_api_base", ""),
                "api_key": "***" if llm_user.get("local_api_key") else "",
                "model": llm_user.get("local_model", ""),
                "configured": bool(llm_user.get("local_api_base")),
            },
            "routing": {
                "intent_parsing": "commercial",
                "risk_analysis": "commercial",
                "data_parsing": "local",
                "text_formatting": "local",
            },
        }

    # Legacy file mode: env + user_settings.json overrides
    return {
        "default_llm": default_llm,
        "commercial": {
            "provider": llm_user.get("commercial_provider", settings.COMMERCIAL_LLM_PROVIDER),
            "api_base": llm_user.get("commercial_api_base", settings.COMMERCIAL_LLM_API_BASE),
            "api_key": "***" if (llm_user.get("commercial_api_key") or settings.COMMERCIAL_LLM_API_KEY) else "",
            "model": llm_user.get("commercial_model", settings.COMMERCIAL_LLM_MODEL),
            "configured": bool(llm_user.get("commercial_api_key") or settings.COMMERCIAL_LLM_API_KEY),
        },
        "local": {
            "api_base": llm_user.get("local_api_base", settings.LOCAL_LLM_API_BASE or ""),
            "api_key": "***" if (llm_user.get("local_api_key") or settings.LOCAL_LLM_API_KEY) else "",
            "model": llm_user.get("local_model", settings.LOCAL_LLM_MODEL or ""),
            "configured": bool(llm_user.get("local_api_base") or settings.LOCAL_LLM_API_BASE),
        },
        "routing": {
            "intent_parsing": "commercial",
            "risk_analysis": "commercial",
            "data_parsing": "local",
            "text_formatting": "local",
        },
    }


async def get_project_config(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Get project configuration (no MCP URL fallback — all from user settings)."""
    user = await _get_settings(user_id)
    project = user.get("project", {})

    return {
        "name": project.get("name", settings.APP_NAME),
        "version": project.get("version", settings.APP_VERSION),
        "jira_url": project.get("jira_url", ""),
        "confluence_url": project.get("confluence_url", ""),
        "gerrit_url": project.get("gerrit_url", ""),
    }


async def update_llm_config(data: Dict[str, Any], user_id: Optional[int] = None) -> Dict[str, Any]:
    """Update LLM configuration."""
    if user_id is not None:
        return await _update_llm_config_db(data, user_id)
    return _update_llm_config_file(data)


async def _update_llm_config_db(data: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    llm = {}
    if "default_llm" in data:
        llm["default_llm"] = data["default_llm"]
    if "commercial" in data:
        c = data["commercial"]
        if c.get("api_key") and c["api_key"] != "***":
            llm["commercial_api_key"] = c["api_key"]
        if c.get("api_base"):
            llm["commercial_api_base"] = c["api_base"]
        if c.get("model"):
            llm["commercial_model"] = c["model"]
        if c.get("provider"):
            llm["commercial_provider"] = c["provider"]
    if "local" in data:
        l = data["local"]
        if l.get("api_key") and l["api_key"] != "***":
            llm["local_api_key"] = l["api_key"]
        if l.get("api_base"):
            llm["local_api_base"] = l["api_base"]
        if l.get("model"):
            llm["local_model"] = l["model"]
    await save_user_settings(user_id, {"llm": llm})
    return await get_llm_config(user_id)


def _update_llm_config_file(data: Dict[str, Any]) -> Dict[str, Any]:
    llm = {}
    if "default_llm" in data:
        llm["default_llm"] = data["default_llm"]
    if "commercial" in data:
        c = data["commercial"]
        if c.get("api_key") and c["api_key"] != "***":
            llm["commercial_api_key"] = c["api_key"]
        if c.get("api_base"):
            llm["commercial_api_base"] = c["api_base"]
        if c.get("model"):
            llm["commercial_model"] = c["model"]
        if c.get("provider"):
            llm["commercial_provider"] = c["provider"]
    if "local" in data:
        l = data["local"]
        if l.get("api_key") and l["api_key"] != "***":
            llm["local_api_key"] = l["api_key"]
        if l.get("api_base"):
            llm["local_api_base"] = l["api_base"]
        if l.get("model"):
            llm["local_model"] = l["model"]
    save_user_settings_file({"llm": llm})
    return get_llm_config()  # type: ignore[call-arg]


async def update_project_config(data: Dict[str, Any], user_id: Optional[int] = None) -> Dict[str, Any]:
    """Update project configuration."""
    if user_id is not None:
        return await _update_project_config_db(data, user_id)
    return _update_project_config_file(data)


async def _update_project_config_db(data: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    project = {}
    if data.get("name"):
        project["name"] = data["name"]
    if data.get("version"):
        project["version"] = data["version"]
    if "jira_url" in data:
        project["jira_url"] = data["jira_url"]
    if "confluence_url" in data:
        project["confluence_url"] = data["confluence_url"]
    if "gerrit_url" in data:
        project["gerrit_url"] = data["gerrit_url"]
    await save_user_settings(user_id, {"project": project})
    return await get_project_config(user_id)


def _update_project_config_file(data: Dict[str, Any]) -> Dict[str, Any]:
    project = {}
    if data.get("name"):
        project["name"] = data["name"]
    if data.get("version"):
        project["version"] = data["version"]
    if "jira_url" in data:
        project["jira_url"] = data["jira_url"]
    if "confluence_url" in data:
        project["confluence_url"] = data["confluence_url"]
    if "gerrit_url" in data:
        project["gerrit_url"] = data["gerrit_url"]
    save_user_settings_file({"project": project})
    return get_project_config()  # type: ignore[call-arg]


# ── Dashboard config (user-scoped) ──────────────────────────────────────────


_DEFAULT_CONFIG: Dict[str, Any] = {
    "projects": [],
    "thresholds": {"eta_expiring_days": 7, "stale_days": 7},
    "refresh_interval_seconds": 30,
    "jira_web_url": "",
}


async def get_dashboard_config(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Load dashboard config for a user (DB if user_id, else global file)."""
    user = await _get_settings(user_id)
    config = user.get("dashboard", {})
    if not config:
        return dict(_DEFAULT_CONFIG)
    if "thresholds" not in config:
        config["thresholds"] = dict(_DEFAULT_CONFIG["thresholds"])
    if "refresh_interval_seconds" not in config:
        config["refresh_interval_seconds"] = _DEFAULT_CONFIG["refresh_interval_seconds"]
    if "jira_web_url" not in config:
        config["jira_web_url"] = _DEFAULT_CONFIG["jira_web_url"]
    return config


async def update_dashboard_config(data: Dict[str, Any], user_id: Optional[int] = None) -> Dict[str, Any]:
    """Save dashboard config for a user."""
    if user_id is not None:
        await save_user_settings(user_id, {"dashboard": data})
    else:
        save_user_settings_file({"dashboard": data})
    return await get_dashboard_config(user_id)


_DEFAULT_CL_CONFIG: Dict[str, Any] = {
    "projects": [],
}


async def get_cl_monitoring_config(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Load CL monitoring config."""
    user = await _get_settings(user_id)
    config = user.get("cl_monitoring", {})
    if not config:
        return dict(_DEFAULT_CL_CONFIG)
    return config


# ── CI 构建（Google Android Build 查询/下载/FTPS 上传，2026-09-25）──────────

# 敏感字段：auth.client_secret / ftp.password —— GET 脱敏为 "***"，
# POST 遇 "***" 跳过保留旧值（与 LLM/MCP 配置同一约定）。
_DEFAULT_CI_BUILD_CONFIG: Dict[str, Any] = {
    "branch": "",
    "target": "",
    "ota_flavor": "",
    "default_build_type": "userdebug",
    "auth": {"enabled": True, "client_id": "", "client_secret": "", "project_id": ""},
    "ftp": {"host": "", "port": 21, "username": "", "password": "", "remote_dir": "/"},
}

_CI_BUILD_SENSITIVE = (("auth", "client_secret"), ("ftp", "password"))


def _mask_ci_build(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """返回脱敏副本：敏感字段替换为 "***"（不修改入参）。"""
    masked = dict(cfg)
    for section, key in _CI_BUILD_SENSITIVE:
        sub = masked.get(section)
        if isinstance(sub, dict) and sub.get(key):
            masked[section] = {**sub, key: "***"}
    return masked


async def get_ci_build_config(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Load CI 构建配置（敏感字段脱敏）。"""
    user = await _get_settings(user_id)
    config = user.get("ci_build", {})
    if not config:
        return dict(_DEFAULT_CI_BUILD_CONFIG)
    return _mask_ci_build(config)


async def update_ci_build_config(data: Dict[str, Any],
                                 user_id: Optional[int] = None) -> Dict[str, Any]:
    """Save CI 构建配置。敏感字段值为 "***" 时保留旧值。"""
    user = await _get_settings(user_id)
    existing = user.get("ci_build", {}) or {}
    merged = {**_DEFAULT_CI_BUILD_CONFIG, **existing}
    for section, key in _CI_BUILD_SENSITIVE:
        new_sub = data.get(section)
        if not isinstance(new_sub, dict):
            continue
        if new_sub.get(key) == "***":
            old_val = (existing.get(section) or {}).get(key, "")
            data[section][key] = old_val
        # 保留既有子段中未提交的字段（前端表单只发全量，这里防御性合并）
        data[section] = {**(merged.get(section) or {}), **new_sub}
    merged.update(data)
    if user_id is not None:
        await save_user_settings(user_id, {"ci_build": merged})
    else:
        save_user_settings_file({"ci_build": merged})
    return await get_ci_build_config(user_id)


async def get_ci_build_config_raw(user_id: int) -> Dict[str, Any]:
    """后端专用：返回未脱敏 CI 构建配置（含 client_secret / FTP 密码明文）。

    仅供 ci_build 内置工具进程内使用，绝不暴露给 API。
    """
    user = await load_user_settings(user_id)
    config = user.get("ci_build", {}) if isinstance(user, dict) else {}
    if not config:
        return dict(_DEFAULT_CI_BUILD_CONFIG)
    return config


async def save_cl_monitoring_config(data: Dict[str, Any], user_id: Optional[int] = None) -> Dict[str, Any]:
    """Save CL monitoring config."""
    if user_id is not None:
        await save_user_settings(user_id, {"cl_monitoring": data})
    else:
        save_user_settings_file({"cl_monitoring": data})
    return await get_cl_monitoring_config(user_id)


# ── CL 不关注标记 + 自动提醒去重 (per-user, DB-backed) ─────────────────────


async def get_cl_suppressed(user_id: int) -> list:
    """返回该用户标记不关注的 CL 列表。

    Returns:
        [{instance, change_id, jira_key, reason, marked_at}, ...]
    """
    settings_data = await load_user_settings(user_id)
    return settings_data.get("cl_suppressed") or []


async def add_cl_suppressed(user_id: int, entries: list) -> list:
    """批量追加不关注条目（按 (instance, change_id, jira_key) 去重后覆写）。

    Args:
        entries: [{instance, change_id, jira_key, reason}]，marked_at 由服务端补全。

    Returns:
        最新 suppressed 列表。
    """
    current = await get_cl_suppressed(user_id)
    kept = {
        (e.get("instance"), e.get("change_id"), e.get("jira_key"))
        for e in current if isinstance(e, dict)
    }
    now = datetime.now(timezone.utc).isoformat()
    for e in entries:
        if not isinstance(e, dict):
            continue
        key = (e.get("instance"), e.get("change_id"), e.get("jira_key"))
        if key in kept:
            continue
        current.append({**e, "marked_at": now})
        kept.add(key)
    await save_user_settings(user_id, {"cl_suppressed": current})
    return current


async def remove_cl_suppressed(user_id: int, instance: str, change_id: str, jira_key: str) -> list:
    """移除一个不关注条目（恢复关注）。返回最新列表。"""
    current = await get_cl_suppressed(user_id)
    current = [
        e for e in current
        if not (e.get("instance") == instance and e.get("change_id") == change_id
                and e.get("jira_key") == jira_key)
    ]
    await save_user_settings(user_id, {"cl_suppressed": current})
    return current


# 注：cl_reminders / cl_verified_tracking 两份 UserSetting JSON 已于 2026-09-18 随
# CL 监控 V2 移除 —— 提醒去重改由 cl_jira_states.last_reminded_at 承载，曾 Verified
# 的持续跟踪改由同表的 queued/archived 字段承载。旧存的孤儿数据用
# scripts/cleanup_cl_legacy.py 清理（不可用时直接 DELETE user_settings 相应行）。
# 全量覆写仍走 save_cl_rd_scan（直接操作表，绕开 save_user_settings 的 dict 深合并）。


# ── RD 回复 LLM 分析节流（记录上次分析的评论数，防 token 浪费）───────────


async def get_cl_rd_scan(user_id: int) -> dict:
    """返回 RD 回复分析节流记录 {jira_key: {"count": int, "ts": iso}}."""
    settings_data = await load_user_settings(user_id)
    return settings_data.get("cl_rd_scan") or {}


async def save_cl_rd_scan(user_id: int, scan: dict) -> dict:
    """全量覆写 RD 回复分析节流记录（绕开 dict 深合并，直接操作 user_settings 表）。"""
    from app.db.session import AsyncSessionLocal
    from app.models import UserSetting

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(UserSetting).where(
                    UserSetting.user_id == user_id,
                    UserSetting.key == "cl_rd_scan",
                )
            )
        ).scalar_one_or_none()
        payload = json.dumps(scan, ensure_ascii=False)
        if row:
            row.value = payload
        else:
            db.add(UserSetting(user_id=user_id, key="cl_rd_scan", value=payload))
        await db.commit()
    return scan


# ── MCP config (per-user, DB-backed, standard mcpServers format) ─────────


def _extract_server_configs(user_mcp: dict) -> dict:
    """Extract server config dict from user MCP settings.

    Supports two formats:
      1. New standard format: {"mcpServers": {"name": {"url": ..., "headers": ...}}}
      2. Old flat format:     {"name": {"url": ..., "username": ..., "password": ...}}
    """
    if not isinstance(user_mcp, dict):
        return {}

    # Standard format: wrapped in "mcpServers" key
    if "mcpServers" in user_mcp:
        servers = user_mcp["mcpServers"]
        return servers if isinstance(servers, dict) else {}

    # Old flat format: servers at top level
    return {k: v for k, v in user_mcp.items() if isinstance(v, dict)}


def _extract_auth_from_headers(headers: Any) -> dict:
    """Extract auth dict from headers.Authorization JSON string.

    Standard MCP format stores auth as a JSON string in the Authorization header:
      {"headers": {"Authorization": "{\\"username\\": \\"...\\", \\"password\\": \\"...\\"}"}}
    """
    if not isinstance(headers, dict):
        return {}
    auth_header = headers.get("Authorization", "")
    if not isinstance(auth_header, str) or not auth_header:
        return {}
    try:
        return json.loads(auth_header)
    except (json.JSONDecodeError, TypeError):
        return {}


async def get_mcp_config(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Get per-user MCP server config.

    Returns raw stored config (preserving mcpServers format if used).
    Passwords are masked ("***") in responses.
    No hardcoded server list — dynamically returns all configured servers.
    """
    if user_id is not None:
        user = await _get_settings(user_id)
        raw_mcp = user.get("mcp", {}) if isinstance(user, dict) else {}
    else:
        user = load_user_settings_file()
        raw_mcp = user.get("mcp", {}) if isinstance(user, dict) else {}

    # Deep-copy and mask passwords to avoid mutating cached data
    return _mask_mcp_passwords(raw_mcp)


def _mask_mcp_passwords(cfg: dict) -> dict:
    """Recursively mask password fields in an MCP config dict.

    Handles both old flat format (password field) and standard format
    (Authorization header JSON string containing "password" key).
    """
    if not isinstance(cfg, dict):
        return cfg

    masked = {}
    for key, val in cfg.items():
        if key == "password" and isinstance(val, str) and val:
            masked[key] = "***"
        elif key == "headers" and isinstance(val, dict):
            auth = val.get("Authorization", "")
            if isinstance(auth, str) and auth:
                try:
                    auth_data = json.loads(auth)
                    if isinstance(auth_data, dict) and auth_data.get("password"):
                        auth_data["password"] = "***"
                        masked[key] = {**val, "Authorization": json.dumps(auth_data)}
                    else:
                        masked[key] = val
                except (json.JSONDecodeError, TypeError):
                    masked[key] = val
            else:
                masked[key] = val
        elif isinstance(val, dict):
            masked[key] = _mask_mcp_passwords(val)
        else:
            masked[key] = val
    return masked


async def update_mcp_config(data: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    """Save per-user MCP server config.

    Accepts both formats:
      - Standard (全量替换): {"mcpServers": {"name": {"url": ..., "headers": ...}}}
      - Flat (逐 server 合并):  {"name": {"url": ..., ...}}

    Standard format replaces the entire mcpServers config (full control via JSON editor).
    Flat format merges per-server for backward compatibility.
    "***" preserves existing password value.
    """
    current = await load_user_settings(user_id)
    existing_mcp_raw = current.get("mcp", {}) if isinstance(current, dict) else {}

    if "mcpServers" in data:
        # Standard format: full replace of mcpServers, preserving "***" passwords
        # Also remove flat-format keys that duplicate servers now in mcpServers
        new_servers = data["mcpServers"]
        if isinstance(new_servers, dict):
            existing_servers = existing_mcp_raw.get("mcpServers", {}) if isinstance(existing_mcp_raw, dict) else {}
            # For each new server, if Authorization contains "***", preserve old value
            for name, cfg in new_servers.items():
                if not isinstance(cfg, dict):
                    continue
                headers = cfg.get("headers")
                if isinstance(headers, dict):
                    auth_header = headers.get("Authorization", "")
                    if auth_header == "***" or '"***"' in auth_header:
                        # Preserve old Authorization header value
                        old_server = existing_servers.get(name, {})
                        old_headers = old_server.get("headers", {}) if isinstance(old_server, dict) else {}
                        old_auth = old_headers.get("Authorization", "") if isinstance(old_headers, dict) else ""
                        if old_auth:
                            headers["Authorization"] = old_auth
            existing_mcp_raw["mcpServers"] = new_servers
        # Remove flat-format keys that are now in mcpServers
        for srv_name in new_servers:
            existing_mcp_raw.pop(srv_name, None)
    else:
        # Flat format: merge at top level (backward compat)
        for name, cfg in data.items():
            if not isinstance(cfg, dict):
                continue
            existing = existing_mcp_raw.get(name, {})
            if isinstance(existing, dict):
                merged = dict(existing)
                for k, v in cfg.items():
                    if v is not None and v != "" and v != "***":
                        merged[k] = v
                if merged:
                    existing_mcp_raw[name] = merged
            else:
                existing_mcp_raw[name] = cfg

    await save_user_settings(user_id, {"mcp": existing_mcp_raw})
    return await get_mcp_config(user_id)


async def get_mcp_auth_dict(user_id: int, include_no_auth: bool = False) -> Dict[str, Dict]:
    """Get raw MCP auth dict for backend internal use (passwords unmasked).

    Supports both standard mcpServers format and old flat format.
    Dynamically discovers all configured servers (no hardcoded list).

    Args:
        user_id: 用户 ID
        include_no_auth: 为 True 时保留无 auth 但有 url 的服务器（auth 为空 dict），
            供 MCPCapabilityService 发现工具/标记状态使用。

    Returns:
        {"jira": {"url": str, "auth": {"username": str, "password": str}},
         "my-custom-server": {"url": str, "auth": {"username": str, "password": str}}}
    """
    user = await load_user_settings(user_id)
    user_mcp = user.get("mcp", {}) if isinstance(user, dict) else {}

    servers = _extract_server_configs(user_mcp)
    result = {}
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue

        url = cfg.get("url", "")
        if not url:
            continue

        # Extract auth from headers (standard format)
        auth = _extract_auth_from_headers(cfg.get("headers"))

        # Fallback to flat format fields
        if not auth:
            username = cfg.get("username", "")
            password = cfg.get("password", "")
            if username and password:
                auth = {"username": username, "password": password}

        if not auth:
            if include_no_auth:
                result[name] = {"url": url, "auth": {}}
            continue

        # Gerrit / Confluence servers may need server_url in auth.
        # MCP 代理地址（如 http://10.0.0.1:8118/mcp）≠ 真实 REST 地址
        # （如 https://gerrit-1.example.com），REST 工具依赖 server_url 定位真实服务。
        if "server_url" not in auth and ("gerrit" in name or "confluence" in name):
            # 优先取顶层字段，其次从 headers 的 Authorization JSON 中提取
            server_url = cfg.get("server_url", "")
            if not server_url:
                hdr_auth = _extract_auth_from_headers(cfg.get("headers"))
                if isinstance(hdr_auth, dict):
                    server_url = hdr_auth.get("server_url", "")
            if server_url:
                auth["server_url"] = server_url

        result[name] = {
            "url": url,
            "auth": auth,
        }
    return result


# ── Sync shims for backward compatibility (used by non-API code) ────────────
# These are used by code that doesn't have access to request-level user context.
# They read from the global JSON file only.


def get_dashboard_config_sync() -> Dict[str, Any]:
    """Sync version of get_dashboard_config (file only). Used by background tasks."""
    settings = load_user_settings_file()
    config = settings.get("dashboard", {})
    if not config:
        return dict(_DEFAULT_CONFIG)
    if "thresholds" not in config:
        config["thresholds"] = dict(_DEFAULT_CONFIG["thresholds"])
    if "refresh_interval_seconds" not in config:
        config["refresh_interval_seconds"] = _DEFAULT_CONFIG["refresh_interval_seconds"]
    if "jira_web_url" not in config:
        config["jira_web_url"] = _DEFAULT_CONFIG["jira_web_url"]
    return config


def get_cl_monitoring_config_sync() -> Dict[str, Any]:
    """Sync version of get_cl_monitoring_config (file only). Used by background tasks."""
    settings = load_user_settings_file()
    config = settings.get("cl_monitoring", {})
    if not config:
        return dict(_DEFAULT_CL_CONFIG)
    return config


# ── Legacy sync function aliases (for backward compatibility) ───────────────

# NOTE: Do NOT create aliases named 'load_user_settings' or 'save_user_settings'
# here — those names are used by the async DB versions above and would shadow them.
get_llm_config_sync = get_llm_config  # backward compat alias
get_project_config_sync = get_project_config
