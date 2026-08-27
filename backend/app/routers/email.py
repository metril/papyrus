import logging
import secrets
import time
from collections import defaultdict

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_db
from app.models import AppConfig, User
from app.routers.jobs import _create_print_job_from_upload
from app.schemas import EmailConfig, EmailConfigStatus
from app.services import settings_cache
from app.services.convert_service import is_printable
from app.services.crypto import decrypt_value, encrypt_value
from app.services.email_service import email_service
from app.services.file_service import detect_mime_type

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory rate limiting for the /receive webhook. F57: keyed by the
# validated token (see receive_email) rather than the client/proxy IP, and
# idle buckets are evicted so a rotated token's bucket doesn't linger
# forever.
_webhook_requests: dict[str, list[float]] = defaultdict(list)
_BUCKET_IDLE_SECONDS = 3600.0


async def _get_smtp_config(db: AsyncSession) -> dict:
    """Load SMTP config from database."""
    result = await db.execute(
        select(AppConfig).where(AppConfig.key.like("smtp_%"))
    )
    rows = result.scalars().all()
    return {row.key: row.value for row in rows}


@router.get("/config", response_model=EmailConfigStatus)
async def get_email_config(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get SMTP configuration status (no secrets returned)."""
    db_config = await _get_smtp_config(db)
    return EmailConfigStatus(
        configured=email_service.is_configured(db_config),
        smtp_host=db_config.get("smtp_host"),
        smtp_from=db_config.get("smtp_from"),
    )


@router.put("/config")
async def update_email_config(
    data: EmailConfig,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update SMTP configuration (admin only)."""
    config_items = {
        "smtp_host": data.smtp_host,
        "smtp_port": str(data.smtp_port),
        "smtp_user": data.smtp_user,
        "smtp_password_encrypted": encrypt_value(data.smtp_password),
        "smtp_from": data.smtp_from,
    }

    for key, value in config_items.items():
        result = await db.execute(select(AppConfig).where(AppConfig.key == key))
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = value
        else:
            db.add(AppConfig(key=key, value=value))

    await db.commit()
    for key in ("smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from"):
        settings_cache.invalidate(key)
    return {"message": "SMTP configuration updated"}


@router.post("/test")
async def test_email(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Test SMTP connection."""
    db_config = await _get_smtp_config(db)
    success = await email_service.test_connection(db_config)
    if not success:
        raise HTTPException(status_code=502, detail="SMTP connection failed")
    return {"message": "SMTP connection successful"}


# --- Webhook ---


async def _get_webhook_secret(db: AsyncSession) -> str:
    """Get webhook secret from DB or env.

    F74: an unguarded decrypt here meant a rotated PAPYRUS_ENCRYPTION_KEY (or
    a restored foreign backup) turned every inbound /api/email/receive into
    a generic 500 with no hint the key was the cause. A decrypt failure is
    treated the same as "not configured" (empty string), matching
    get_setting's guarded pattern.
    """
    result = await db.execute(
        select(AppConfig).where(AppConfig.key == "email_webhook_secret")
    )
    row = result.scalar_one_or_none()
    if not row:
        return ""
    try:
        return decrypt_value(row.value)
    except InvalidToken:
        logger.warning(
            "Failed to decrypt email webhook secret -- encryption key may have changed"
        )
        return ""


def _check_rate_limit(key: str, max_requests: int = 10) -> bool:
    """Sliding-window rate limit. Returns True if the call for `key` is allowed.

    F57: `key` is the caller-supplied identity to rate-limit on -- the
    validated webhook token (see receive_email), not the client/proxy IP, so
    every sender no longer shares one bucket behind a proxy that doesn't
    forward the real client address. Also evicts any bucket that's seen no
    requests in over an hour, so a rotated/one-off token's bucket doesn't
    accumulate in `_webhook_requests` forever.
    """
    now = time.time()
    window = 60.0  # 1 minute

    for stale_key in [
        k for k, timestamps in _webhook_requests.items()
        if not timestamps or now - timestamps[-1] > _BUCKET_IDLE_SECONDS
    ]:
        del _webhook_requests[stale_key]

    _webhook_requests[key] = [
        t for t in _webhook_requests[key] if now - t < window
    ]

    if len(_webhook_requests[key]) >= max_requests:
        return False

    _webhook_requests[key].append(now)
    return True


@router.post("/receive", status_code=201)
async def receive_email(
    files: list[UploadFile] = File(...),
    token: str = Form(...),
    sender: str = Form(default=""),
    subject: str = Form(default="Email Attachment"),
    db: AsyncSession = Depends(get_db),
):
    """Receive forwarded email attachments and create print jobs.

    This endpoint is called by external services (Postfix, Zapier, n8n)
    to forward email attachments for printing. Authentication is via
    a shared secret token, not OIDC.
    """
    # F57: validate the token *before* touching the rate limiter -- an
    # attacker posting junk tokens must not be able to exhaust the
    # legitimate forwarder's quota (the old order recorded every hit,
    # authenticated or not, in a bucket keyed by the client/proxy IP that
    # every sender behind that proxy shares).
    webhook_secret = await _get_webhook_secret(db)
    if not webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook not configured")
    if not secrets.compare_digest(token, webhook_secret):
        raise HTTPException(status_code=403, detail="Invalid webhook token")

    from app.routers.settings import get_setting, safe_int_setting
    rate_limit = safe_int_setting(await get_setting(db, "email_webhook_rate_limit"), 10)
    if not _check_rate_limit(token, max_requests=rate_limit):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # F71: the subject is unbounded, arbitrary input, while PrintJob.title is
    # String(255) -- truncate up front rather than letting a long subject
    # blow up the DB write after every attachment is already on disk.
    title = subject.strip()[:255] if subject and subject.strip() else "Email Attachment"

    # F55/F25: each attachment is created via the same shared helper
    # `/upload` and the share-target route use, so it gets the same
    # streaming save + configured size cap (an oversize attachment 413s
    # instead of being buffered whole in memory with no limit), the same
    # job_created WS broadcast, and the same print.upload/print.held webhook
    # dispatch -- none of which this endpoint used to do at all.
    created_jobs = []
    for upload_file in files:
        if not upload_file.filename:
            continue

        mime_type = detect_mime_type(upload_file.filename)
        if not is_printable(mime_type):
            continue

        job, _pin = await _create_print_job_from_upload(
            db, None, upload_file,
            hold=True, auto_pin=False, source_type="email", title=title,
        )
        created_jobs.append({"id": job.id, "title": job.title})

    return {"jobs": created_jobs, "total": len(created_jobs)}


@router.get("/webhook-info")
async def get_webhook_info(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get webhook URL and configuration status (admin only)."""
    from app.routers.settings import get_setting
    webhook_secret = await get_setting(db, "email_webhook_secret")
    has_secret = bool(webhook_secret)
    from app.config import settings
    return {
        "webhook_url": f"{settings.base_url}/api/email/receive",
        "configured": has_secret,
    }


@router.post("/webhook-secret")
async def generate_webhook_secret(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new webhook secret (admin only). Returns plaintext once."""
    new_secret = secrets.token_urlsafe(32)

    result = await db.execute(
        select(AppConfig).where(AppConfig.key == "email_webhook_secret")
    )
    existing = result.scalar_one_or_none()
    encrypted = encrypt_value(new_secret)

    if existing:
        existing.value = encrypted
    else:
        db.add(AppConfig(key="email_webhook_secret", value=encrypted))

    await db.commit()
    settings_cache.invalidate("email_webhook_secret")

    from app.config import settings
    return {
        "secret": new_secret,
        "webhook_url": f"{settings.base_url}/api/email/receive",
    }
