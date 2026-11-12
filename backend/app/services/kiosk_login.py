"""Shared helpers for kiosk sign-in (tap-to-approve + PIN)."""
import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Kiosk, User
from app.routers.settings import get_setting, safe_int_setting
from app.services.ws_manager import ws_manager

logger = logging.getLogger(__name__)

PIN_LOCK_AFTER = 5  # failures (since the last lock) that lock the PIN
PIN_LOCK_SECONDS = 15 * 60
PIN_CLEAR_AFTER = 10  # consecutive failures (lock expiry doesn't reset) that clear the PIN


async def flag(db: AsyncSession, key: str) -> bool:
    return (await get_setting(db, key) or "false").lower() == "true"


async def pin_length(db: AsyncSession) -> int:
    return safe_int_setting(await get_setting(db, "kiosk_pin_length"), 6)


def eligible_users():
    """Users who may sign in at a kiosk: everyone except kiosk service users (the User model
    has no separate active flag)."""
    return select(User).where(User.id.notin_(select(Kiosk.service_user_id)))


async def get_eligible_user(db: AsyncSession, user_id: uuid.UUID, lock: bool = False):
    q = eligible_users().where(User.id == user_id)
    if lock:
        q = q.with_for_update()
    return (await db.execute(q)).scalar_one_or_none()


async def notify(user_id: uuid.UUID, type_: str, data: dict) -> None:
    await ws_manager.broadcast_user(user_id, {"type": type_, "data": data})


async def email_pin_cleared(db: AsyncSession, user: User) -> None:
    """Best-effort: tell the user their kiosk PIN was removed (no-op without SMTP/email)."""
    if not user.email:
        return
    try:
        from app.services.email_service import email_service
        await email_service.send_alert(
            db, user.email, "Your Papyrus kiosk PIN was removed",
            "Your kiosk PIN was removed after too many incorrect attempts. "
            "Set a new one from Settings > Account if you want to use PIN sign-in again.")
    except Exception:
        logger.info("Could not send PIN-cleared email", exc_info=True)


def dummy_verify_sync() -> None:
    from app.routers.auth import _dummy_hash, _ph
    try:
        _ph.verify(_dummy_hash(), "not-the-pin")
    except Exception:
        pass


async def dummy_verify() -> None:
    await asyncio.to_thread(dummy_verify_sync)
