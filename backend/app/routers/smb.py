import os
import tempfile

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin, require_permission
from app.database import get_db
from app.models import SMBShare, User
from app.schemas import SMBFileEntry, SMBShareCreate, SMBShareResponse
from app.services.crypto import encrypt_value
from app.services.smb_service import SMBError, smb_service

router = APIRouter()


@router.get("/shares", response_model=list[SMBShareResponse])
async def list_shares(
    user: User = Depends(require_permission("files")),
    db: AsyncSession = Depends(get_db),
):
    """List configured SMB shares."""
    result = await db.execute(select(SMBShare).order_by(SMBShare.name))
    return result.scalars().all()


@router.post("/shares", response_model=SMBShareResponse, status_code=201)
async def add_share(
    data: SMBShareCreate,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Add a new SMB share configuration."""
    password_encrypted = None
    if data.password:
        password_encrypted = encrypt_value(data.password)

    share = SMBShare(
        name=data.name,
        server=data.server,
        share_name=data.share_name,
        username=data.username,
        password_encrypted=password_encrypted,
        domain=data.domain,
        created_by=user.id,
    )
    db.add(share)
    await db.commit()
    await db.refresh(share)
    return share


@router.delete("/shares/{share_id}", status_code=204)
async def remove_share(
    share_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Remove an SMB share configuration."""
    result = await db.execute(select(SMBShare).where(SMBShare.id == share_id))
    share = result.scalar_one_or_none()
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found")
    await db.delete(share)
    await db.commit()


@router.get("/browse/{share_id}", response_model=list[SMBFileEntry])
async def browse_share(
    share_id: int,
    path: str = Query(default="/"),
    user: User = Depends(require_permission("files")),
    db: AsyncSession = Depends(get_db),
):
    """Browse files on an SMB share."""
    result = await db.execute(select(SMBShare).where(SMBShare.id == share_id))
    share = result.scalar_one_or_none()
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found")

    try:
        entries = smb_service.browse(
            server=share.server,
            share_name=share.share_name,
            path=path,
            username=share.username,
            password_encrypted=share.password_encrypted,
            domain=share.domain,
        )
        return entries
    except SMBError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/download/{share_id}")
async def download_from_share(
    share_id: int,
    path: str = Query(...),
    user: User = Depends(require_permission("files")),
    db: AsyncSession = Depends(get_db),
):
    """Download a file from an SMB share (for printing or local use)."""
    result = await db.execute(select(SMBShare).where(SMBShare.id == share_id))
    share = result.scalar_one_or_none()
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found")

    # Download to temp file
    filename = os.path.basename(path)
    temp_path = os.path.join(tempfile.gettempdir(), f"papyrus_smb_{filename}")

    try:
        smb_service.download(
            server=share.server,
            share_name=share.share_name,
            remote_path=path,
            local_path=temp_path,
            username=share.username,
            password_encrypted=share.password_encrypted,
            domain=share.domain,
        )
    except SMBError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return FileResponse(temp_path, filename=filename)
