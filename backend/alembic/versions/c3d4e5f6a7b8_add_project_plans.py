"""add project_plans and project_plan_phases

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-10

P-D：项目计划知识库（阶段/里程碑/目标日期，Agent 判断项目进展的时间基准）。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_name", sa.String(200), nullable=False),
        sa.Column("overall_status", sa.String(100), nullable=True),
        sa.Column("source", sa.String(30), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_plans_id", "project_plans", ["id"])
    op.create_index("ix_project_plans_user_id", "project_plans", ["user_id"])
    op.create_index("ix_project_plan_user_name", "project_plans", ["user_id", "project_name"], unique=True)

    op.create_table(
        "project_plan_phases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("phase_name", sa.String(200), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="planned"),
        sa.Column("deliverables", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["plan_id"], ["project_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_plan_phases_id", "project_plan_phases", ["id"])
    op.create_index("ix_plan_phase_plan", "project_plan_phases", ["plan_id"])


def downgrade() -> None:
    op.drop_table("project_plan_phases")
    op.drop_table("project_plans")
