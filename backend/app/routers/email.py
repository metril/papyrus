import os
import secrets
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_db
from app.models import AppConfig, PrintJob, User
from app.schemas import EmailConfig, EmailConfigStatus
from app.services.convert_service import is_printable
from app.services.crypto import decrypt_value, encrypt_value
from app.services.email_service import email_service
from app.services.file_service import detect_mime_type, get_upload_path, sanitize_filename

router = APIRouter()

# In-memory rate limiting for webhook
_webhook_requests: dict[str, list[float]] = defaultdict(list)


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
    """Get webhook secret from DB or env."""
    result = await db.execute(
        select(AppConfig).where(AppConfig.key == "email_webhook_secret")
    )
    row = result.scalar_one_or_none()
    if row:
        return decrypt_value(row.value)
    return ""


def _check_rate_limit(client_ip: str, max_requests: int = 10) -> bool:
    """Check if client IP is within rate limit. Returns True if allowed."""
    now = time.time()
    window = 60.0  # 1 minute

    # Clean old entries
    _webhook_requests[client_ip] = [
        t for t in _webhook_requests[client_ip] if now - t < window
    ]

    if len(_webhook_requests[client_ip]) >= max_requests:
        return False

    _webhook_requests[client_ip].append(now)
    return True


@router.post("/receive", status_code=201)
async def receive_email(
    request: Request,
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
    # Rate limit
    from app.routers.settings import get_setting, safe_int_setting
    rate_limit = safe_int_setting(await get_setting(db, "email_webhook_rate_limit"), 10)
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip, max_requests=rate_limit):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Validate webhook token
    webhook_secret = await _get_webhook_secret(db)
    if not webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook not configured")
    if not secrets.compare_digest(token, webhook_secret):
        raise HTTPException(status_code=403, detail="Invalid webhook token")

    # Ensure upload directory exists
    upload_dir = await get_setting(db, "upload_dir") or "/app/data/uploads"
    os.makedirs(upload_dir, exist_ok=True)

    created_jobs = []
    for upload_file in files:
        if not upload_file.filename:
            continue

        mime_type = detect_mime_type(upload_file.filename)
        if not is_printable(mime_type):
            continue

        # Save file
        filepath = get_upload_path(upload_file.filename, upload_dir=upload_dir)
        content = await upload_file.read()

        if not content:
            continue

        with open(filepath, "wb") as f:
            f.write(content)

        file_size = len(content)
        safe_filename = sanitize_filename(upload_file.filename)
        title = f"{subject} - {safe_filename}" if subject else safe_filename

        # Create held print job (no user_id since this is webhook-based)
        job = PrintJob(
            user_id=None,
            title=title,
            filename=safe_filename,
            filepath=filepath,
            file_size=file_size,
            mime_type=mime_type,
            status="held",
            source_type="email",
        )
        db.add(job)
        await db.flush()
        created_jobs.append({"id": job.id, "title": job.title})

    await db.commit()

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

    from app.config import settings
    return {
        "secret": new_secret,
        "webhook_url": f"{settings.base_url}/api/email/receive",
    }
