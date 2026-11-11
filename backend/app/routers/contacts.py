"""Personal address book: every operation is scoped to the caller's own contacts."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_docs import AUTH_ERRORS, NOT_FOUND, VALIDATION, std_responses
from app.auth.dependencies import require_permission
from app.database import get_db
from app.exceptions import NotFoundError
from app.models import Contact, User
from app.schemas.kiosks import ContactCreate, ContactResponse

router = APIRouter()


async def _get(db: AsyncSession, user: User, contact_id: uuid.UUID) -> Contact:
    c = (await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.owner_id == user.id)
    )).scalar_one_or_none()
    if c is None:
        raise NotFoundError("Contact not found")
    return c


@router.get("", summary="List contacts", response_model=list[ContactResponse],
            responses=std_responses(AUTH_ERRORS))
async def list_contacts(
    user: User = Depends(require_permission("scan")), db: AsyncSession = Depends(get_db)
):
    """List your own contacts, by name (sessions, or tokens with `scan`)."""
    q = select(Contact).where(Contact.owner_id == user.id).order_by(Contact.name, Contact.email)
    return (await db.execute(q)).scalars().all()


@router.post("", summary="Add a contact", response_model=ContactResponse, status_code=201,
             responses=std_responses(AUTH_ERRORS, VALIDATION))
async def create_contact(
    body: ContactCreate, user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Add a contact to your address book (sessions, or tokens with `scan`)."""
    c = Contact(owner_id=user.id, name=body.name, email=body.email)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


@router.put("/{contact_id}", summary="Update a contact", response_model=ContactResponse,
            responses=std_responses(AUTH_ERRORS, NOT_FOUND, VALIDATION))
async def update_contact(
    contact_id: uuid.UUID, body: ContactCreate,
    user: User = Depends(require_permission("scan")), db: AsyncSession = Depends(get_db),
):
    """Replace a contact's name and email (own contacts only; sessions or `scan` tokens)."""
    c = await _get(db, user, contact_id)
    c.name, c.email = body.name, body.email
    c.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(c)
    return c


@router.delete("/{contact_id}", summary="Delete a contact", status_code=204,
               responses=std_responses(AUTH_ERRORS, NOT_FOUND))
async def delete_contact(
    contact_id: uuid.UUID, user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a contact (own contacts only; sessions or `scan` tokens)."""
    c = await _get(db, user, contact_id)
    await db.delete(c)
    await db.commit()
