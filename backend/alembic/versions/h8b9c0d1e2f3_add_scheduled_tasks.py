"""add scheduled_tasks

Revision ID: h8b9c0d1e2f3
Revises: g7a8b9c0d1e2
Create Date: 2026-09-09

Agent 通用定时任务：主表支撑声明式规则引擎 + 看板管理。
生产库惯例：不走 alembic（alembic_version 为空，create_all 建表），
新表需在生产库手工执行等价 CREATE TABLE。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'h8b9c0d1e2f3'
down_revision = 'g7a8b9c0d1e2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("task_type", sa.String(30), nullable=False, server_default="declarative_rule"),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("run_until", sa.Date(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(300), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduled_tasks_id", "scheduled_tasks", ["id"])
    op.create_index("ix_scheduled_tasks_user_id", "scheduled_tasks", ["user_id"])
    op.create_index("ix_sched_task_due", "scheduled_tasks", ["active", "next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_sched_task_due", table_name="scheduled_tasks")
    op.drop_index("ix_scheduled_tasks_user_id", table_name="scheduled_tasks")
    op.drop_index("ix_scheduled_tasks_id", table_name="scheduled_tasks")
    op.drop_table("scheduled_tasks")
