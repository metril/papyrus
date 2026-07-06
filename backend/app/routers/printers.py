import asyncio
import re
from urllib.parse import urlparse

import ifaddr
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_db
from app.models import Printer, User
from app.schemas import serialize_print_job
from app.services import cups_admin
from app.services.cups_service import CupsService
from app.services.discovery_service import discover_printers
from app.services.ipp_client import probe_ipp
from app.services.test_page_service import TestPageError, print_test_page

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


async def _cups_status(cups_name: str) -> dict:
    try:
        return await CupsService(printer_name=cups_name).get_printer_status()
    except Exception:
        return {"state": 5, "state_message": "Unavailable", "accepting_jobs": False}


async def _printer_response(p: Printer) -> dict:
    return {
        "id": p.id,
        "display_name": p.display_name,
        "cups_name": p.cups_name,
        "uri": p.uri,
        "description": p.description,
        "make_and_model": p.make_and_model,
        "location": p.location,
        "is_default": p.is_default,
        "is_network_queue": p.is_network_queue,
        "auto_release": p.auto_release,
        "created_at": p.created_at,
        "cups_status": await _cups_status(p.cups_name),
    }


async def _tcp_port_open(ip: str, port: int, timeout: float = 3.0) -> bool:
    """True if a TCP connection to ``ip:port`` succeeds within ``timeout``."""
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=timeout
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass  # connect already succeeded; a reset during close is still "reachable"
    return True


async def _check_reachable(ip: str) -> bool:
    """Try the CUPS/IPP port first, then plain HTTP."""
    for port in (631, 80):
        if await _tcp_port_open(ip, port):
            return True
    return False


async def _enrich_printer_info(printer: Printer, uri: str) -> bool:
    """Probe the host behind an ``ipp``/``ipps`` URI and populate
    ``printer.make_and_model``/``printer.location`` from the result.

    Never raises: a non-IPP URI, an unresolvable host, an unreachable
    device, or a failed probe all just leave the printer untouched and
    return ``False``. Shared by both the add-printer flow and the
    refresh-info endpoint so enrichment behaves identically in both places.
    """
    try:
        parsed = urlparse(uri)
        if parsed.scheme not in ("ipp", "ipps") or not parsed.hostname:
            return False
        result = await probe_ipp(parsed.hostname)
    except Exception:
        return False
    if result is None:
        return False
    # Only overwrite a field when the probe actually returned a value for it:
    # a printer that answers IPP but omits e.g. printer-location must not have
    # a previously stored value wiped out just because this probe didn't see it.
    make_and_model = result.get("make_and_model")
    if make_and_model is not None:
        printer.make_and_model = make_and_model
    location = result.get("location")
    if location is not None:
        printer.location = location
    return True


