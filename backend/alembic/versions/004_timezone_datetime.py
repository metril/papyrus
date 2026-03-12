"""Convert all DateTime columns to TIMESTAMPTZ

Revision ID: 004
Revises: 003
Create Date: 2026-03-12
"""
from alembic import op

revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None

COLUMNS = [
    ("users",           "created_at"),
    ("users",           "last_login"),
    ("api_tokens",      "expires_at"),
    ("api_tokens",      "created_at"),
    ("api_tokens",      "last_used_at"),
    ("print_jobs",      "created_at"),
    ("print_jobs",      "updated_at"),
    ("print_jobs",      "completed_at"),
    ("scan_jobs",       "created_at"),
    ("scan_jobs",       "completed_at"),
    ("smb_shares",      "created_at"),
    ("cloud_providers", "token_expiry"),
    ("cloud_providers", "connected_at"),
    ("app_config",      "updated_at"),
    ("printers",        "created_at"),
    ("scanners",        "created_at"),
]


def upgrade():
    for table, col in COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {col} "
            f"TYPE TIMESTAMPTZ USING {col} AT TIME ZONE 'UTC'"
        )


def downgrade():
    for table, col in COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {col} "
            f"TYPE TIMESTAMP WITHOUT TIME ZONE USING {col} AT TIME ZONE 'UTC'"
        )
