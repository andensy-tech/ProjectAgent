"""add created to focus_set_members

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-13

看板停滞需推动列表按 Jira ID/描述/owner/创建时间展示，需在成员快照中持久化 created。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "focus_set_members",
        sa.Column("created", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("focus_set_members", "created")
