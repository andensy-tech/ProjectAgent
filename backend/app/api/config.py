"""
Configuration API - System settings and LLM configuration.
"""

import asyncio
from typing import Dict, Optional, Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.api.deps import get_current_active_user
from app.core.config import settings
from app.services.settings_service import (
    get_llm_config as _get_llm,
    get_project_config as _get_project,
    update_llm_config as _upd_llm,
    update_project_config as _upd_project,
    get_mcp_config as _get_mcp,
    update_mcp_config as _upd_mcp,
    get_ci_build_config as _get_ci_build,
    update_ci_build_config as _upd_ci_build,
)

router = APIRouter(prefix="/api/config", tags=["config"])


class LLMConfigUpdate(BaseModel):
    """LLM configuration update."""
    default_llm: Optional[str] = None
    commercial: dict = {}
    local: dict = {}


class ProjectConfigUpdate(BaseModel):
    """Project configuration update."""
    name: str = ""
    version: str = ""
    jira_url: str = ""
    confluence_url: str = ""
    gerrit_url: str = ""


class CiBuildAuthConfig(BaseModel):
    """CI 构建 Google OAuth 配置。"""
    enabled: bool = True
    client_id: str = ""
    client_secret: str = ""
    project_id: str = ""


class CiBuildFtpConfig(BaseModel):
    """CI 构建 FTPS 上传配置。"""
    host: str = ""
    port: int = 21
    username: str = ""
    password: str = ""
    remote_dir: str = "/"


class CiBuildConfigUpdate(BaseModel):
    """CI 构建配置更新（敏感字段传 "***" 表示保留旧值）。"""
    branch: str = ""
    target: str = ""
    ota_flavor: str = ""
    default_build_type: str = "userdebug"
    auth: CiBuildAuthConfig = CiBuildAuthConfig()
    ftp: CiBuildFtpConfig = CiBuildFtpConfig()


@router.get("/llm")
async def get_llm_config_endpoint(
    current_user: dict = Depends(get_current_active_user),
):
    """Get LLM configuration (sensitive values masked), including saved verify status."""
    user_id = current_user.get("user_id")
    result = await _get_llm(user_id)
    # Include persisted verification status from last save
    result["_verify"] = _load_verify_status(user_id, "llm")
    return result


@router.post("/llm")
async def save_llm_config(
    data: LLMConfigUpdate,
    current_user: dict = Depends(get_current_active_user),
):
    """Save LLM configuration and verify connectivity."""
    user_id = current_user.get("user_id")
    result = await _upd_llm(data.model_dump(), user_id)

    # Verify LLM connectivity after saving
    async def _test_llm(provider_key: str, cfg: dict) -> dict:
        api_key = cfg.get("api_key", "")
        api_base = cfg.get("api_base", "")
        model = cfg.get("model", "")
        # "***" means user didn't provide a new value — skip and look elsewhere
        _is_placeholder = lambda v: not v or v == "***"
        if _is_placeholder(api_key) or _is_placeholder(api_base) or _is_placeholder(model):
            try:
                # Try DB saved config (FLAT format: commercial_api_key, commercial_api_base, ...)
                from app.services.settings_service import load_user_settings
                raw = await load_user_settings(user_id)
                saved = raw.get("llm", {})
                if _is_placeholder(api_key):
                    api_key = saved.get(f"{provider_key}_api_key", "")
                if _is_placeholder(api_base):
                    api_base = saved.get(f"{provider_key}_api_base", "")
                if _is_placeholder(model):
                    model = saved.get(f"{provider_key}_model", "")
            except Exception:
                pass
            # Fall back to .env defaults
            if _is_placeholder(api_key) or _is_placeholder(api_base) or _is_placeholder(model):
                from app.core.config import settings
                prefix = "COMMERCIAL_" if provider_key == "commercial" else "LOCAL_"
                if _is_placeholder(api_key):
                    api_key = getattr(settings, f"{prefix}LLM_API_KEY", "")
                if _is_placeholder(api_base):
                    api_base = getattr(settings, f"{prefix}LLM_API_BASE", "")
                if _is_placeholder(model):
                    model = getattr(settings, f"{prefix}LLM_MODEL", "")
        if not api_key or not api_base or not model:
            return {"verified": False, "message": "配置不完整"}
        try:
            from app.services.llm.client import llm_client
            # Log test params to /tmp/llm_verify.log for debugging
            masked_key = api_key[:8] + "..." if len(api_key) > 8 else "***"
            import builtins as _b
            with _b.open('/tmp/llm_verify.log', 'a') as _lf:
                _lf.write(f"[LLM_VERIFY] testing {provider_key}: model={model} base={api_base[:50]} key={masked_key}\n")
            await llm_client.complete(
                messages=[{"role": "user", "content": "Hello"}],
                model_type=provider_key,
                model=model,
                api_key=api_key,
                api_base=api_base,
                max_tokens=20,
            )
            with _b.open('/tmp/llm_verify.log', 'a') as _lf:
                _lf.write(f"[LLM_VERIFY] {provider_key}: success\n")
            return {"verified": True, "message": "连接成功"}
        except Exception as e:
            err_msg = str(e)[:200]
            with _b.open('/tmp/llm_verify.log', 'a') as _lf:
                _lf.write(f"[LLM_VERIFY] {provider_key}: FAILED: {err_msg}\n")
            return {"verified": False, "message": f"链接失败: {err_msg}"}

    tests = {}
    raw = data.model_dump()
    # Only test providers that actually have fields being saved (skip empty {})
    for prov in ("commercial", "local"):
        cfg = raw.get(prov, {})
        if cfg and any(cfg.values()):  # non-empty dict with at least one field
            tests[prov] = await _test_llm(prov, cfg)

    result["_verify"] = tests
    # Persist verification status for page refresh
    _save_verify_status(user_id, "llm", tests)
    return result


