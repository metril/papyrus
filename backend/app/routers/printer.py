from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import User
from app.schemas import PrinterStatus
from app.services.cups_service import CupsService, get_default_printer_name

router = APIRouter()


@router.get("/status", response_model=PrinterStatus)
async def get_printer_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current printer status from CUPS."""
    name = await get_default_printer_name(db)
    status = CupsService(printer_name=name).get_printer_status()
    return PrinterStatus(**status)


@router.get("/settings")
async def get_printer_settings(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get available printer options."""
    name = await get_default_printer_name(db)
    return CupsService(printer_name=name).get_printer_options()
