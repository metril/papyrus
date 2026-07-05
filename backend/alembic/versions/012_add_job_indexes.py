"""Add indexes on print_jobs.created_at and scan_jobs.created_at.

Speeds up history/dashboard queries that sort or filter by recency.

Revision ID: 012
Revises: 011
Create Date: 2026-07-05
"""
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_print_jobs_created_at", "print_jobs", ["created_at"])
    op.create_index("ix_scan_jobs_created_at", "scan_jobs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_scan_jobs_created_at", table_name="scan_jobs")
    op.drop_index("ix_print_jobs_created_at", table_name="print_jobs")
