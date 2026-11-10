from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_docs import AUTH_ERRORS, std_responses
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import User
from app.schemas import LoadedPaper, PrinterOptions, PrinterStatus
from app.services.cups_service import (
    CupsService,
    get_default_printer,
    get_default_release_queue_name,
)
from app.services.paper_service import get_loaded_paper

router = APIRouter()


@router.get(
    "/status",
    response_model=PrinterStatus,
    summary="Get default printer status",
    responses=std_responses(AUTH_ERRORS),
)
async def get_printer_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Current status and consumable levels of the default printer, read from CUPS.

    Any authenticated user. Read-only, no side effects. Returns a stopped/unreachable
    status object rather than an error when CUPS cannot be queried.

    F34: read from the default printer's `_release` queue, not its
    `cups_name` hold queue -- the hold queue is a fake `papyrus:/` device
    with no PPD-backed markers/state, so status/toner reported here must
    match what `/api/printers` and the alert service already report from the
    real device. The hold queue is used only for enable/resume.
    """
    name = await get_default_release_queue_name(db)
    status = await CupsService(printer_name=name).get_printer_status()
    return PrinterStatus(**status)


@router.get(
    "/settings",
    response_model=PrinterOptions,
    response_model_exclude_unset=True,
    summary="Get printer capabilities",
    responses=std_responses(AUTH_ERRORS),
)
async def get_printer_settings(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Paper sizes, duplex modes and colour support of the default printer.

    Any authenticated user. Read-only. Returns an empty object if CUPS cannot be queried.

    F34: see get_printer_status -- options/capabilities must come from the
    real device (`_release` queue), not the fake hold queue.
    """
    name = await get_default_release_queue_name(db)
    return await CupsService(printer_name=name).get_printer_options()


@router.get(
    "/paper",
    response_model=LoadedPaper,
    summary="Get loaded paper size",
    responses=std_responses(AUTH_ERRORS),
)
async def get_loaded_paper_size(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Paper size loaded in the default printer (``"A4"``/``"Letter"``/``"Legal"``),
    or null when unknown, read over IPP and cached for 60s.

    Any authenticated user. Never errors: forms just fall back to A4."""
    printer = await get_default_printer(db)
    media = await get_loaded_paper(printer.uri) if printer else None
    return {"media": media}
