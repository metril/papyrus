"""Audit remediation: BigInteger file_size, network-ingest dedupe key,
default-printer/scanner partial unique indexes, audit_log filter indexes,
cascade-delete on cloud_providers/smb_shares user FKs, and a widened
webhooks.secret column for a future Fernet-encrypted value.

Revision ID: 014
Revises: 013
Create Date: 2026-08-26
"""
import sqlalchemy as sa
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # F70: a plain int32 file_size overflows just over 2 GiB, which network
    # ingest can legitimately stream.
    op.alter_column("print_jobs", "file_size", type_=sa.BigInteger())
    op.alter_column("scan_jobs", "file_size", type_=sa.BigInteger())

    # F91: idempotency key (boot_id:printer:job_id) so a retried CUPS backend
    # POST is recognized instead of creating a duplicate held job.
    op.add_column("print_jobs", sa.Column("ingest_key", sa.String(128), nullable=True))
    op.create_index(
        "ux_print_jobs_ingest_key", "print_jobs", ["ingest_key"], unique=True
    )

    # F11: partial unique indexes make it impossible for two rows to persist
    # is_default=true, closing the race the old clear-then-set left open.
    op.create_index(
        "ux_printers_default", "printers", ["is_default"],
        unique=True, postgresql_where=sa.text("is_default"),
    )
    op.create_index(
        "ux_scanners_default", "scanners", ["is_default"],
        unique=True, postgresql_where=sa.text("is_default"),
    )

    # F69: GET /api/admin/audit filters on both columns; only created_at was
    # indexed.
    op.create_index(
        "ix_audit_log_action_created", "audit_log",
        ["action", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_audit_log_entity_created", "audit_log",
        ["entity_type", sa.text("created_at DESC")],
    )

    # F62: deleting a user with a connected cloud provider or SMB share used
    # to 500 on an FK violation -- cascade instead.
    op.drop_constraint("cloud_providers_user_id_fkey", "cloud_providers", type_="foreignkey")
    op.create_foreign_key(
        "cloud_providers_user_id_fkey", "cloud_providers", "users",
        ["user_id"], ["id"], ondelete="CASCADE",
    )
    op.drop_constraint("smb_shares_created_by_fkey", "smb_shares", type_="foreignkey")
    op.create_foreign_key(
        "smb_shares_created_by_fkey", "smb_shares", "users",
        ["created_by"], ["id"], ondelete="CASCADE",
    )

    # Coordinator ruling: webhooks.secret will hold a Fernet-encrypted value in
    # a later task, whose ciphertext exceeds 255 chars for a long secret.
    op.alter_column("webhooks", "secret", type_=sa.Text())


def downgrade() -> None:
    op.alter_column("webhooks", "secret", type_=sa.String(255))

    op.drop_constraint("smb_shares_created_by_fkey", "smb_shares", type_="foreignkey")
    op.create_foreign_key(
        "smb_shares_created_by_fkey", "smb_shares", "users", ["created_by"], ["id"],
    )
    op.drop_constraint("cloud_providers_user_id_fkey", "cloud_providers", type_="foreignkey")
    op.create_foreign_key(
        "cloud_providers_user_id_fkey", "cloud_providers", "users", ["user_id"], ["id"],
    )

    op.drop_index("ix_audit_log_entity_created", table_name="audit_log")
    op.drop_index("ix_audit_log_action_created", table_name="audit_log")

    op.drop_index("ux_scanners_default", table_name="scanners")
    op.drop_index("ux_printers_default", table_name="printers")

    op.drop_index("ux_print_jobs_ingest_key", table_name="print_jobs")
    op.drop_column("print_jobs", "ingest_key")

    op.alter_column("scan_jobs", "file_size", type_=sa.Integer())
    op.alter_column("print_jobs", "file_size", type_=sa.Integer())
