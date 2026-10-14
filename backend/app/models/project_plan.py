"""
ProjectPlan models — 项目计划知识库（P-D：monitoring_optimization）。

记录项目阶段/里程碑/目标日期（时间基准），供 Agent 判断"当前到哪、下个节点、
问题相对计划的偏差"。主表 + 阶段子表（方案 4.2.3：target_date 拆 DATE 列、
阶段明细拆子表支持增量更新/按状态聚合）。
"""

from datetime import date
from typing import Optional
from sqlalchemy import String, Text, JSON, Date, Integer, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class ProjectPlan(Base, TimestampMixin):
    """项目计划主表（每项目一条）。"""

    __tablename__ = "project_plans"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_name: Mapped[str] = mapped_column(String(200), nullable=False)
    overall_status: Mapped[str] = mapped_column(String(100), nullable=True)  # "phase3/5" 或描述
    source: Mapped[str] = mapped_column(String(30), default="manual")  # confluence_page | ppt | doc | manual
    source_ref: Mapped[str] = mapped_column(String(300), nullable=True)  # pageId / file 引用

    __table_args__ = (
        Index("ix_project_plan_user_name", "user_id", "project_name", unique=True),
    )


class ProjectPlanPhase(Base, TimestampMixin):
    """项目计划阶段子表。"""

    __tablename__ = "project_plan_phases"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("project_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    phase_name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # DATE 列，可按日期查询/排序
    status: Mapped[str] = mapped_column(String(30), default="planned")  # planned | in_progress | done | delayed
    deliverables: Mapped[list] = mapped_column(JSON, default=list)  # 描述性数据，保留 JSON
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("ix_plan_phase_plan", "plan_id"),
    )
