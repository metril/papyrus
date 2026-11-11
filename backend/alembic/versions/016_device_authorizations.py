"""Device authorization grants (RFC 8628-style kiosk sign-in).

Revision ID: 016
Revises: 015
Create Date: 2026-11-10
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_authorizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("device_code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_code", sa.String(9), nullable=False, unique=True),
        sa.Column("client_name", sa.String(64), nullable=False),
        sa.Column(
            "requester_token_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("api_tokens.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "approved_user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column(
            "minted_token_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("api_tokens.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("interval", sa.Integer(), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_device_authorizations_requester_token_id", "device_authorizations",
        ["requester_token_id"],
    )
    op.create_index(
        "ix_device_authorizations_minted_token_id", "device_authorizations",
        ["minted_token_id"],
    )


def downgrade() -> None:
    op.drop_table("device_authorizations")
