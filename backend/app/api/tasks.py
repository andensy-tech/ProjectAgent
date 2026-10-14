"""
Task Management API — submit, status, cancel for background tasks.

Endpoints:
  POST   /api/tasks/submit       — submit a long-running skill to background queue
  GET    /api/tasks/{task_id}    — get task status and result
  POST   /api/tasks/{task_id}/cancel  — cancel a running/pending task
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.api.deps import get_current_active_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# Lazy-import to avoid circular deps at module level
_task_queue = None


async def _get_queue():
    """返回全局 BackgroundTaskWorker（与 engine / ci_build_tool 同一单例）。"""
    global _task_queue
    try:
        from app.services.task_queue import get_worker
        _task_queue = await get_worker()
        return _task_queue
    except Exception as e:
        logger.error("Failed to init BackgroundTaskWorker: %s", e)
        return None


class TaskSubmitRequest(BaseModel):
    """Request body for submitting a background task."""
    skill_name: str
    params: dict = {}


@router.post("/submit")
async def task_submit(
    data: TaskSubmitRequest,
    current_user: dict = Depends(get_current_active_user),
):
    """Submit a long-running skill to the background task queue.

    Returns task_id immediately. Use GET /api/tasks/{task_id} to poll status.
    """
    user_id = current_user.get("user_id")
    queue = await _get_queue()
    if not queue:
        raise HTTPException(status_code=503, detail="后台任务队列不可用")

    # Resolve skill handler
    from app.services.agent.executor import action_executor
    handler = action_executor.skill_registry.get(data.skill_name)
    if not handler:
        raise HTTPException(status_code=404, detail=f"技能不存在: {data.skill_name}")

    # Inject per-user MCP credentials and user_id into params (for subprocess skills)
    # This mirrors the same logic in ActionExecutor._execute_skill()，统一从 MCPCapabilityService 获取
    params = {**data.params}  # mutable copy
    if user_id:
        params["_user_id"] = user_id
        try:
            from app.mcp.capability import capability, build_mcp_config_dict
            servers = await capability.get_servers(user_id)
            if servers:
                params["_mcp_config"] = build_mcp_config_dict(servers)
        except Exception:
            logger.debug("Failed to inject MCP config for task %s / user %s", data.skill_name, user_id)

    # Inject per-user LLM config into params (for subprocess skills that need LLM)
    if user_id and "_llm_config" not in params:
        try:
            from app.services.settings_service import load_user_settings
            us = await load_user_settings(user_id)
            llm_setting = us.get("llm", {}) if isinstance(us, dict) else {}
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
            logger.debug("Failed to inject LLM config for task %s / user %s", data.skill_name, user_id)

    task_id = await queue.submit(handler, params, user_id, data.skill_name)
    return {
        "task_id": task_id,
        "status": "pending",
        "message": f"技能「{data.skill_name}」已提交到后台执行",
    }


@router.get("/{task_id}")
async def task_status(
    task_id: str,
    current_user: dict = Depends(get_current_active_user),
):
    """Get task status and result (if completed).

    结果里若含技能产出的文件，附加 ``files``（含签名下载链接）供前端下载。
    """
    queue = await _get_queue()
    if not queue:
        raise HTTPException(status_code=503, detail="后台任务队列不可用")

    status = await queue.get_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    result = status.get("result")
    if isinstance(result, dict):
        from app.services.file_outputs import describe_file_outputs
        files = describe_file_outputs(result, user_id=current_user.get("user_id"))
        if files:
            status = {**status, "result": {**result, "files": files}}
    return status


@router.post("/{task_id}/cancel")
async def task_cancel(
    task_id: str,
    current_user: dict = Depends(get_current_active_user),
):
    """Cancel a running or pending task."""
    queue = await _get_queue()
    if not queue:
        raise HTTPException(status_code=503, detail="后台任务队列不可用")

    ok = await queue.cancel(task_id)
    if not ok:
        raise HTTPException(status_code=409, detail="任务无法取消（已完成或不存在）")
    return {"task_id": task_id, "status": "cancelled"}
