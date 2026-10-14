"""
Action Execution Layer — Executes skill and tool calls for the single-brain LLM loop.

Responsibilities:
  - _execute_skill() — used by engine.py for execute_skill meta-tool
    (injects per-user skill config, MCP _mcp_config, and LLM config into subprocess skills)
  - _execute_tool() — used by engine.py for call_builtin_tool (built-in in-process tools only)

MCP tool calls are NOT routed through this executor anymore — they go through
MCPCapabilityService (app/mcp/capability.py), the single unified MCP entry.
"""

import logging
from inspect import iscoroutinefunction
from typing import Dict, Any
from sqlalchemy import select
from app.core.exceptions import SkillException, ToolException

logger = logging.getLogger(__name__)


class ActionExecutor:
    """Executes skill and tool actions. Used by the Agent engine's LLM function calling loop."""

    def __init__(self):
        self.skill_registry = {}
        self.tool_registry = {}

    def register_skill(self, name: str, handler: callable):
        """Register a Skill handler."""
        self.skill_registry[name] = handler

    def register_tool(self, name: str, handler: callable):
        """Register a built-in tool handler."""
        self.tool_registry[name] = handler

    async def _execute_skill(self, skill_name: str, params: Dict, context: Dict) -> Any:
        """Execute a registered Skill with per-user config injection."""
        if skill_name not in self.skill_registry:
            raise SkillException(f"Skill not found: {skill_name}", skill_name=skill_name)

        # Inject per-user skill config into params (user overrides merged on top of defaults)
        user_id = context.get("_user_id") if isinstance(context, dict) else None
        if user_id:
            params["_user_id"] = user_id
            try:
                from app.db.session import AsyncSessionLocal
                from app.models.skill_config import SkillConfig
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(SkillConfig).where(
                            SkillConfig.user_id == user_id,
                            SkillConfig.skill_name == skill_name,
                        )
                    )
                    row = result.scalar_one_or_none()
                    if row and row.config_json:
                        merged = {**params, **row.config_json}
                        params = merged
            except Exception:
                logger.warning("Failed to load skill config for %s / user %s", skill_name, user_id)

        # Inject per-user MCP credentials into params (for subprocess skills)
        # 统一从 MCPCapabilityService 获取能力清单并构造 _mcp_config（技能脚本独立用 requests 直调）
        if user_id:
            try:
                from app.mcp.capability import capability, build_mcp_config_dict
                servers = await capability.get_servers(user_id)
                if servers:
                    params["_mcp_config"] = build_mcp_config_dict(servers)

                # Validate MCP is configured for skills that require it
                if not params.get("_mcp_config"):
                    try:
                        from app.skills.scanner import skill_scanner
                        skill_def = skill_scanner.get_metadata().get(skill_name)
                        if skill_def and skill_def.mcp_required:
                            servers = "、".join(skill_def.mcp_required)
                            raise SkillException(
                                f"技能「{skill_name}」需要 MCP 服务器配置（{servers}），"
                                f"但您尚未配置任何 MCP 服务器。请先在设置页面的「MCP 服务」中配置后再执行此技能。",
                                skill_name=skill_name,
                            )
                    except SkillException:
                        raise
                    except Exception:
                        logger.debug("Failed to check mcp_required for skill %s", skill_name)
            except Exception:
                logger.debug("Failed to inject MCP config for skill %s / user %s", skill_name, user_id)

        # Inject per-user LLM config into params (for subprocess skills that need LLM)
        if user_id and "_llm_config" not in params:
            try:
                from app.services.settings_service import load_user_settings
                us = await load_user_settings(user_id)
                llm_setting = us.get("llm", {}) if isinstance(us, dict) else {}
                # Determine which provider (commercial/local) the user selected
                default = llm_setting.get("default_llm", "commercial")
                prefix = "commercial" if default == "commercial" else "local"
                api_key = llm_setting.get(f"{prefix}_api_key", "")
                api_base = llm_setting.get(f"{prefix}_api_base", "")
                model = llm_setting.get(f"{prefix}_model", "deepseek-chat")
                if api_key and api_base:
                    params["_llm_config"] = {
                        "api_base": api_base,
                        "api_key": api_key,
                        "model": model,
                    }
            except Exception:
                logger.debug("Failed to inject LLM config for skill %s / user %s", skill_name, user_id)

        handler = self.skill_registry[skill_name]
        if iscoroutinefunction(handler):
            result = await handler(params, context)
        else:
            result = handler(params, context)

        # Normalize return format to standard {success, data, message, suggested_next}
        if isinstance(result, dict):
            if "success" not in result:
                result_type = result.get("type", "")
                result["success"] = result_type != "error"
            result.setdefault("data", None)
            result.setdefault("message", result.get("title") or result.get("error") or "")
            result.setdefault("suggested_next", None)

        return result

    async def _execute_tool(self, tool_name: str, params: Dict, context: Dict) -> Any:
        """Execute a built-in tool (in-process function).

        MCP tool calls are NOT handled here — they go through
        MCPCapabilityService (app/mcp/capability.py), the single unified entry.

        Args:
            tool_name: Built-in tool simple name (e.g. "read_docx").
            params: Tool parameters.
            context: Execution context.
        """
        if tool_name not in self.tool_registry:
            raise ToolException(f"Tool not found: {tool_name}", tool_name=tool_name)
        handler = self.tool_registry[tool_name]
        return await handler(params, context)


# Global action executor instance
action_executor = ActionExecutor()
