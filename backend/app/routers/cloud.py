import os
import secrets
import tempfile
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.config import settings
from app.database import get_db
from app.models import CloudProvider, User
from app.schemas import CloudFileEntry, CloudProviderResponse
from app.services.cloud_service import CloudError, cloud_service
from app.services.crypto import decrypt_value, encrypt_value

router = APIRouter()

GDRIVE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GDRIVE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GDRIVE_SCOPES = "https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/drive.file"

DROPBOX_AUTH_URL = "https://www.dropbox.com/oauth2/authorize"
DROPBOX_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"


@router.get("/providers")
async def list_providers(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List connected cloud storage providers for the current user."""
    result = await db.execute(
        select(CloudProvider).where(CloudProvider.user_id == user.id)
    )
    providers = result.scalars().all()
    return {
        "providers": [
            {
                "id": p.id,
                "provider": p.provider,
                "connected_at": p.connected_at.isoformat(),
            }
            for p in providers
        ]
    }


@router.delete("/disconnect/{provider_id}", status_code=204)
async def disconnect_provider(
    provider_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect a cloud storage provider."""
    result = await db.execute(
        select(CloudProvider).where(
            CloudProvider.id == provider_id,
            CloudProvider.user_id == user.id,
        )
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    await db.delete(provider)
    await db.commit()


# --- OAuth ---


@router.get("/authorize/{provider}")
async def authorize_provider(
    provider: str,
    request: Request,
    user: User = Depends(get_current_user),
):
    """Redirect to cloud provider OAuth consent screen."""
    state = secrets.token_urlsafe(32)
    request.session["cloud_oauth_state"] = state
    request.session["cloud_oauth_provider"] = provider

    redirect_uri = f"{settings.base_url}/api/cloud/callback/{provider}"

    if provider == "gdrive":
        if not settings.gdrive_client_id:
            raise HTTPException(status_code=400, detail="Google Drive not configured")
        params = {
            "client_id": settings.gdrive_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GDRIVE_SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        url = GDRIVE_AUTH_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return RedirectResponse(url=url)

    elif provider == "dropbox":
        if not settings.dropbox_app_key:
            raise HTTPException(status_code=400, detail="Dropbox not configured")
        params = {
            "client_id": settings.dropbox_app_key,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "token_access_type": "offline",
            "state": state,
        }
        url = DROPBOX_AUTH_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return RedirectResponse(url=url)

    raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")


@router.get("/callback/{provider}")
async def oauth_callback(
    provider: str,
    code: str,
    state: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Handle OAuth callback from cloud provider."""
    expected_state = request.session.pop("cloud_oauth_state", None)
    if state != expected_state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    redirect_uri = f"{settings.base_url}/api/cloud/callback/{provider}"

    if provider == "gdrive":
        async with httpx.AsyncClient() as client:
            resp = await client.post(GDRIVE_TOKEN_URL, data={
                "code": code,
                "client_id": settings.gdrive_client_id,
                "client_secret": settings.gdrive_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            })
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to exchange token with Google")
        data = resp.json()

    elif provider == "dropbox":
        async with httpx.AsyncClient() as client:
            resp = await client.post(DROPBOX_TOKEN_URL, data={
                "code": code,
                "client_id": settings.dropbox_app_key,
                "client_secret": settings.dropbox_app_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            })
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to exchange token with Dropbox")
        data = resp.json()

    else:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    access_token = data["access_token"]
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in")

    token_expiry = None
    if expires_in:
        token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # Upsert: update existing or create new
    result = await db.execute(
        select(CloudProvider).where(
            CloudProvider.user_id == user.id,
            CloudProvider.provider == provider,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.access_token_encrypted = encrypt_value(access_token)
        if refresh_token:
            existing.refresh_token_encrypted = encrypt_value(refresh_token)
        existing.token_expiry = token_expiry
        existing.connected_at = datetime.now(timezone.utc)
    else:
        cp = CloudProvider(
            user_id=user.id,
            provider=provider,
            access_token_encrypted=encrypt_value(access_token),
            refresh_token_encrypted=encrypt_value(refresh_token) if refresh_token else None,
            token_expiry=token_expiry,
        )
        db.add(cp)

    await db.commit()
    return RedirectResponse(url="/settings?cloud=connected", status_code=303)


# --- Browse & Download ---


async def _get_access_token(provider: CloudProvider, db: AsyncSession) -> str:
    """Get a valid access token, refreshing if expired."""
    now = datetime.now(timezone.utc)

    if provider.token_expiry and provider.token_expiry.replace(tzinfo=timezone.utc) < now:
        if not provider.refresh_token_encrypted:
            raise HTTPException(
                status_code=401,
                detail="Token expired and no refresh token available. Please reconnect.",
            )

        if provider.provider == "gdrive":
            new_token, expiry = await cloud_service.refresh_gdrive_token(
                provider.refresh_token_encrypted
            )
        elif provider.provider == "dropbox":
            new_token, expiry = await cloud_service.refresh_dropbox_token(
                provider.refresh_token_encrypted
            )
        else:
            raise HTTPException(status_code=400, detail="Unknown provider")

        provider.access_token_encrypted = encrypt_value(new_token)
        provider.token_expiry = expiry
        await db.commit()
        return new_token

    return decrypt_value(provider.access_token_encrypted)


@router.get("/files/{provider_id}", response_model=list[CloudFileEntry])
async def list_files(
    provider_id: int,
    folder_id: str | None = None,
    path: str = "",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Browse files in a cloud storage provider."""
    result = await db.execute(
        select(CloudProvider).where(
            CloudProvider.id == provider_id,
            CloudProvider.user_id == user.id,
        )
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    access_token = await _get_access_token(provider, db)

    try:
        if provider.provider == "gdrive":
            files = await cloud_service.list_gdrive_files(access_token, folder_id)
        elif provider.provider == "dropbox":
            files = await cloud_service.list_dropbox_files(access_token, path)
        else:
            raise HTTPException(status_code=400, detail="Unknown provider")
    except CloudError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return files


def _cleanup_temp_file(path: str):
    """Background task to remove a temp file after download."""
    try:
        os.unlink(path)
    except OSError:
        pass


@router.get("/download/{provider_id}")
async def download_file(
    provider_id: int,
    background_tasks: BackgroundTasks,
    file_id: str | None = None,
    path: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Download a file from cloud storage."""
    result = await db.execute(
        select(CloudProvider).where(
            CloudProvider.id == provider_id,
            CloudProvider.user_id == user.id,
        )
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    access_token = await _get_access_token(provider, db)

    tmp_dir = tempfile.gettempdir()
    tmp_name = f"papyrus_cloud_{secrets.token_hex(8)}"
    local_path = os.path.join(tmp_dir, tmp_name)

    try:
        if provider.provider == "gdrive":
            if not file_id:
                raise HTTPException(status_code=400, detail="file_id is required for Google Drive")
            await cloud_service.download_gdrive_file(access_token, file_id, local_path)
        elif provider.provider == "dropbox":
            if not path:
                raise HTTPException(status_code=400, detail="path is required for Dropbox")
            await cloud_service.download_dropbox_file(access_token, path, local_path)
        else:
            raise HTTPException(status_code=400, detail="Unknown provider")
    except CloudError as e:
        raise HTTPException(status_code=502, detail=str(e))

    background_tasks.add_task(_cleanup_temp_file, local_path)

    filename = os.path.basename(path) if path else f"cloud_file_{file_id}"
    return FileResponse(local_path, filename=filename)
