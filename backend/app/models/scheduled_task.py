"""
ScheduledTask model — Agent 通用定时任务（2026-09-09）。

PM 通过 web chat（Agent 内置工具）或看板创建/管理的无人值守定时任务。
通用三层架构：本表（数据）→ scheduled_task_service（引擎，与任务类型无关）→
handler 注册表（task_type → 执行逻辑；本期仅 declarative_rule，新类型 = 注册新 handler）。

调度表达：interval_minutes（间隔）+ run_until（可选截止日，到期自动停用 + 站内通知）。
config 为声明式规则 JSON（source/conditions/action），由 handler.validate 校验。
state 为运行时状态（如 add_comment 去重 {issue_key: 上次提醒日期}）。
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    String, Text, JSON, Date, DateTime, Boolean, Integer, ForeignKey, Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ScheduledTask(Base, TimestampMixin):
    """通用定时任务主表（按 user_id 隔离）。"""

    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 任务类型：本期 "declarative_rule"；未来新类型经 register_handler 注册（如二期 LLM 生成型）
    task_type: Mapped[str] = mapped_column(String(30), default="declarative_rule")
    # 声明式规则配置（source/conditions/action），创建时经 handler.validate 校验
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    # 截止日（含当日）；到期自动 active=False + 站内通知
    run_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # 运行时状态
    next_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
    )
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    # 任务私有运行时状态（去重等），结构由 handler 自定义
    state: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("ix_sched_task_due", "active", "next_run_at"),
    )
