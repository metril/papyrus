"""Shared address book."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_docs import AUTH_ERRORS, NOT_FOUND, VALIDATION, std_responses
from app.auth.dependencies import require_admin, require_permission
from app.database import get_db
from app.exceptions import NotFoundError
from app.models import Contact, User
from app.schemas.kiosks import ContactCreate, ContactResponse

router = APIRouter()


async def _get(db: AsyncSession, contact_id: uuid.UUID) -> Contact:
    c = (await db.execute(select(Contact).where(Contact.id == contact_id))).scalar_one_or_none()
    if c is None:
        raise NotFoundError("Contact not found")
    return c


@router.get("", summary="List contacts", response_model=list[ContactResponse],
            responses=std_responses(AUTH_ERRORS))
async def list_contacts(
    user: User = Depends(require_permission("scan")), db: AsyncSession = Depends(get_db)
):
    """List the address book, by name (sessions, or tokens with `scan`)."""
    return (await db.execute(select(Contact).order_by(Contact.name, Contact.email))).scalars().all()


@router.post("", summary="Add a contact", response_model=ContactResponse, status_code=201,
             responses=std_responses(AUTH_ERRORS, VALIDATION))
async def create_contact(
    body: ContactCreate, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    """Add an address-book contact (admin)."""
    c = Contact(name=body.name, email=body.email)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


@router.put("/{contact_id}", summary="Update a contact", response_model=ContactResponse,
            responses=std_responses(AUTH_ERRORS, NOT_FOUND, VALIDATION))
async def update_contact(
    contact_id: uuid.UUID, body: ContactCreate,
    user: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    """Replace a contact's name and email (admin)."""
    c = await _get(db, contact_id)
    c.name, c.email = body.name, body.email
    c.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(c)
    return c


@router.delete("/{contact_id}", summary="Delete a contact", status_code=204,
               responses=std_responses(AUTH_ERRORS, NOT_FOUND))
async def delete_contact(
    contact_id: uuid.UUID, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    """Delete a contact (admin)."""
    c = await _get(db, contact_id)
    await db.delete(c)
    await db.commit()
