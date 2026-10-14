"""
Script Skill Runner - Standardized subprocess execution for script-based skills.

Takes a SkillDef with entry_point, runs the script as a subprocess,
passes params via stdin as JSON, and parses the output.

Output contract for scripts:
    - Scripts should output a single JSON object on the last line of stdout:
      {"success": true/false, "message": "...", "data": {...}}
    - If the last line is not valid JSON, all stdout is returned as text.
    - Params are passed to the script via stdin as a JSON string.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, Optional
from app.skills.scanner import SkillDef

logger = logging.getLogger(__name__)


class ScriptExecutionError(Exception):
    """Raised when a script skill execution fails."""
    pass


async def run_script_skill(
    skill_def: SkillDef,
    params: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run a script-based skill via subprocess.

    Args:
        skill_def: Skill definition with entry_point info.
        params: Runtime parameters to pass to the script.
        context: Execution context. Supports:
            _background_task (bool): If True, disables script timeout.
                Background tasks have no HTTP request to protect,
                so the timeout would only cause false failures.

    Returns:
        Standardized result dict matching the agent's expected format.
    """
    entry_point = skill_def.entry_point_path
    if not entry_point:
        return {
            "type": "error",
            "title": "技能配置错误",
            "error": f"技能 '{skill_def.name}' 没有配置入口脚本",
            "data": {},
        }

    if not os.path.isfile(entry_point):
        return {
            "type": "error",
            "title": "技能脚本未找到",
            "error": f"入口脚本不存在: {entry_point}",
            "data": {},
        }

    # Build command: use the same Python interpreter that runs the agent
    cmd = [sys.executable, entry_point]
    cmd.extend(skill_def.entry_args or [])

    # Pass runtime params via stdin as JSON
    stdin_data = json.dumps(params, ensure_ascii=False).encode("utf-8")

    # Background tasks (task queue) should not timeout — no HTTP request to protect.
    # Inline execution (from chat) respects skill_def.timeout (default 300s).
    is_background = isinstance(context, dict) and context.get("_background_task")
    effective_timeout = None if is_background else skill_def.timeout

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=skill_def.directory,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        if effective_timeout is not None:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(stdin_data), timeout=effective_timeout
            )
        else:
            stdout_bytes, stderr_bytes = await proc.communicate(stdin_data)

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            logger.error(
                "Script skill '%s' exited with code %d: %s",
                skill_def.name, proc.returncode, stderr[:500]
            )
            return {
                "type": "error",
                "title": f"技能执行失败 ({skill_def.display_name})",
                "error": f"脚本退出码 {proc.returncode}\n{stderr[:2000]}",
                "data": {"stdout": stdout[:2000], "stderr": stderr[:2000]},
            }

        # Parse output: try last line as JSON
        result = _parse_script_output(stdout, stderr, skill_def)
        return result

    except asyncio.TimeoutError:
        logger.error("Script skill '%s' timed out after %ds", skill_def.name, skill_def.timeout)
        return {
            "type": "error",
            "title": f"技能执行超时 ({skill_def.display_name})",
            "error": f"脚本执行超过 {skill_def.timeout} 秒",
            "data": {},
        }
    except FileNotFoundError as e:
        logger.error("Script skill '%s' failed: %s", skill_def.name, e)
        return {
            "type": "error",
            "title": "技能执行失败",
            "error": f"无法执行脚本: {e}",
            "data": {},
        }
    except Exception as e:
        logger.exception("Script skill '%s' unexpected error", skill_def.name)
        return {
            "type": "error",
            "title": f"技能执行异常 ({skill_def.display_name})",
            "error": str(e),
            "data": {},
        }


def _parse_script_output(
    stdout: str, stderr: str, skill_def: SkillDef
) -> Dict[str, Any]:
    """
    Parse script stdout into a structured result.

    Priority:
    1. Last non-empty line as JSON → structured result
    2. Everything as text → text result
    """
    lines = [l for l in stdout.splitlines() if l.strip()]

    # Try last line as JSON
    if lines:
        last_line = lines[-1].strip()
        try:
            parsed = json.loads(last_line)
            if isinstance(parsed, dict):
                # Normalize to agent format
                success = parsed.get("success", True)
                return {
                    "type": "success" if success else "error",
                    "title": parsed.get("message", skill_def.display_name),
                    "data": parsed.get("data", parsed),
                    "message": parsed.get("message", ""),
                    "error": parsed.get("error") if not success else None,
                }
        except json.JSONDecodeError:
            pass

    # Fallback: return stdout as text
    text = stdout.strip()
    if stderr:
        text += f"\n\n[stderr]\n{stderr.strip()[:1000]}"

    return {
        "type": "text",
        "title": skill_def.display_name,
        "message": text,
        "data": {"output": text},
    }


def make_script_handler(skill_def: SkillDef):
    """
    Create an async handler function for a script-based skill.

    Returns an async callable compatible with ActionExecutor.register_skill().
    """
    async def handler(params: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        return await run_script_skill(skill_def, params, context)
    return handler
