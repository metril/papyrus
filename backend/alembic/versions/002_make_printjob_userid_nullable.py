"""Make print_jobs.user_id nullable for network print jobs.

Revision ID: 002
Revises: 001
Create Date: 2026-03-11
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("print_jobs", "user_id", nullable=True)


def downgrade() -> None:
    op.alter_column("print_jobs", "user_id", nullable=False)