@router.get("/mcp")
async def get_mcp_config_endpoint(
    current_user: dict = Depends(get_current_active_user),
):
    """Get per-user MCP server configuration (passwords masked), including saved verify status."""
    user_id = current_user.get("user_id")
    result = await _get_mcp(user_id)
    result["_server_status"] = _load_verify_status(user_id, "mcp")
    return result


@router.get("/project")
async def get_project_config_endpoint(
    current_user: dict = Depends(get_current_active_user),
):
    """Get project configuration."""
    user_id = current_user.get("user_id")
    return await _get_project(user_id)
@router.post("/project")
async def save_project_config(
    data: ProjectConfigUpdate,
    current_user: dict = Depends(get_current_active_user),
):
    """Save project configuration."""
    user_id = current_user.get("user_id")
    return await _upd_project(data.model_dump(), user_id)


@router.get("/ci_build")
async def get_ci_build_config_endpoint(
    current_user: dict = Depends(get_current_active_user),
):
    """Get CI 构建配置（client_secret / FTP 密码脱敏为 ***）。"""
    user_id = current_user.get("user_id")
    return await _get_ci_build(user_id)


@router.post("/ci_build")
async def save_ci_build_config(
    data: CiBuildConfigUpdate,
    current_user: dict = Depends(get_current_active_user),
):
    """Save CI 构建配置。敏感字段值为 "***" 时保留旧值。"""
    user_id = current_user.get("user_id")
    return await _upd_ci_build(data.model_dump(), user_id)


@router.post("/mcp")
async def save_mcp_config(
    data: Dict[str, Dict],
    current_user: dict = Depends(get_current_active_user),
):
    """Save per-user MCP server configuration and verify connectivity.

    Only stores values that differ from defaults.
    Passwords with value "***" are skipped (keeps existing).
    After saving, triggers MCP discovery and returns per-server status.
    """
    user_id = current_user.get("user_id")
    result = await _upd_mcp(data, user_id)

    # 热启动：失效该用户工具缓存并立即发现新配置的服务器工具，
    # 保存后无需重启即可在 web chat 中使用新的 MCP 能力。
    # 验证超时用 15s：5s 太紧，刚 invalidate 重建 client（首次连接握手）+ 服务器偶发慢时
    # 会误报"不可达"（如 jira MCP 曾间歇性 ReadTimeout）。15s 与正常调用一致。
    from app.mcp.capability import capability
    await capability.invalidate(user_id)
    server_status = await capability.discover(user_id, timeout=15)

    _save_verify_status(user_id, "mcp", server_status)
    return {**result, "_server_status": server_status}


# ── Verification status persistence (survives page refresh) ───────────────

_VERIFY_DIR = "/tmp/agent-verify"


def _save_verify_status(user_id: int, key: str, data: dict):
    """Persist verification status to a temp file so GET can return it."""
    import os as _os, json as _json
    try:
        _os.makedirs(_VERIFY_DIR, exist_ok=True)
        fp = _os.path.join(_VERIFY_DIR, f"{key}-{user_id}.json")
        with open(fp, "w") as _f:
            _json.dump(data, _f)
    except Exception:
        pass


def _load_verify_status(user_id: int, key: str) -> dict:
    """Load persisted verification status."""
    import os as _os, json as _json
    try:
        fp = _os.path.join(_VERIFY_DIR, f"{key}-{user_id}.json")
        with open(fp) as _f:
            return _json.load(_f)
    except (FileNotFoundError, _json.JSONDecodeError, OSError):
        return {}




@router.get("/system")
async def get_system_config(
    current_user: dict = Depends(get_current_active_user),
):
    """Get system configuration."""
    return {
        "app_name": settings.APP_NAME,
        "app_version": settings.APP_VERSION,
        "debug": settings.DEBUG,
        "data_masking_level": settings.DATA_MASKING_LEVEL,
        "enable_audit_log": settings.ENABLE_AUDIT_LOG,
    }
