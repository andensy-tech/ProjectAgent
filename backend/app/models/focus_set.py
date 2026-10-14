"""
FocusSet models — PM 关注集合（P-B：monitoring_optimization 聊天式关注指令）。

FocusSet = PM 通过 web chat 下发的关注指令（filter/epic 链接 + 目标 + 截止），
用于看板状态图 + 到期提醒。主表 + 成员子表（方案 4.3.3 数据模型：
deadline 拆 DATE 列、metric_target 拆 NUMERIC 列、成员拆子表支持按状态聚合/反查）。
"""

from datetime import date, datetime
from typing import Optional
from sqlalchemy import (
    String, Text, JSON, Date, DateTime, Float, Boolean, Integer, ForeignKey, Index,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class FocusSet(Base, TimestampMixin):
    """PM 关注集合主表。"""

    __tablename__ = "focus_sets"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(20), default="filter")  # filter | epic | manual
    identifier: Mapped[str] = mapped_column(String(100), nullable=False)  # filter id / epic key
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    jql: Mapped[str] = mapped_column(Text, nullable=True)  # 拉取成员用 JQL
    description: Mapped[str] = mapped_column(Text, nullable=True)
    deadline: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # 到期扫描
    metric_type: Mapped[str] = mapped_column(String(30), nullable=True)  # fix_rate | all_fix
    metric_target: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 0.8
    priority_semantics: Mapped[str] = mapped_column(String(100), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    command_history: Mapped[list] = mapped_column(JSON, default=list)  # 指令历史（纯展示）

    # Epic 周基线：每周一固化 epic 下的 issue 列表，作为本周 fix rate 分母
    baseline_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    baseline_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    baseline_members: Mapped[Optional[list]] = mapped_column(JSON, default=list, nullable=True)

    # 上一日完成数：用于计算“今日新增完成数”
    last_done_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_focus_set_ident", "user_id", "type", "identifier", unique=True),
    )


class FocusSetMember(Base, TimestampMixin):
    """关注集合成员（高频更新，snapshot 语义，按 issue_key 反查/按 status 聚合）。"""

    __tablename__ = "focus_set_members"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    focus_set_id: Mapped[int] = mapped_column(ForeignKey("focus_sets.id", ondelete="CASCADE"), nullable=False, index=True)
    issue_key: Mapped[str] = mapped_column(String(50), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(50), default="")
    priority: Mapped[str] = mapped_column(String(20), default="")
    assignee: Mapped[str] = mapped_column(String(100), default="")  # 负责人（按负责人分布）
    created: Mapped[str] = mapped_column(String(50), nullable=True)  # Jira 创建时间（ISO）
    last_comment_time: Mapped[str] = mapped_column(String(50), nullable=True)  # 最后评论时间（ISO，停滞基准）
    updated_at: Mapped[str] = mapped_column(String(50), nullable=True)  # Jira ISO 时间
    stagnant: Mapped[bool] = mapped_column(Boolean, default=False)
    stagnant_days: Mapped[int] = mapped_column(Integer, default=0)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_focus_member_set", "focus_set_id"),
        Index("ix_focus_member_key", "issue_key"),
    )
