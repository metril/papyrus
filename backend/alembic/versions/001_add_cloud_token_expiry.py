"""Add token_expiry to cloud_providers.

Revision ID: 001
Revises:
Create Date: 2026-03-10
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_providers",
        sa.Column("token_expiry", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cloud_providers", "token_expiry")
