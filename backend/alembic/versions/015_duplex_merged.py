"""Mark scans that took part in a two-pass double-sided merge.

True on the merged result and on the fronts scan it came from, so neither
is offered (or accepted) for another merge.

Revision ID: 015
Revises: 014
Create Date: 2026-11-08
"""
import sqlalchemy as sa
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scan_jobs",
        sa.Column("duplex_merged", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("scan_jobs", "duplex_merged")
