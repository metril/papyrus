"""Helpers for deleting users without orphaning their jobs."""
import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PrintJob, ScanJob


async def reassign_jobs(db: AsyncSession, from_user_id: uuid.UUID, to_user_id: uuid.UUID) -> None:
    """Hand a user's scan and print jobs to another user before the user row is deleted.

    Without this the ORM nulls `user_id`, and null-owner scans are visible to every session user.
    """
    for model in (ScanJob, PrintJob):
        await db.execute(
            update(model).where(model.user_id == from_user_id).values(user_id=to_user_id)
            .execution_options(synchronize_session=False)
        )
