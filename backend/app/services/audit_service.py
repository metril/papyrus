"""Audit logging service."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEntry


async def log_event(
    db: AsyncSession,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    user_id: uuid.UUID | None = None,
    source: str = "web",
    ip_address: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Record an audit log entry.

    Actions follow the pattern: entity.verb, e.g.:
    - print.release, print.delete, print.upload
    - scan.complete, scan.delete, scan.upload
    - settings.update
    - cloud.upload
    - email.send
    """
    entry = AuditEntry(
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        user_id=user_id,
        source=source,
        ip_address=ip_address,
        detail=detail,
    )
    db.add(entry)
    # Don't commit here — let the caller's transaction handle it.
    # Use flush to persist without committing.
    await db.flush()
