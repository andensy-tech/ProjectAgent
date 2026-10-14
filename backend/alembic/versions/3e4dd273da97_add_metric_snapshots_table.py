"""add metric_snapshots table

Revision ID: 3e4dd273da97
Revises: 7cd5a9f6beaf
Create Date: 2026-06-18 10:20:53.551224

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3e4dd273da97'
down_revision = '7cd5a9f6beaf'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metric_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("snapshot_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_issues", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("p0_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stale_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eta_expiring", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unmerged_cls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extra_metrics", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_snap_project_time", "metric_snapshots",
        ["project_name", "snapshot_time"],
    )


def downgrade() -> None:
    op.drop_index("ix_snap_project_time", table_name="metric_snapshots")
    op.drop_table("metric_snapshots")
