"""
ProjectPlan 服务 — 项目计划知识库（P-D：monitoring_optimization）。

存储项目阶段/里程碑/目标日期（时间基准），供 Agent 判断项目进展
（当前处于哪个阶段、下个里程碑何时）。主表 upsert + 阶段子表替换式更新。
"""

from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.project_plan import ProjectPlan, ProjectPlanPhase

logger = __import__("logging").getLogger(__name__)


async def save_project_plan(
    user_id: int,
    project_name: str,
    phases: List[Dict[str, Any]],
    overall_status: Optional[str] = None,
    source: str = "manual",
    source_ref: Optional[str] = None,
) -> Dict[str, Any]:
    """保存项目计划（按 user_id+project_name upsert，阶段替换式更新）。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ProjectPlan).where(
                ProjectPlan.user_id == user_id,
                ProjectPlan.project_name == project_name,
            )
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            plan = ProjectPlan(
                user_id=user_id, project_name=project_name,
                overall_status=overall_status, source=source, source_ref=source_ref,
            )
            db.add(plan)
            await db.flush()
        else:
            plan.overall_status = overall_status or plan.overall_status
            plan.source = source or plan.source
            plan.source_ref = source_ref or plan.source_ref

        # 阶段替换式更新
        await db.execute(delete(ProjectPlanPhase).where(ProjectPlanPhase.plan_id == plan.id))
        for idx, ph in enumerate(phases):
            target = None
            raw_date = ph.get("target_date")
            if raw_date:
                try:
                    target = date.fromisoformat(str(raw_date).strip())
                except ValueError:
                    logger.warning("非法 target_date: %s", raw_date)
            db.add(ProjectPlanPhase(
                plan_id=plan.id,
                phase_name=ph.get("phase_name", ""),
                target_date=target,
                status=ph.get("status", "planned"),
                deliverables=ph.get("deliverables", []),
                sort_order=ph.get("sort_order", idx),
            ))

        await db.commit()
        await db.refresh(plan)
        return {"id": plan.id, "project_name": plan.project_name}


async def get_project_plan(user_id: int, project_name: str) -> Optional[Dict[str, Any]]:
    """获取项目计划（含阶段，按 target_date 排序）。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ProjectPlan).where(
                ProjectPlan.user_id == user_id,
                ProjectPlan.project_name == project_name,
            )
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            return None
        phases = (await db.execute(
            select(ProjectPlanPhase)
            .where(ProjectPlanPhase.plan_id == plan.id)
            .order_by(ProjectPlanPhase.target_date, ProjectPlanPhase.sort_order)
        )).scalars().all()
        return {
            "id": plan.id,
            "project_name": plan.project_name,
            "overall_status": plan.overall_status,
            "source": plan.source,
            "source_ref": plan.source_ref,
            "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
            "phases": [
                {
                    "phase_name": p.phase_name,
                    "target_date": p.target_date.isoformat() if p.target_date else None,
                    "status": p.status,
                    "deliverables": p.deliverables,
                }
                for p in phases
            ],
        }


async def list_project_plans(user_id: int) -> List[Dict[str, Any]]:
    """列出用户所有项目计划（仅主表摘要）。"""
    async with AsyncSessionLocal() as db:
        plans = (await db.execute(
            select(ProjectPlan).where(ProjectPlan.user_id == user_id)
            .order_by(ProjectPlan.updated_at.desc())
        )).scalars().all()
        return [
            {
                "id": p.id,
                "project_name": p.project_name,
                "overall_status": p.overall_status,
                "source": p.source,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in plans
        ]
