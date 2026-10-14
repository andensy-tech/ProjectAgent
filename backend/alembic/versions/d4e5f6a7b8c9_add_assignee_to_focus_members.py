"""add assignee to focus_set_members

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-11

支持看板按负责人分布展示（P-C 增强）。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "focus_set_members",
        sa.Column("assignee", sa.String(100), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("focus_set_members", "assignee")
