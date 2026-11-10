import os
import tempfile

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.api_docs import AUTH_ERRORS, NOT_FOUND, SERVER, file_response, std_responses
from app.auth.dependencies import require_admin, require_permission
from app.database import get_db
from app.models import SMBShare, User
from app.schemas import SMBFileEntry, SMBShareCreate, SMBShareResponse
from app.services.crypto import encrypt_value
from app.services.smb_service import smb_service

router = APIRouter()


@router.get(
    "/shares",
    summary="List SMB shares",
    response_model=list[SMBShareResponse],
    responses=std_responses(AUTH_ERRORS),
)
async def list_shares(
    user: User = Depends(require_permission("files")),
    db: AsyncSession = Depends(get_db),
):
    """List configured SMB shares, by name (permission `files`). Passwords are never returned."""
    result = await db.execute(select(SMBShare).order_by(SMBShare.name))
    return result.scalars().all()


@router.post(
    "/shares",
    summary="Add an SMB share",
    response_model=SMBShareResponse,
    status_code=201,
    responses=std_responses(AUTH_ERRORS),
)
async def add_share(
    data: SMBShareCreate,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Add an SMB share (admin only). The password is encrypted at rest."""
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


@router.delete(
    "/shares/{share_id}",
    summary="Remove an SMB share",
    status_code=204,
    responses=std_responses(AUTH_ERRORS, NOT_FOUND),
)
async def remove_share(
    share_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Remove an SMB share configuration (admin only)."""
    result = await db.execute(select(SMBShare).where(SMBShare.id == share_id))
    share = result.scalar_one_or_none()
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found")
    await db.delete(share)
    await db.commit()


@router.get(
    "/browse/{share_id}",
    summary="Browse an SMB share",
    response_model=list[SMBFileEntry],
    responses=std_responses(AUTH_ERRORS, NOT_FOUND, SERVER),
)
async def browse_share(
    share_id: int,
    path: str = Query(default="/"),
    user: User = Depends(require_permission("files")),
    db: AsyncSession = Depends(get_db),
):
    """List files and folders at `path` on an SMB share (permission `files`)."""
    result = await db.execute(select(SMBShare).where(SMBShare.id == share_id))
    share = result.scalar_one_or_none()
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found")

    return await smb_service.browse(
        server=share.server,
        share_name=share.share_name,
        path=path,
        username=share.username,
        password_encrypted=share.password_encrypted,
        domain=share.domain,
    )


@router.get(
    "/download/{share_id}",
    summary="Download a file from an SMB share",
    response_class=Response,
    responses=file_response(["application/octet-stream"], "The file contents as an attachment.")
    | std_responses(AUTH_ERRORS, NOT_FOUND, SERVER),
)
async def download_from_share(
    share_id: int,
    path: str = Query(...),
    user: User = Depends(require_permission("files")),
    db: AsyncSession = Depends(get_db),
):
    """Download the file at `path` from an SMB share (permission `files`). Staged in a temp file
    that is removed after the response is sent."""
    result = await db.execute(select(SMBShare).where(SMBShare.id == share_id))
    share = result.scalar_one_or_none()
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found")

    # Download to a temp file. F59: a fixed `papyrus_smb_<basename>` path
    # collided across concurrent downloads of the same filename (from
    # different shares, or the same request twice) -- the second write
    # truncated the file the first response was still streaming -- and was
    # never cleaned up. mkstemp() gives every download a unique path, and
    # the BackgroundTask below removes it once the response has been sent.
    filename = os.path.basename(path)
    fd, temp_path = tempfile.mkstemp(prefix="papyrus_smb_", suffix=f"_{filename}")
    os.close(fd)

    try:
        await smb_service.download(
            server=share.server,
            share_name=share.share_name,
            remote_path=path,
            local_path=temp_path,
            username=share.username,
            password_encrypted=share.password_encrypted,
            domain=share.domain,
        )
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise

    return FileResponse(
        temp_path, filename=filename, background=BackgroundTask(os.unlink, temp_path)
    )
