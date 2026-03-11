"""Add printers and scanners tables

Revision ID: 003
Revises: 002
Create Date: 2026-03-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "printers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("cups_name", sa.String(100), unique=True, nullable=False),
        sa.Column("uri", sa.String(255), nullable=False, server_default=""),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_network_queue", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("auto_release", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "scanners",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("device", sa.String(255), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("auto_deliver", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("post_scan_config", JSON, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.add_column(
        "print_jobs",
        sa.Column(
            "printer_id",
            sa.Integer,
            sa.ForeignKey("printers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.add_column(
        "scan_jobs",
        sa.Column(
            "scanner_id",
            sa.Integer,
            sa.ForeignKey("scanners.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("scan_jobs", "scanner_id")
    op.drop_column("print_jobs", "printer_id")
    op.drop_table("scanners")
    op.drop_table("printers")
