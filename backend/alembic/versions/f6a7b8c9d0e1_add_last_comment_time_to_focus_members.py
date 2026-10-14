"""add last_comment_time to focus_set_members

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-13

停滞基准从 issue.updated 改为最后评论时间（停滞天数 = 今天 − 最后评论时间）。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "focus_set_members",
        sa.Column("last_comment_time", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("focus_set_members", "last_comment_time")
