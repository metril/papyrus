"""Per-user is_active and kiosk_enabled flags.

Revision ID: 020
Revises: 019
Create Date: 2026-11-12
"""
import sqlalchemy as sa

from alembic import op

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False,
                                     server_default=sa.true()))
    op.add_column("users", sa.Column("kiosk_enabled", sa.Boolean(), nullable=False,
                                     server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("users", "kiosk_enabled")
    op.drop_column("users", "is_active")
