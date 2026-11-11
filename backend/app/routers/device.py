"""Device authorization grant (RFC 8628-style): a kiosk's shared API token asks for a
per-user token, the user approves on their phone via a QR code."""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_docs import (
    AUTH_ERRORS,
    BAD_REQUEST,
    CONFLICT,
    NOT_FOUND,
    RATE_LIMITED,
    VALIDATION,
    std_responses,
)
from app.auth.dependencies import get_current_user, require_permission
from app.auth.tokens import generate_token, hash_token
from app.config import settings
from app.database import get_db
from app.exceptions import ConflictError, DeviceGrantError, ExternalServiceError, NotFoundError
from app.models import APIToken, DeviceAuthorization, User
from app.schemas.device import (
    DeviceApproveRequest,
    DeviceApproveResponse,
    DeviceCodeRequest,
    DeviceCodeResponse,
    DeviceLookupResponse,
    DeviceTokenRequest,
    DeviceTokenResponse,
    DeviceTokenUser,
)
from app.services.audit_service import log_event
from app.services.rate_limit import RateLimiter, retry_after_seconds

router = APIRouter()

DEVICE_SCOPES = ["print", "scan", "files", "email"]
TOKEN_TTL_MINUTES = 15
EXPIRES_IN = 300
BASE_INTERVAL = 5
_USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L
_PRUNE_GRACE = timedelta(hours=1)

# Process-local, per key (see services/rate_limit.py).
_code_limiter = RateLimiter()
_poll_limiter = RateLimiter()
_approve_limiter = RateLimiter()


def _limit(limiter: RateLimiter, key: str, rpm: int) -> None:
    wait = limiter.check(key, rpm)
    if wait:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Try again later.",
            headers={"Retry-After": str(retry_after_seconds(wait))},
        )


def _normalize_user_code(raw: str) -> str:
    s = "".join(c for c in raw.upper() if c.isalnum())
    return f"{s[:4]}-{s[4:]}" if len(s) == 8 else s


def _new_user_code() -> str:
    s = "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(8))
    return f"{s[:4]}-{s[4:]}"


def _require_session(request: Request) -> None:
    if getattr(request.state, "token_permissions", None) is not None:
        raise HTTPException(status_code=403, detail="A signed-in user session is required")


def _expired(row: DeviceAuthorization) -> bool:
    return row.expires_at <= datetime.now(timezone.utc)


@router.post(
    "/code",
    summary="Request a device code",
    response_model=DeviceCodeResponse,
    responses=std_responses(AUTH_ERRORS, VALIDATION, RATE_LIMITED),
)
async def request_device_code(
    request: Request,
    data: DeviceCodeRequest | None = None,
    user: User = Depends(require_permission("device")),
    db: AsyncSession = Depends(get_db),
):
    """Start a device authorization (API token with the `device` permission only; sessions get
    403).

    Show `verification_uri_complete` as a QR code and `user_code` as text, then poll
    `POST /auth/device/token` every `interval` seconds. Limited to 10 requests/minute per token.
    """
    token_id = getattr(request.state, "token_id", None)
    if token_id is None:
        raise HTTPException(status_code=403, detail="An API token is required")
    _limit(_code_limiter, str(token_id), 10)

    now = datetime.now(timezone.utc)
    await db.execute(delete(DeviceAuthorization).where(
        DeviceAuthorization.expires_at < now - _PRUNE_GRACE))

    device_code = secrets.token_urlsafe(32)
    for _ in range(20):
        user_code = _new_user_code()
        taken = await db.execute(select(DeviceAuthorization.id).where(
            DeviceAuthorization.user_code == user_code))
        if taken.first() is None:
            break
    else:
        raise ExternalServiceError("Could not allocate a sign-in code. Try again.")
    db.add(DeviceAuthorization(
        device_code_hash=hash_token(device_code),
        user_code=user_code,
        client_name=((data.client_name if data else None) or "Kiosk").strip()[:64] or "Kiosk",
        requester_token_id=token_id,
        status="pending",
        interval=BASE_INTERVAL,
        expires_at=now + timedelta(seconds=EXPIRES_IN),
    ))
    await db.commit()

    base = settings.base_url.rstrip("/")
    return DeviceCodeResponse(
        device_code=device_code,
        user_code=user_code,
        verification_uri=f"{base}/device",
        verification_uri_complete=f"{base}/device?code={user_code}",
        interval=BASE_INTERVAL,
        expires_in=EXPIRES_IN,
    )


async def _find_by_user_code(db: AsyncSession, raw: str) -> DeviceAuthorization:
    result = await db.execute(select(DeviceAuthorization).where(
        DeviceAuthorization.user_code == _normalize_user_code(raw)))
    row = result.scalar_one_or_none()
    if row is None or _expired(row):
        raise NotFoundError("Code not found or expired")
    return row


