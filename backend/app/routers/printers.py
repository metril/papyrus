import asyncio
import ipaddress
import logging
import re
from urllib.parse import urlparse

import ifaddr
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_db
from app.exceptions import ExternalServiceError, PapyrusError
from app.models import Printer, User
from app.schemas import serialize_print_job
from app.services import cups_admin
from app.services.audit_service import log_event
from app.services.cups_service import CupsService
from app.services.discovery_service import discover_printers
from app.services.ipp_client import probe_ipp
from app.services.test_page_service import print_test_page
from app.services.webhook_service import dispatch_webhook

logger = logging.getLogger(__name__)

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


def _status_queue_name(p: Printer) -> str:
    """The CUPS queue that reports this printer's real status/toner (F34).

    A physical printer's ``cups_name`` queue is the fake ``papyrus:/`` hold
    queue with the generic PPD -- it has no device behind it, so it always
    reports idle/no-markers. The ``_release`` queue is the only one bound to
    the actual device URI. Network (hold-only) queues have no ``_release``
    sibling, so they keep reporting from their own queue.
    """
    return p.cups_name if p.is_network_queue else f"{p.cups_name}_release"


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
        "cups_status": await _cups_status(_status_queue_name(p)),
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


def _validate_probe_ip(ip: str) -> None:
    """F36: reject anything that isn't a plausible LAN host literal --
    unparseable input, or loopback/link-local/multicast/unspecified, which a
    host-networked server has no legitimate reason to probe on an admin's
    behalf. Never raises with the parsed exception text; the detail is a
    fixed, safe message."""
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid IP address") from exc
    if parsed.is_loopback or parsed.is_link_local or parsed.is_multicast or parsed.is_unspecified:
        raise HTTPException(status_code=400, detail="Invalid IP address")


@router.get("/probe")
async def probe_printer_ip(
    ip: str,
    _user: User = Depends(require_admin),
) -> dict:
    """Probe a printer at the given IP address for reachability and IPP details."""
    _validate_probe_ip(ip)
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

    # F113: this name would collide with the built-in zero-config hold queue
    # the static Avahi AirPrint advert hardcodes (rp=printers/Papyrus) --
    # reconfiguring or later deleting a printer named "Papyrus" would
    # clobber/destroy it.
    if cups_name == cups_admin.DEFAULT_QUEUE_NAME:
        raise HTTPException(status_code=400, detail="Name is reserved")

    # Ensure cups_name is unique
    existing = await db.execute(select(Printer).where(Printer.cups_name == cups_name))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409, detail=f"A printer with cups_name '{cups_name}' already exists"
        )

    # F35: the first physical printer added becomes the default, so release
    # never resolves to an empty queue name. Network (hold-only) queues are
    # never eligible (set_default_printer already rejects them).
    is_default = False
    if not body.is_network_queue:
        current_default = await db.execute(
            select(Printer).where(
                Printer.is_default.is_(True), Printer.is_network_queue.is_(False)
            )
        )
        is_default = current_default.scalars().first() is None

    printer = Printer(
        display_name=body.display_name,
        cups_name=cups_name,
        uri=body.uri,
        description=body.description,
        is_network_queue=body.is_network_queue,
        auto_release=body.auto_release,
        is_default=is_default,
    )
    db.add(printer)

    # F33: provision CUPS *before* committing. add_physical_printer/
    # add_network_queue now raise RuntimeError on an lpadmin failure instead
    # of silently "succeeding" -- roll back the uncommitted row rather than
    # leave a Printer row with no working queue behind it (every later
    # release would then 502 with no indication why).
    try:
        if body.is_network_queue:
            await cups_admin.add_network_queue(cups_name, body.display_name)
        else:
            await cups_admin.add_physical_printer(cups_name, body.display_name, body.uri)
    except RuntimeError as exc:
        logger.warning("Failed to provision CUPS queue '%s': %s", cups_name, exc)
        await db.rollback()
        raise ExternalServiceError("Could not create the CUPS queue.") from exc

    await db.commit()
    await db.refresh(printer)

    if not body.is_network_queue and await _enrich_printer_info(printer, body.uri):
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
    was_default = printer.is_default
    await db.delete(printer)
    await db.commit()
    await cups_admin.remove_printer(cups_name)

    # F35: promote the oldest remaining physical printer, or every held job
    # loses its default and release starts failing with "no default printer".
    if was_default:
        result = await db.execute(
            select(Printer)
            .where(Printer.is_network_queue.is_(False))
            .order_by(Printer.id)
        )
        replacement = result.scalars().first()
        if replacement is not None:
            replacement.is_default = True
            await db.commit()


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

    # F11: clear-and-set in a single statement (rather than a separate clear
    # UPDATE followed by setting this row) closes the race where two
    # concurrent calls could both commit is_default=true for different
    # printers -- the partial unique index (migration 014) now also rejects
    # that outright, but a single statement removes the window entirely.
    await db.execute(
        update(Printer).values(is_default=(Printer.id == printer_id))
    )
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
        # Don't leak raw cupsenable/cupsaccept stderr to the client.
        raise PapyrusError("Re-enabling the printer queue failed.") from exc
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

    # Capture scalars before print_test_page's internal commits expire the ORM
    # objects (a lazy reload in this async context would raise MissingGreenlet).
    printer_id_val = printer.id
    display_name = printer.display_name
    user_id = user.id

    # TestPageError is an ExternalServiceError (502); the PrintJob row is
    # already marked failed/broadcast by the service, so let it propagate to
    # the global handler.
    job = await print_test_page(db, printer, user)
    # Serialize while the job row is still loaded (the log_event commit below
    # expires it). Only reached on success — a failed test page raises above.
    response = serialize_print_job(job)
    job_id = job.id

    await log_event(
        db, "print.test_page", "printer", str(printer_id_val),
        user_id=user_id, detail={"job_id": job_id},
    )
    await db.commit()
    await dispatch_webhook(db, "print.test_page", {
        "printer_id": printer_id_val,
        "display_name": display_name,
        "job_id": job_id,
    })

    return response


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
