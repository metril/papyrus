"""Add make_and_model and location columns to printers.

Stores device info gathered by mDNS discovery / IPP probing.

Revision ID: 013
Revises: 012
Create Date: 2026-07-05
"""
import sqlalchemy as sa
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("printers", sa.Column("make_and_model", sa.String(255), nullable=True))
    op.add_column("printers", sa.Column("location", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("printers", "location")
    op.drop_column("printers", "make_and_model")
