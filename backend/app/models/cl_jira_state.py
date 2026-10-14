"""
ClJiraState model — CL 监控的「追踪队列」，一题一行。

背景（2026-09-16 CL 监控 V2）：旧模型每次刷新只把「评论里有 Gerrit 链接」的题
写进 JSON 快照，无法感知 Jira 状态迁移（非 Resolved → Resolved/Verified/Closed），
也无法承载"每天提醒一次"的时间戳。V2 改为按题建行：

  - 入队：配置的 JQL 命中的题全部入队，只进不出，直到 CL 全部合并且已 Closed
  - 跟踪：每轮扫描更新 status/prev_status，用于识别状态迁移
  - 焦点：状态进入 Resolved/Verified/Closed 的题进入重点监控（in_focus）
  - CL：本行的 cl_* 字段是该题 CL 合并状态的最近一次确认结果
  - 提醒：last_reminded_at 支撑"每天提醒一次"（取代 cl_reminders 的"每状态一次"）

  - 确认：owner 在 Jira 评论里说明"CL 都已合并 / 该题不需要 CL / 已转移到别处跟进"
    时，LLM 判定该 Jira 已无需关注 -> 写 ack_at/ack_reason 停止提醒（仍留在队列里
    供 PM 复核；状态再变化或有新回复时自动清除）

本表取代 cl_verified_tracking 与 cl_reminders 两份 UserSetting JSON。
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, Integer, Boolean, Index, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class ClJiraState(Base, TimestampMixin):
    """CL 监控追踪队列中的一条 Jira 记录（per-user）。"""

    __tablename__ = "cl_jira_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    project_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    issue_key: Mapped[str] = mapped_column(String(50), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    assignee: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # ── 状态跟踪（变化检测的「上一状态」）──────────────────────────
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    prev_status: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    status_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── 追踪队列 ──────────────────────────────────────────────────
    queued: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    queue_reason: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exited_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── CL 确认结果 ───────────────────────────────────────────────
    cl_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cl_merged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cl_unmerged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cl_noregister: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cl_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cl_items_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    issue_updated: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    # ── 提醒 ──────────────────────────────────────────────────────
    last_reminded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reminded_status: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    # ── owner 已说明（ack）：停止提醒，但仍留在队列供 PM 复核 ──────
    ack_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ack_reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    first_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "issue_key", name="uq_cl_jira_states_user_issue"),
        Index("idx_cl_jira_states_queue", "user_id", "queued", "status"),
        {"extend_existing": True},
    )
