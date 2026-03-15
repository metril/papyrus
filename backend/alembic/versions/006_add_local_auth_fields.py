"""Add local auth fields to users table.

Make oidc_sub nullable, add username, password_hash, is_local.

Revision ID: 006
Revises: 005
Create Date: 2026-03-14
"""
from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make oidc_sub nullable (allow local accounts without OIDC identity)
    op.alter_column("users", "oidc_sub", existing_type=sa.String(255), nullable=True)

    # Add local auth fields
    op.add_column("users", sa.Column("is_local", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("username", sa.String(100), nullable=True))
    op.create_unique_constraint("uq_users_username", "users", ["username"])


def downgrade() -> None:
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_column("users", "username")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "is_local")
    op.alter_column("users", "oidc_sub", existing_type=sa.String(255), nullable=False)