@router.get("")
async def list_printers(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[dict]:
    result = await db.execute(select(Printer).order_by(Printer.id))
    # Fetch per-printer CUPS status concurrently.
    return list(await asyncio.gather(*(_printer_response(p) for p in result.scalars())))


# Papyrus advertises itself over mDNS on the same host it browses on
# (``docker/avahi/airprint.service``: ``_ipp._tcp`` on port 6310, resource
# path ``printers/Papyrus``). Left unfiltered, the add-printer flow would
# list the server as an addable device; configuring it creates a CUPS queue
# that feeds jobs straight back into the papyrus hold queue -- an unbounded
# print loop once auto-release is on.
_SELF_ADVERTISEMENT_PORT = 6310
_SELF_ADVERTISEMENT_RESOURCE_MARKER = "printers/Papyrus"


def _local_ipv4_addresses() -> set[str] | None:
    """Every IPv4 address bound to a local network interface, or ``None`` if
    enumeration failed for any reason.

    Never raises: interface enumeration is best-effort and must not break
    discovery just because it isn't available in some environment.
    """
    try:
        addresses: set[str] = set()
        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                if ip.is_IPv4:
                    addresses.add(ip.ip)
        return addresses
    except Exception:
        return None


def _is_self_advertisement(device: dict) -> bool:
    """Fingerprint of Papyrus's own static mDNS advertisement, used only as a
    fallback when local-interface enumeration fails and IP-based filtering
    isn't possible."""
    uri = device.get("uri") or ""
    return (
        _SELF_ADVERTISEMENT_RESOURCE_MARKER in uri
        or device.get("port") == _SELF_ADVERTISEMENT_PORT
    )


def _filter_self_advertisement(devices: list[dict]) -> list[dict]:
    """Drop Papyrus's own mDNS advertisement from a discovery result."""
    local_ips = _local_ipv4_addresses()
    if local_ips is not None:
        return [d for d in devices if (d.get("ip") or "") not in local_ips]
    return [d for d in devices if not _is_self_advertisement(d)]


@router.get("/discover")
async def discover_network_printers(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict:
    """Browse the LAN via mDNS, drop Papyrus's own advertisement, and flag
    devices already configured."""
    devices = await discover_printers()
    devices = _filter_self_advertisement(devices)
    result = await db.execute(select(Printer))
    configured_uris = [p.uri for p in result.scalars() if p.uri]

    # Exact-host matching, not substring: "10.0.0.1" must not match a printer
    # configured at "10.0.0.11". Malformed stored URIs are skipped, not fatal.
    configured_hosts = set()
    for configured_uri in configured_uris:
        try:
            host = urlparse(configured_uri).hostname
        except ValueError:
            continue
        if host:
            configured_hosts.add(host)

    for device in devices:
        ip = device.get("ip") or ""
        uri = device.get("uri") or ""
        device["already_configured"] = (ip != "" and ip in configured_hosts) or (
            uri != "" and uri in configured_uris
        )

    return {"printers": devices}


@router.get("/probe")
async def probe_printer_ip(
    ip: str,
    _user: User = Depends(require_admin),
) -> dict:
    """Probe a printer at the given IP address for reachability and IPP details."""
    fallback_uri = f"ipp://{ip}/ipp"
    empty_fields = {
        "make_model": None,
        "location": None,
        "state": None,
        "suggested_display_name": None,
    }

    if not await _check_reachable(ip):
        return {"reachable": False, "uri": fallback_uri, **empty_fields}

    enrich = await probe_ipp(ip)
    if enrich is None:
        return {"reachable": True, "uri": fallback_uri, **empty_fields}

    make_model = enrich.get("make_and_model")
    return {
        "reachable": True,
        "uri": f"ipp://{ip}:631{enrich['resource']}",
        "make_model": make_model,
        "location": enrich.get("location"),
        "state": enrich.get("state"),
        "suggested_display_name": make_model,
    }


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
        raise HTTPException(
            status_code=409, detail=f"A printer with cups_name '{cups_name}' already exists"
        )

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
        if await _enrich_printer_info(printer, body.uri):
            await db.commit()

    return await _printer_response(printer)


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

    return await _printer_response(printer)


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
        update(Printer).where(Printer.is_default.is_(True)).values(is_default=False)
    )
    printer.is_default = True
    await db.commit()
    await db.refresh(printer)
    return await _printer_response(printer)


@router.post("/{printer_id}/resume", status_code=200)
async def resume_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict:
    """Re-enable a stopped CUPS printer queue via pycups."""
    printer = await db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")
    try:
        await cups_admin.enable_queue(printer.cups_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return await _printer_response(printer)


@router.post("/{printer_id}/test-page")
async def send_test_page(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> dict:
    """Print an identify sheet so an admin can physically confirm which
    device this printer maps to."""
    printer = await db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")
    if printer.is_network_queue:
        raise HTTPException(
            status_code=400,
            detail="A network hold queue has no physical device to print a test page to",
        )

    try:
        job = await print_test_page(db, printer, user)
    except TestPageError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return serialize_print_job(job)


@router.post("/{printer_id}/refresh-info")
async def refresh_printer_info(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict:
    """Re-probe a configured printer's IPP endpoint and refresh its device info."""
    printer = await db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")
    if await _enrich_printer_info(printer, printer.uri):
        await db.commit()
    return await _printer_response(printer)
