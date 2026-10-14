"""add focus_sets and focus_set_members

Revision ID: a1b2c3d4e5f6
Revises: 3e4dd273da97
Create Date: 2026-08-10

P-B：PM 关注集合（FocusSet）主表 + 成员子表，支撑看板状态图 + 到期提醒。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '3e4dd273da97'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "focus_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(20), nullable=False, server_default="filter"),
        sa.Column("identifier", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("jql", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("metric_type", sa.String(30), nullable=True),
        sa.Column("metric_target", sa.Float(), nullable=True),
        sa.Column("priority_semantics", sa.String(100), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("command_history", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_focus_sets_id", "focus_sets", ["id"])
    op.create_index("ix_focus_sets_user_id", "focus_sets", ["user_id"])
    op.create_index("ix_focus_set_ident", "focus_sets", ["user_id", "type", "identifier"], unique=True)

    op.create_table(
        "focus_set_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("focus_set_id", sa.Integer(), nullable=False),
        sa.Column("issue_key", sa.String(50), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False, server_default=""),
        sa.Column("status", sa.String(50), nullable=False, server_default=""),
        sa.Column("priority", sa.String(20), nullable=False, server_default=""),
        sa.Column("updated_at", sa.String(50), nullable=True),
        sa.Column("stagnant", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("stagnant_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snapshot_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["focus_set_id"], ["focus_sets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_focus_set_members_id", "focus_set_members", ["id"])
    op.create_index("ix_focus_member_set", "focus_set_members", ["focus_set_id"])
    op.create_index("ix_focus_member_key", "focus_set_members", ["issue_key"])


def downgrade() -> None:
    op.drop_table("focus_set_members")
    op.drop_table("focus_sets")
