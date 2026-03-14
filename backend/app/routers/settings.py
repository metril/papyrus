from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.config import settings
from app.database import get_db
from app.models import AppConfig, User
from app.services.audit_service import log_event
from app.services.crypto import decrypt_value, encrypt_value

router = APIRouter()

# Maps setting key → (python_type, is_encrypted)
# Encrypted values are stored in AppConfig as "{key}_encrypted"
# Note: printer/scanner hardware is managed via /api/printers and /api/scanners
CONFIGURABLE: dict[str, tuple[type, bool]] = {
    "scan_dir": (str, False),
    "upload_dir": (str, False),
    "max_upload_size_mb": (int, False),
    "scan_retention_days": (int, False),
    "escl_enabled": (bool, False),
    "base_url": (str, False),
    "dev_mode": (bool, False),
    "smtp_host": (str, False),
    "smtp_port": (int, False),
    "smtp_user": (str, False),
    "smtp_password": (str, True),
    "smtp_from": (str, False),
    "gdrive_client_id": (str, False),
    "gdrive_client_secret": (str, True),
    "dropbox_app_key": (str, False),
    "dropbox_app_secret": (str, True),
    "email_webhook_rate_limit": (int, False),
    "onedrive_client_id": (str, False),
    "onedrive_client_secret": (str, True),
    "paperless_url": (str, False),
    "paperless_api_token": (str, True),
    "ocr_enabled": (bool, False),
    "ocr_language": (str, False),
    "scan_filename_template": (str, False),
    "ftp_host": (str, False),
    "ftp_port": (int, False),
    "ftp_username": (str, False),
    "ftp_password": (str, True),
    "ftp_remote_dir": (str, False),
    "ftp_protocol": (str, False),  # ftp, ftps, sftp
    "require_release_pin": (bool, False),
    "print_retention_days": (int, False),
}

_PLACEHOLDER = "*set*"


def _db_key(key: str, encrypted: bool) -> str:
    return f"{key}_encrypted" if encrypted else key


def _coerce(value: str, type_: type) -> Any:
    if type_ == bool:
        return value.lower() in ("true", "1", "yes")
    return type_(value)


async def _load_db_values(db: AsyncSession) -> dict[str, str]:
    result = await db.execute(select(AppConfig))
    return {row.key: row.value for row in result.scalars()}


async def get_setting(db: AsyncSession, key: str) -> str | None:
    """Read a setting from DB (AppConfig), falling back to env-var config."""
    _type, encrypted = CONFIGURABLE.get(key, (str, False))
    db_key = _db_key(key, encrypted)
    row = await db.get(AppConfig, db_key)
    if row:
        if encrypted:
            return decrypt_value(row.value)
        return row.value
    val = getattr(settings, key, None)
    return val if val else None


@router.get("")
async def get_settings(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict[str, Any]:
    db_values = await _load_db_values(db)
    out: dict[str, Any] = {}
    for key, (type_, encrypted) in CONFIGURABLE.items():
        db_row_key = _db_key(key, encrypted)
        if db_row_key in db_values:
            if encrypted:
                out[key] = _PLACEHOLDER
            else:
                out[key] = _coerce(db_values[db_row_key], type_)
        else:
            env_val = getattr(settings, key, None)
            if encrypted:
                out[key] = _PLACEHOLDER if env_val else ""
            else:
                out[key] = env_val
    return out


@router.put("")
async def update_settings(
    updates: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict[str, bool]:
    for key, value in updates.items():
        if key not in CONFIGURABLE:
            raise HTTPException(status_code=400, detail=f"Unknown setting: {key}")
        _type, encrypted = CONFIGURABLE[key]

        if encrypted:
            if value == _PLACEHOLDER:
                continue  # unchanged — skip
            db_row_key = f"{key}_encrypted"
            if value == "" or value is None:
                # Clear the secret
                await db.execute(delete(AppConfig).where(AppConfig.key == db_row_key))
            else:
                stored = encrypt_value(str(value))
                existing = await db.get(AppConfig, db_row_key)
                if existing:
                    existing.value = stored
                else:
                    db.add(AppConfig(key=db_row_key, value=stored))
        else:
            str_value = str(value).lower() if _type == bool else str(value)
            existing = await db.get(AppConfig, key)
            if existing:
                existing.value = str_value
            else:
                db.add(AppConfig(key=key, value=str_value))

    await db.commit()

    # Audit log — record which keys were changed (redact encrypted values)
    changed_keys = [k for k in updates if not (CONFIGURABLE[k][1] and updates[k] == _PLACEHOLDER)]
    if changed_keys:
        await log_event(db, "settings.update", "settings", detail={
            "keys": changed_keys,
        }, user_id=_user.id)

    return {"ok": True}
