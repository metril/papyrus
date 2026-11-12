"""Kiosk sign-in configuration, user list and per-user PIN management."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_docs import AUTH_ERRORS, VALIDATION, std_responses
from app.auth.dependencies import get_current_user, require_permission
from app.database import get_db
from app.models import User
from app.routers.auth import _ph
from app.schemas import OkResponse
from app.schemas.device import KioskLoginConfig, KioskLoginUser, KioskPinSet, KioskPinStatus
from app.services import kiosk_login as kl
from app.services.audit_service import log_event

router = APIRouter()


def _require_session(request: Request) -> None:
    if getattr(request.state, "token_permissions", None) is not None:
        raise HTTPException(status_code=403, detail="A signed-in user session is required")


@router.get(
    "/kiosk-login/config",
    summary="Kiosk sign-in options",
    response_model=KioskLoginConfig,
    responses=std_responses(AUTH_ERRORS),
)
async def kiosk_login_config(
    _user: User = Depends(require_permission("device")),
    db: AsyncSession = Depends(get_db),
):
    """Which kiosk sign-in methods the admin enabled, and the required PIN length. Needs the
    `device` permission."""
    return KioskLoginConfig(
        approve=await kl.flag(db, "kiosk_approve_enabled"),
        pin=await kl.flag(db, "kiosk_pin_enabled"),
        pin_length=await kl.pin_length(db),
    )


@router.get(
    "/kiosk-login/users",
    summary="List users for kiosk sign-in",
    response_model=list[KioskLoginUser],
    responses=std_responses(AUTH_ERRORS),
)
async def kiosk_login_users(
    _user: User = Depends(require_permission("device")),
    db: AsyncSession = Depends(get_db),
):
    """Users who can sign in at a kiosk (kiosk service users excluded), with whether each has
    a PIN. Needs the `device` permission."""
    rows = (await db.execute(kl.eligible_users().order_by(User.display_name))).scalars().all()
    return [KioskLoginUser(id=u.id, display_name=u.display_name,
                           has_pin=u.kiosk_pin_hash is not None) for u in rows]


@router.get(
    "/me/kiosk-pin",
    summary="Get my kiosk PIN status",
    response_model=KioskPinStatus,
    responses=std_responses(AUTH_ERRORS),
)
async def get_kiosk_pin_status(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Whether PIN sign-in is enabled, the required length, and whether the caller has a PIN
    (signed-in session only)."""
    _require_session(request)
    return KioskPinStatus(enabled=await kl.flag(db, "kiosk_pin_enabled"),
                          pin_length=await kl.pin_length(db),
                          has_pin=user.kiosk_pin_hash is not None)


@router.put(
    "/me/kiosk-pin",
    summary="Set my kiosk PIN",
    response_model=OkResponse,
    responses=std_responses(AUTH_ERRORS, VALIDATION),
)
async def set_kiosk_pin(
    data: KioskPinSet,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set or replace the caller's kiosk PIN (session only): digits only, exactly the
    configured length (422 otherwise). Resets the failure counters and any lock."""
    _require_session(request)
    length = await kl.pin_length(db)
    if not (data.pin.isascii() and data.pin.isdigit() and len(data.pin) == length):
        raise HTTPException(status_code=422, detail=f"PIN must be exactly {length} digits")
    hashed = await asyncio.to_thread(_ph.hash, data.pin)
    row = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    row.kiosk_pin_hash = hashed
    row.kiosk_pin_failures = 0
    row.kiosk_pin_consecutive_failures = 0
    row.kiosk_pin_locked_until = None
    await log_event(db, "kiosk.pin_set", "user", str(user.id), user_id=user.id)
    await db.commit()
    return {"ok": True}


@router.delete(
    "/me/kiosk-pin",
    summary="Clear my kiosk PIN",
    response_model=OkResponse,
    responses=std_responses(AUTH_ERRORS),
)
async def clear_kiosk_pin(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove the caller's kiosk PIN (session only)."""
    _require_session(request)
    row = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    row.kiosk_pin_hash = None
    row.kiosk_pin_failures = 0
    row.kiosk_pin_consecutive_failures = 0
    row.kiosk_pin_locked_until = None
    await log_event(db, "kiosk.pin_clear", "user", str(user.id), user_id=user.id)
    await db.commit()
    return {"ok": True}


