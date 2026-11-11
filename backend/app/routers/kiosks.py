"""Kiosk identities (admin): a non-login service user plus a dedicated API token.

`last_seen_at` is stamped by `auth.dependencies.get_current_user` on any request made with the
kiosk's token (throttled, in-process).
"""
import re
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_docs import AUTH_ERRORS, CONFLICT, NOT_FOUND, VALIDATION, std_responses
from app.auth.dependencies import require_admin
from app.auth.tokens import generate_token
from app.database import get_db
from app.exceptions import ConflictError, NotFoundError
from app.models import APIToken, Kiosk, User
from app.schemas.kiosks import (
    KioskCreate,
    KioskResponse,
    KioskTokenResponse,
    KioskUpdate,
)
from app.services.audit_service import log_event
from app.services.user_deletion import reassign_jobs

router = APIRouter()

KIOSK_PERMISSIONS = ["print", "scan", "device"]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "kiosk"


async def _get(db: AsyncSession, kiosk_id: uuid.UUID) -> Kiosk:
    k = (await db.execute(select(Kiosk).where(Kiosk.id == kiosk_id))).scalar_one_or_none()
    if k is None:
        raise NotFoundError("Kiosk not found")
    return k


def _mint_token(db: AsyncSession, user: User, name: str) -> tuple[APIToken, str]:
    plaintext, token_hash = generate_token()
    token = APIToken(
        user_id=user.id, name=f"kiosk: {name}"[:100], token_hash=token_hash,
        permissions=list(KIOSK_PERMISSIONS), expires_at=None,
    )
    db.add(token)
    return token, plaintext


@router.get("", summary="List kiosks", response_model=list[KioskResponse],
            responses=std_responses(AUTH_ERRORS))
async def list_kiosks(user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """List kiosks with their last-seen time (admin)."""
    return (await db.execute(select(Kiosk).order_by(Kiosk.name))).scalars().all()


@router.post("", summary="Create a kiosk", response_model=KioskTokenResponse, status_code=201,
             responses=std_responses(AUTH_ERRORS, CONFLICT, VALIDATION))
async def create_kiosk(
    body: KioskCreate, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    """Create a kiosk: a dedicated service user (role `user`, no password, cannot log in) and a
    non-expiring API token with permissions `print`, `scan` and `device`. The plaintext token is
    returned once (admin)."""
    if (await db.execute(select(Kiosk.id).where(Kiosk.name == body.name))).first():
        raise ConflictError("A kiosk with this name already exists")
    username = f"kiosk-{_slug(body.name)}"
    if (await db.execute(select(User.id).where(User.username == username))).first():
        username = f"{username}-{secrets.token_hex(3)}"
    svc = User(
        email=f"{username}@kiosk.invalid", display_name=body.name, role="user",
        is_local=True, password_hash=None, username=username,
    )
    db.add(svc)
    await db.flush()
    token, plaintext = _mint_token(db, svc, body.name)
    await db.flush()
    k = Kiosk(name=body.name, token_id=token.id, service_user_id=svc.id)
    db.add(k)
    await db.flush()
    await log_event(db, "kiosk.create", "kiosk", str(k.id), user_id=user.id,
                    detail={"name": body.name})
    await db.commit()
    await db.refresh(k)
    return KioskTokenResponse(kiosk=KioskResponse.model_validate(k, from_attributes=True),
                              token=plaintext)


@router.get("/{kiosk_id}", summary="Get a kiosk", response_model=KioskResponse,
            responses=std_responses(AUTH_ERRORS, NOT_FOUND))
async def get_kiosk(
    kiosk_id: uuid.UUID, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    """Get one kiosk (admin). The token is never returned again after creation."""
    return await _get(db, kiosk_id)


@router.put("/{kiosk_id}", summary="Rename a kiosk", response_model=KioskResponse,
            responses=std_responses(AUTH_ERRORS, NOT_FOUND, CONFLICT, VALIDATION))
async def update_kiosk(
    kiosk_id: uuid.UUID, body: KioskUpdate,
    user: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    """Rename a kiosk (admin); the service user's display name follows."""
    k = await _get(db, kiosk_id)
    if body.name != k.name:
        if (await db.execute(select(Kiosk.id).where(Kiosk.name == body.name))).first():
            raise ConflictError("A kiosk with this name already exists")
        k.name = body.name
        svc = (await db.execute(select(User).where(User.id == k.service_user_id))).scalar_one()
        svc.display_name = body.name
        k.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(k)
    return k


@router.delete("/{kiosk_id}", summary="Delete a kiosk", status_code=204,
               responses=std_responses(AUTH_ERRORS, NOT_FOUND))
async def delete_kiosk(
    kiosk_id: uuid.UUID, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    """Revoke the kiosk's token and delete the kiosk and its service user; the kiosk's scan and
    print jobs are reassigned to the acting admin (admin)."""
    k = await _get(db, kiosk_id)
    svc = (await db.execute(select(User).where(User.id == k.service_user_id))).scalar_one_or_none()
    await log_event(db, "kiosk.delete", "kiosk", str(k.id), user_id=user.id,
                    detail={"name": k.name})
    if svc is not None:
        await reassign_jobs(db, svc.id, user.id)
        await db.delete(svc)  # cascades to its tokens and (by FK) the kiosk row
    else:
        await db.delete(k)
    await db.commit()


@router.post("/{kiosk_id}/rotate-token", summary="Rotate a kiosk's token",
             response_model=KioskTokenResponse,
             responses=std_responses(AUTH_ERRORS, NOT_FOUND))
async def rotate_kiosk_token(
    kiosk_id: uuid.UUID, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    """Revoke the kiosk's current token and mint a new one with the same permissions. The new
    plaintext token is returned once (admin)."""
    k = await _get(db, kiosk_id)
    svc = (await db.execute(select(User).where(User.id == k.service_user_id))).scalar_one()
    old = None
    if k.token_id:
        res = await db.execute(select(APIToken).where(APIToken.id == k.token_id))
        old = res.scalar_one_or_none()
    token, plaintext = _mint_token(db, svc, k.name)
    if old is not None:
        token.permissions = list(old.permissions)
    await db.flush()
    k.token_id = token.id
    k.updated_at = datetime.now(timezone.utc)
    if old is not None:
        await db.delete(old)
    await log_event(db, "kiosk.rotate_token", "kiosk", str(k.id), user_id=user.id)
    await db.commit()
    await db.refresh(k)
    return KioskTokenResponse(kiosk=KioskResponse.model_validate(k, from_attributes=True),
                              token=plaintext)
