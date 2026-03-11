import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_db
from app.models import Printer, User
from app.services import cups_admin
from app.services.cups_service import CupsService

router = APIRouter()


def _sanitize(display_name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", display_name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "printer"


class PrinterCreate(BaseModel):
    display_name: str
    uri: str = ""
    description: str | None = None
    is_network_queue: bool = False
    auto_release: bool = False


class PrinterUpdate(BaseModel):
    display_name: str | None = None
    uri: str | None = None
    description: str | None = None
    auto_release: bool | None = None


def _cups_status(cups_name: str) -> dict:
    try:
        return CupsService(printer_name=cups_name).get_printer_status()
    except Exception:
        return {"state": 5, "state_message": "Unavailable", "accepting_jobs": False}


def _printer_response(p: Printer) -> dict:
    return {
        "id": p.id,
        "display_name": p.display_name,
        "cups_name": p.cups_name,
        "uri": p.uri,
        "description": p.description,
        "is_default": p.is_default,
        "is_network_queue": p.is_network_queue,
        "auto_release": p.auto_release,
        "created_at": p.created_at,
        "cups_status": _cups_status(p.cups_name),
    }


@router.get("")
async def list_printers(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[dict]:
    result = await db.execute(select(Printer).order_by(Printer.id))
    return [_printer_response(p) for p in result.scalars()]


@router.get("/probe")
async def probe_printer_ip(
    ip: str,
    _user: User = Depends(require_admin),
) -> dict:
    """Probe a printer at the given IP address and return connection info."""
    from urllib.request import urlopen
    from urllib.error import URLError

    uri = f"ipp://{ip}/ipp"

    async def _try(url: str) -> bool:
        loop = asyncio.get_event_loop()
        def _fetch():
            try:
                with urlopen(url, timeout=3):
                    pass
                return True
            except Exception:
                return True  # Any response (even error) means host is reachable
        try:
            await loop.run_in_executor(None, _fetch)
            return True
        except Exception:
            return False

    # Try CUPS/IPP port 631 first, then port 80
    reachable = False
    for port in (631, 80):
        loop = asyncio.get_event_loop()
        def _check(p=port):
            import socket
            try:
                s = socket.create_connection((ip, p), timeout=3)
                s.close()
                return True
            except OSError:
                return False
        if await loop.run_in_executor(None, _check):
            reachable = True
            break

    return {"reachable": reachable, "uri": uri}


@router.post("", status_code=201)
async def add_printer(
    body: PrinterCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict:
    cups_name = _sanitize(body.display_name)

    # Ensure cups_name is unique
    existing = await db.execute(select(Printer).where(Printer.cups_name == cups_name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"A printer with cups_name '{cups_name}' already exists")

    printer = Printer(
        display_name=body.display_name,
        cups_name=cups_name,
        uri=body.uri,
        description=body.description,
        is_network_queue=body.is_network_queue,
        auto_release=body.auto_release,
    )
    db.add(printer)
    await db.commit()
    await db.refresh(printer)

    # Configure CUPS + Avahi
    if body.is_network_queue:
        await cups_admin.add_network_queue(cups_name, body.display_name)
    else:
        await cups_admin.add_physical_printer(cups_name, body.display_name, body.uri)

    return _printer_response(printer)


@router.patch("/{printer_id}")
async def update_printer(
    printer_id: int,
    body: PrinterUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict:
    printer = await db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")

    old_cups_name = printer.cups_name
    old_display_name = printer.display_name

    if body.display_name is not None:
        printer.display_name = body.display_name
    if body.uri is not None:
        printer.uri = body.uri
    if body.description is not None:
        printer.description = body.description
    if body.auto_release is not None:
        printer.auto_release = body.auto_release

    await db.commit()
    await db.refresh(printer)

    display_changed = printer.display_name != old_display_name
    uri_changed = body.uri is not None

    if not printer.is_network_queue and (uri_changed or display_changed):
        await cups_admin.update_physical_printer(old_cups_name, printer.display_name, printer.uri)
    elif printer.is_network_queue and display_changed:
        # Just update Avahi service name
        await cups_admin.update_physical_printer(old_cups_name, printer.display_name, "")

    return _printer_response(printer)


@router.delete("/{printer_id}", status_code=204)
async def delete_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> None:
    printer = await db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")

    cups_name = printer.cups_name
    await db.delete(printer)
    await db.commit()
    await cups_admin.remove_printer(cups_name)


@router.post("/{printer_id}/default", status_code=200)
async def set_default_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict:
    printer = await db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")
    if printer.is_network_queue:
        raise HTTPException(status_code=400, detail="Network queue cannot be set as default")

    # Clear existing default
    await db.execute(
        update(Printer).where(Printer.is_default == True).values(is_default=False)
    )
    printer.is_default = True
    await db.commit()
    await db.refresh(printer)
    return _printer_response(printer)


@router.post("/{printer_id}/resume", status_code=200)
async def resume_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict:
    """Re-enable a stopped CUPS printer queue (cupsenable + cupsaccept)."""
    printer = await db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")
    await cups_admin._enable_queue(printer.cups_name)
    return _printer_response(printer)
