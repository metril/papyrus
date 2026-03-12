"""Make scan_jobs.user_id nullable for eSCL network scan jobs.

Revision ID: 005
Revises: 004
Create Date: 2026-03-12
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("scan_jobs", "user_id", nullable=True)


def downgrade() -> None:
    op.alter_column("scan_jobs", "user_id", nullable=False)
