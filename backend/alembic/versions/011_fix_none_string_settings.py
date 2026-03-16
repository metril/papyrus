"""Fix AppConfig rows with literal string "None" stored by str(None) bug.

Replace with defaults where available, delete otherwise.

Revision ID: 011
Revises: 010
Create Date: 2026-03-16
"""
from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None

# Copied from settings.py DEFAULTS — only the keys that could be affected
DEFAULTS = {
    "scan_dir": "/app/data/scans",
    "upload_dir": "/app/data/uploads",
    "max_upload_size_mb": "50",
    "scan_retention_days": "7",
    "print_retention_days": "30",
    "scan_filename_template": "scan_{date}_{time}_{id}",
    "dev_mode": "false",
    "require_release_pin": "false",
    "smtp_port": "587",
    "ocr_enabled": "false",
    "ocr_language": "eng",
    "ftp_port": "21",
    "ftp_remote_dir": "/",
    "ftp_protocol": "ftp",
    "email_webhook_rate_limit": "10",
    "escl_enabled": "true",
    "local_auth_enabled": "true",
    "oidc_enabled": "false",
    "oidc_scopes": "openid email profile",
}


def upgrade():
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT key FROM app_config WHERE value = 'None'")
    ).fetchall()
    for (key,) in rows:
        if key in DEFAULTS:
            conn.execute(
                sa.text("UPDATE app_config SET value = :val WHERE key = :key"),
                {"val": DEFAULTS[key], "key": key},
            )
        else:
            conn.execute(
                sa.text("DELETE FROM app_config WHERE key = :key"),
                {"key": key},
            )


def downgrade():
    pass  # non-reversible data fix
