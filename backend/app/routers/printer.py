from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import User
from app.schemas import PrinterStatus
from app.services.cups_service import CupsService, get_default_release_queue_name

router = APIRouter()


@router.get("/status", response_model=PrinterStatus)
async def get_printer_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current printer status from CUPS.

    F34: read from the default printer's `_release` queue, not its
    `cups_name` hold queue -- the hold queue is a fake `papyrus:/` device
    with no PPD-backed markers/state, so status/toner reported here must
    match what `/api/printers` and the alert service already report from the
    real device. The hold queue is used only for enable/resume.
    """
    name = await get_default_release_queue_name(db)
    status = await CupsService(printer_name=name).get_printer_status()
    return PrinterStatus(**status)


@router.get("/settings")
async def get_printer_settings(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get available printer options.

    F34: see get_printer_status -- options/capabilities must come from the
    real device (`_release` queue), not the fake hold queue.
    """
    name = await get_default_release_queue_name(db)
    return await CupsService(printer_name=name).get_printer_options()
