from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_user, require_admin
from app.models import User
from app.schemas import PrinterSettings, PrinterStatus
from app.services.cups_service import cups_service

router = APIRouter()


@router.get("/status", response_model=PrinterStatus)
async def get_printer_status(user: User = Depends(get_current_user)):
    """Get current printer status from CUPS."""
    status = cups_service.get_printer_status()
    return PrinterStatus(**status)


@router.get("/settings")
async def get_printer_settings(user: User = Depends(get_current_user)):
    """Get available printer options."""
    return cups_service.get_printer_options()
