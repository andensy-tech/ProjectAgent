"""add cl_jira_states + ack columns

Revision ID: i9c0d1e2f3a4
Revises: h8b9c0d1e2f3
Create Date: 2026-09-18

CL 监控 V2 追踪队列：一题一行，承载状态跟踪 / 焦点集合 / CL 确认结果 /
提醒去重（last_reminded_at）/ 经办人已说明（ack_at）。
生产库惯例：不走 alembic（alembic_version 为空，create_all 建表 + 手动 ALTER TABLE），
本迁移供规范化环境使用；生产库已手工执行等价建表与加列。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'i9c0d1e2f3a4'
down_revision = 'h8b9c0d1e2f3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cl_jira_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("project_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("issue_key", sa.String(50), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False, server_default=""),
        sa.Column("assignee", sa.String(200), nullable=False, server_default=""),
        sa.Column("status", sa.String(50), nullable=False, server_default="", index=True),
        sa.Column("prev_status", sa.String(50), nullable=False, server_default=""),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("queued", sa.Boolean(), nullable=False, server_default="1", index=True),
        sa.Column("queue_reason", sa.String(30), nullable=False, server_default=""),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("exited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cl_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cl_merged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cl_unmerged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cl_noregister", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("cl_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cl_items_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("issue_updated", sa.String(50), nullable=False, server_default=""),
        sa.Column("last_reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reminded_status", sa.String(50), nullable=False, server_default=""),
        sa.Column("ack_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ack_reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "issue_key", name="uq_cl_jira_states_user_issue"),
        sa.Index("idx_cl_jira_states_queue", "user_id", "queued", "status"),
    )
    # ack 两列（2026-09-18 追加）。生产库已手工 ALTER TABLE 加列。
    op.add_column("cl_jira_states",
                  sa.Column("ack_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cl_jira_states",
                  sa.Column("ack_reason", sa.String(500), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("cl_jira_states", "ack_reason")
    op.drop_column("cl_jira_states", "ack_at")
    op.drop_table("cl_jira_states")
