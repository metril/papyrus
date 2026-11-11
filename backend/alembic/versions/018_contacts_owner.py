"""Personal address books: contacts belong to a user.

Existing (shared) rows go to the oldest admin; with no admin they are deleted.

Revision ID: 018
Revises: 017
Create Date: 2026-11-11
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute(
        "UPDATE contacts SET owner_id = (SELECT id FROM users WHERE role = 'admin' "
        "ORDER BY created_at ASC, id ASC LIMIT 1)"
    )
    op.execute("DELETE FROM contacts WHERE owner_id IS NULL")
    op.alter_column("contacts", "owner_id", nullable=False)
    op.create_foreign_key(
        "fk_contacts_owner_id_users", "contacts", "users", ["owner_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_contacts_owner_id", "contacts", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_contacts_owner_id", table_name="contacts")
    op.drop_constraint("fk_contacts_owner_id_users", "contacts", type_="foreignkey")
    op.drop_column("contacts", "owner_id")
