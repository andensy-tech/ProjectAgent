"""
Project Plans API — 项目计划知识库（P-D：monitoring_optimization）。

  GET  /api/project-plans                 — 列出用户项目计划
  GET  /api/project-plans/{project_name}  — 项目计划详情（含阶段）
  POST /api/project-plans                 — 保存项目计划（upsert）
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_active_user
from app.services.project_plan_service import (
    get_project_plan,
    list_project_plans,
    save_project_plan,
)

router = APIRouter(prefix="/api/project-plans", tags=["project-plans"])


class PhaseIn(BaseModel):
    phase_name: str
    target_date: Optional[str] = None  # YYYY-MM-DD
    status: str = "planned"
    deliverables: list = []


class ProjectPlanIn(BaseModel):
    project_name: str
    overall_status: Optional[str] = None
    source: str = "manual"
    source_ref: Optional[str] = None
    phases: list[PhaseIn] = []


@router.get("")
async def plans_list(
    current_user: dict = Depends(get_current_active_user),
) -> List[Dict[str, Any]]:
    """列出用户所有项目计划。"""
    return await list_project_plans(current_user.get("user_id"))


@router.get("/{project_name}")
async def plans_detail(
    project_name: str,
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """项目计划详情（含阶段）。"""
    plan = await get_project_plan(current_user.get("user_id"), project_name)
    if plan is None:
        raise HTTPException(status_code=404, detail="项目计划不存在")
    return plan


@router.post("")
async def plans_save(
    data: ProjectPlanIn,
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """保存项目计划（按 user_id+project_name upsert，阶段替换）。"""
    result = await save_project_plan(
        user_id=current_user.get("user_id"),
        project_name=data.project_name.strip(),
        phases=[p.model_dump() for p in data.phases],
        overall_status=data.overall_status,
        source=data.source,
        source_ref=data.source_ref,
    )
    return result
