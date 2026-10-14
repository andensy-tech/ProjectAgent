"""add epic weekly baseline to focus_sets

Revision ID: g7a8b9c0d1e2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-01

为 epic 类型 FocusSet 增加周基线字段，支持以每周一成员总数为分母计算 fix rate。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'g7a8b9c0d1e2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("focus_sets", sa.Column("baseline_total", sa.Integer(), nullable=True))
    op.add_column("focus_sets", sa.Column("baseline_date", sa.Date(), nullable=True))
    op.add_column("focus_sets", sa.Column("baseline_members", sa.JSON(), nullable=True))
    op.add_column("focus_sets", sa.Column("last_done_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("focus_sets", "last_done_count")
    op.drop_column("focus_sets", "baseline_members")
    op.drop_column("focus_sets", "baseline_date")
    op.drop_column("focus_sets", "baseline_total")