@router.get(
    "/{user_code}",
    summary="Look up a device code",
    response_model=DeviceLookupResponse,
    responses=std_responses(AUTH_ERRORS, NOT_FOUND, RATE_LIMITED),
)
async def lookup_device_code(
    user_code: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Describe a pending device request for the approval page (signed-in session only).

    404 when the code is unknown or expired. Limited to 20 requests/minute per user.
    """
    _require_session(request)
    _limit(_approve_limiter, str(user.id), 20)
    row = await _find_by_user_code(db, user_code)
    return DeviceLookupResponse(
        client_name=row.client_name,
        scopes=DEVICE_SCOPES,
        token_ttl_minutes=TOKEN_TTL_MINUTES,
        expires_at=row.expires_at,
        status=row.status,
    )


@router.post(
    "/approve",
    summary="Approve or deny a device request",
    response_model=DeviceApproveResponse,
    responses=std_responses(AUTH_ERRORS, NOT_FOUND, CONFLICT, VALIDATION, RATE_LIMITED),
)
async def approve_device(
    data: DeviceApproveRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve or deny a pending device request as the signed-in user (session only; API-token
    callers get 403).

    404 when the code is unknown or expired, 409 when it was already decided. Limited to 20
    requests/minute per user.
    """
    _require_session(request)
    _limit(_approve_limiter, str(user.id), 20)
    row = await _find_by_user_code(db, data.user_code)
    new_status = "approved" if data.approve else "denied"
    values = {"status": new_status}
    if data.approve:
        values["approved_user_id"] = user.id
    res = await db.execute(
        update(DeviceAuthorization)
        .where(DeviceAuthorization.id == row.id, DeviceAuthorization.status == "pending")
        .values(**values)
        .returning(DeviceAuthorization.id)
    )
    if res.first() is None:
        raise ConflictError("This request was already handled")
    await log_event(
        db, "device.approve" if data.approve else "device.deny", "device_authorization",
        str(row.id), user_id=user.id, detail={"client_name": row.client_name},
    )
    await db.commit()
    return DeviceApproveResponse(status=new_status)


@router.post(
    "/token",
    summary="Poll for the device token",
    response_model=DeviceTokenResponse,
    responses=std_responses(
        AUTH_ERRORS, VALIDATION, RATE_LIMITED,
        {400: {**BAD_REQUEST[400], "description": "Protocol error; the envelope `code` is one of "
               "authorization_pending, slow_down, access_denied, expired_token, invalid_grant."}},
    ),
)
async def poll_device_token(
    data: DeviceTokenRequest,
    request: Request,
    user: User = Depends(require_permission("device")),
    db: AsyncSession = Depends(get_db),
):
    """Exchange an approved `device_code` for a 15-minute API token owned by the approving user
    (print, scan, files, email). Single use.

    Must be called by the same API token that requested the code. Polling faster than `interval`
    returns `slow_down` and lengthens the interval by 5s. Errors are HTTP 400 with `code` =
    `authorization_pending`, `slow_down`, `access_denied`, `expired_token` or `invalid_grant`.
    Limited to 60 requests/minute per token.
    """
    caller_token_id = getattr(request.state, "token_id", None)
    if caller_token_id is None:
        raise HTTPException(status_code=403, detail="An API token is required")
    _limit(_poll_limiter, str(caller_token_id), 60)

    result = await db.execute(select(DeviceAuthorization).where(
        DeviceAuthorization.device_code_hash == hash_token(data.device_code)))
    row = result.scalar_one_or_none()
    if row is None or row.requester_token_id != caller_token_id or row.status == "consumed":
        raise DeviceGrantError("invalid_grant", "Invalid device code")

    now = datetime.now(timezone.utc)
    too_fast = row.last_polled_at is not None and (
        now - row.last_polled_at).total_seconds() < row.interval
    row.last_polled_at = now
    if too_fast:
        row.interval += 5
        await db.commit()
        raise DeviceGrantError("slow_down", "Polling too fast; increase the interval")
    if _expired(row):
        await db.commit()
        raise DeviceGrantError("expired_token", "The device code has expired")
    if row.status == "denied":
        await db.commit()
        raise DeviceGrantError("access_denied", "The request was denied")
    if row.status != "approved":
        await db.commit()
        raise DeviceGrantError("authorization_pending", "Waiting for the user to approve")

    # Single use: only one concurrent poll wins this conditional UPDATE.
    claimed = await db.execute(
        update(DeviceAuthorization)
        .where(DeviceAuthorization.id == row.id, DeviceAuthorization.status == "approved")
        .values(status="consumed", last_polled_at=now)
        .returning(DeviceAuthorization.approved_user_id, DeviceAuthorization.client_name)
    )
    claimed_row = claimed.first()
    if claimed_row is None:
        await db.rollback()
        raise DeviceGrantError("invalid_grant", "Invalid device code")
    approved_user_id, client_name = claimed_row
    owner = (await db.execute(select(User).where(User.id == approved_user_id))).scalar_one()

    plaintext, token_hash = generate_token()
    expires_at = now + timedelta(minutes=TOKEN_TTL_MINUTES)
    token = APIToken(
        user_id=owner.id,
        name=f"Device: {client_name}"[:100],
        token_hash=token_hash,
        permissions=list(DEVICE_SCOPES),
        expires_at=expires_at,
    )
    db.add(token)
    await db.flush()
    await db.execute(update(DeviceAuthorization).where(DeviceAuthorization.id == row.id)
                     .values(minted_token_id=token.id))
    await log_event(db, "device.token", "device_authorization", str(row.id), user_id=owner.id,
                    detail={"client_name": client_name, "token_id": str(token.id)})
    await db.commit()
    return DeviceTokenResponse(
        access_token=plaintext,
        token_id=token.id,
        expires_at=expires_at,
        scopes=list(DEVICE_SCOPES),
        user=DeviceTokenUser(id=owner.id, display_name=owner.display_name, email=owner.email),
    )
