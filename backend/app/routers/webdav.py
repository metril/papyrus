"""WebDAV/Nextcloud API endpoints."""

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.database import get_db
from app.models import CloudProvider, User
from app.services.crypto import encrypt_value
from app.services.net_guard import UnsafeHostError, assert_safe_host
from app.services.webdav_service import WebDAVConnectError, webdav_service

router = APIRouter()

_CONNECT_ERROR_DETAIL = {
    WebDAVConnectError.AUTH: "Authentication failed — check the username and password",
    WebDAVConnectError.TRANSPORT: "Could not reach the WebDAV server — check the URL",
    WebDAVConnectError.NOT_WEBDAV: "The server did not respond like a WebDAV server",
}


@router.post("/connect", status_code=201)
async def connect_webdav(
    body: dict,
    user: User = Depends(require_permission("files")),
    db: AsyncSession = Depends(get_db),
):
    """Connect a WebDAV/Nextcloud server.

    Body: {url, username, password}
    """
    url = body.get("url", "").strip()
    username = body.get("username", "").strip()
    password = body.get("password", "")

    if not url or not username or not password:
        raise HTTPException(status_code=400, detail="url, username, and password are required")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="URL must use http:// or https://")
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="Invalid URL")

    # F13: reject loopback/link-local/multicast/unspecified targets before
    # ever making a request — private/LAN addresses are allowed on purpose,
    # a self-hosted Nextcloud instance lives there.
    try:
        await assert_safe_host(parsed.hostname)
    except UnsafeHostError:
        raise HTTPException(status_code=400, detail="Invalid URL")

    password_enc = encrypt_value(password)

    # Test connection first
    reason = await webdav_service.test_connection(url, username, password_enc)
    if reason is not None:
        raise HTTPException(status_code=400, detail=_CONNECT_ERROR_DETAIL[reason])

    # Store as a CloudProvider with provider="webdav"
    provider = CloudProvider(
        user_id=user.id,
        provider="webdav",
        access_token_encrypted=encrypt_value(f"{url}||{username}"),
        refresh_token_encrypted=password_enc,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)

    return {
        "id": provider.id,
        "provider": "webdav",
        "url": url,
        "connected_at": provider.connected_at.isoformat(),
    }


def _parse_webdav_creds(provider: CloudProvider) -> tuple[str, str, str]:
    """Extract url, username, password_encrypted from a webdav CloudProvider."""
    from app.services.crypto import decrypt_value
    combined = decrypt_value(provider.access_token_encrypted)
    parts = combined.split("||", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=500, detail="Corrupt WebDAV credentials")
    url, username = parts
    password_encrypted = provider.refresh_token_encrypted
    if not password_encrypted:
        raise HTTPException(status_code=500, detail="Missing WebDAV password")
    return url, username, password_encrypted


@router.get("/{provider_id}/files")
async def list_webdav_files(
    provider_id: int,
    path: str = Query(default="/"),
    user: User = Depends(require_permission("files")),
    db: AsyncSession = Depends(get_db),
):
    """List files at a WebDAV path."""
    result = await db.execute(
        select(CloudProvider).where(
            CloudProvider.id == provider_id,
            CloudProvider.user_id == user.id,
            CloudProvider.provider == "webdav",
        )
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="WebDAV connection not found")

    url, username, password_enc = _parse_webdav_creds(provider)
    return await webdav_service.list_files(url, username, password_enc, path)


@router.post("/{provider_id}/upload")
async def upload_to_webdav(
    provider_id: int,
    body: dict,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a scan to WebDAV.

    Body: {scan_id, destination_folder}
    """
    from app.models import ScanJob

    scan_id = body.get("scan_id")
    destination = body.get("destination_folder", "/")
    if not scan_id:
        raise HTTPException(status_code=400, detail="scan_id is required")

    result = await db.execute(
        select(CloudProvider).where(
            CloudProvider.id == provider_id,
            CloudProvider.user_id == user.id,
            CloudProvider.provider == "webdav",
        )
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="WebDAV connection not found")

    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == scan_id))
    scan = result.scalar_one_or_none()
    if not scan or not scan.filepath:
        raise HTTPException(status_code=404, detail="Scan not found")

    url, username, password_enc = _parse_webdav_creds(provider)
    filename = f"scan_{scan.scan_id}.{scan.format}"

    await webdav_service.upload_file(
        url, username, password_enc, scan.filepath, filename, destination
    )

    return {"message": f"Uploaded {filename} to WebDAV"}
