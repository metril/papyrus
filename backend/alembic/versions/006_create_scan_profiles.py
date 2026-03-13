"""Create scan_profiles table.

Revision ID: 006
Revises: 005
Create Date: 2026-03-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scan_profiles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("resolution", sa.Integer(), server_default="300"),
        sa.Column("color_mode", sa.String(20), server_default="Color"),
        sa.Column("format", sa.String(10), server_default="pdf"),
        sa.Column("source", sa.String(20), server_default="Flatbed"),
        sa.Column("ocr_enabled", sa.Boolean(), server_default="false"),
        sa.Column("post_actions", JSON(), nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("scan_profiles")
