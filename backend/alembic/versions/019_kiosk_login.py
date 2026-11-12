"""Kiosk login: per-user PIN + lockout columns, tap-to-approve fields.

Revision ID: 019
Revises: 018
Create Date: 2026-11-12
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("kiosk_pin_hash", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("kiosk_pin_failures", sa.Integer(), nullable=False,
                                     server_default="0"))
    op.add_column("users", sa.Column("kiosk_pin_consecutive_failures", sa.Integer(),
                                     nullable=False, server_default="0"))
    op.add_column("users", sa.Column("kiosk_pin_locked_until", sa.DateTime(timezone=True),
                                     nullable=True))
    op.add_column("device_authorizations", sa.Column(
        "target_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("device_authorizations", sa.Column("match_code", sa.Integer(), nullable=True))
    op.add_column("device_authorizations", sa.Column("choices", sa.JSON(), nullable=True))
    op.create_foreign_key("fk_device_auth_target_user", "device_authorizations", "users",
                          ["target_user_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_device_authorizations_target_user_id", "device_authorizations",
                    ["target_user_id"])


def downgrade() -> None:
    op.drop_index("ix_device_authorizations_target_user_id", "device_authorizations")
    op.drop_constraint("fk_device_auth_target_user", "device_authorizations", type_="foreignkey")
    for c in ("choices", "match_code", "target_user_id"):
        op.drop_column("device_authorizations", c)
    for c in ("kiosk_pin_locked_until", "kiosk_pin_consecutive_failures", "kiosk_pin_failures",
              "kiosk_pin_hash"):
        op.drop_column("users", c)
