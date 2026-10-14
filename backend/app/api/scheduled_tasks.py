"""定时任务管理 API — Agent 通用 scheduled tasks（2026-09-09）。

GET    /api/scheduled_tasks          列表（含运行状态）
POST   /api/scheduled_tasks          创建（body 同内置工具 config）
PUT    /api/scheduled_tasks/{id}     更新（active 启停/interval_minutes/run_until/name）
DELETE /api/scheduled_tasks/{id}     删除

均按 user_id 隔离（get_current_active_user），管理入口为看板「定时任务」页面。
"""

import logging
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_active_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["scheduled-tasks"])


class ScheduledTaskCreate(BaseModel):
    """创建定时任务请求体（声明式规则）。"""

    name: str
    config: Dict[str, Any]
    interval_minutes: int = 60
    run_until: Optional[str] = None
    description: Optional[str] = None


class ScheduledTaskUpdate(BaseModel):
    """更新定时任务请求体（全部可选）。"""

    name: Optional[str] = None
    active: Optional[bool] = None
    interval_minutes: Optional[int] = None
    run_until: Optional[str] = None
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


def _parse_run_until(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"run_until 格式应为 YYYY-MM-DD，收到: {raw}")


@router.get("/scheduled_tasks")
async def scheduled_tasks_list(
    current_user: dict = Depends(get_current_active_user),
) -> Any:
    """列出当前用户的定时任务（含运行状态）。"""
    from app.services.scheduled_task_service import list_scheduled_tasks

    return await list_scheduled_tasks(current_user.get("user_id"))


@router.post("/scheduled_tasks")
async def scheduled_tasks_create(
    data: ScheduledTaskCreate,
    current_user: dict = Depends(get_current_active_user),
) -> Any:
    """创建定时任务（创建前经 handler.validate 校验，错误 400 透出中文提示）。"""
    from app.services import scheduled_task_handlers  # noqa: F401 — 触发 handler 自注册
    from app.services.scheduled_task_service import create_scheduled_task

    try:
        return await create_scheduled_task(
            user_id=current_user.get("user_id"),
            name=data.name,
            config=data.config,
            interval_minutes=data.interval_minutes,
            run_until=_parse_run_until(data.run_until),
            description=data.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/scheduled_tasks/{task_id}")
async def scheduled_tasks_update(
    task_id: int,
    data: ScheduledTaskUpdate,
    current_user: dict = Depends(get_current_active_user),
) -> Any:
    """更新任务（启停/改名/改间隔/改截止日）。"""
    from app.services.scheduled_task_service import update_scheduled_task

    try:
        result = await update_scheduled_task(
            user_id=current_user.get("user_id"),
            task_id=task_id,
            name=data.name,
            active=data.active,
            interval_minutes=data.interval_minutes,
            run_until=_parse_run_until(data.run_until),
            description=data.description,
            config=data.config,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return result


@router.delete("/scheduled_tasks/{task_id}")
async def scheduled_tasks_delete(
    task_id: int,
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """删除定时任务。"""
    from app.services.scheduled_task_service import delete_scheduled_task

    ok = await delete_scheduled_task(current_user.get("user_id"), task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return {"success": True}
