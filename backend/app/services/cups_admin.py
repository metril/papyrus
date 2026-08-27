"""CUPS queue and Avahi service management via subprocess."""
import asyncio
import logging
import os
import re
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

PPD_PATH = "/etc/cups/ppd/papyrus.ppd"
AVAHI_SERVICES_DIR = "/etc/avahi/services"
CUPS_PORT = 6310

# Built-in zero-config hold queue that the static AirPrint advert points at.
# This name MUST stay in sync with:
#   - ``rp=printers/Papyrus`` in ``docker/avahi/airprint.service``
#   - ``_SELF_ADVERTISEMENT_RESOURCE_MARKER`` in ``app/routers/printers.py``
DEFAULT_QUEUE_NAME = "Papyrus"

# CUPS defaults every queue to the ``stop-printer`` error policy, which disables
# the whole queue on any backend failure and wedges all later jobs. Every queue
# Papyrus creates overrides it to ``abort-job`` so one bad job is dropped while
# the queue keeps serving everyone else. The papyrus backend script relies on
# this: it ``exit 1``s on API failure, which is only safe under abort-job.
ERROR_POLICY_OPTS = ["-o", "printer-error-policy=abort-job"]


def _sanitize_cups_name(display_name: str) -> str:
    """Convert a display name to a valid CUPS queue name."""
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", display_name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "printer"


async def _run(args: list[str], ignore_errors: bool = False) -> None:
    """Run a CUPS admin command. Raises RuntimeError on a non-zero exit
    unless ``ignore_errors`` (F33) -- callers that need the queue to actually
    exist (add_physical_printer/add_network_queue/ensure_default_queue) must
    not silently "succeed" when lpadmin rejected the request."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        return
    if ignore_errors:
        logger.debug("Command %s failed (ignored): %s", args, stderr.decode())
        return
    logger.warning("Command %s failed (rc=%d): %s", args, proc.returncode, stderr.decode())
    raise RuntimeError(f"{args[0]} failed (rc={proc.returncode}): {stderr.decode().strip()}")


def _avahi_service_xml(display_name: str, cups_name: str) -> str:
    # F32: display_name is an admin-chosen free-text field, interpolated raw
    # into six XML nodes; an unescaped '&' or '<' produced a file avahi
    # rejected on reload, silently killing that printer's mDNS advert.
    name = escape(display_name)
    queue = escape(cups_name)
    return f"""<?xml version="1.0" standalone="no"?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">{name} @ %h</name>
  <service>
    <type>_ipp._tcp</type>
    <subtype>_universal._sub._ipp._tcp</subtype>
    <port>{CUPS_PORT}</port>
    <txt-record>txtvers=1</txt-record>
    <txt-record>qtotal=1</txt-record>
    <txt-record>rp=printers/{queue}</txt-record>
    <txt-record>ty={name}</txt-record>
    <txt-record>note={name} via Papyrus</txt-record>
    <txt-record>product=({name})</txt-record>
    <txt-record>printer-state=3</txt-record>
    <txt-record>printer-type=0x801046</txt-record>
    <txt-record>pdl=application/octet-stream,application/pdf,application/postscript,image/jpeg,image/png,image/urf</txt-record>
    <txt-record>URF=DM3</txt-record>
    <txt-record>Transparent=T</txt-record>
    <txt-record>Binary=T</txt-record>
    <txt-record>Color=T</txt-record>
    <txt-record>Duplex=T</txt-record>
  </service>
</service-group>
"""


def _avahi_service_path(cups_name: str) -> str:
    return os.path.join(AVAHI_SERVICES_DIR, f"{cups_name}.service")


async def _write_avahi_service(display_name: str, cups_name: str) -> None:
    try:
        os.makedirs(AVAHI_SERVICES_DIR, exist_ok=True)
        with open(_avahi_service_path(cups_name), "w") as f:
            f.write(_avahi_service_xml(display_name, cups_name))
        await _reload_avahi()
    except Exception as e:
        logger.warning("Failed to write Avahi service for %s: %s", cups_name, e)


async def _remove_avahi_service(cups_name: str) -> None:
    path = _avahi_service_path(cups_name)
    try:
        if os.path.exists(path):
            os.remove(path)
        await _reload_avahi()
    except Exception as e:
        logger.warning("Failed to remove Avahi service for %s: %s", cups_name, e)


async def _reload_avahi() -> None:
    await _run(["avahi-daemon", "--reload"], ignore_errors=True)


async def _enable_queue(name: str) -> None:
    await _run(["cupsenable", name], ignore_errors=True)
    await _run(["cupsaccept", name], ignore_errors=True)


async def enable_queue(name: str) -> None:
    """Enable (unpause) a CUPS printer queue. Raises RuntimeError on failure."""
    for cmd in [["cupsenable", name], ["cupsaccept", name]]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"{cmd[0]} '{name}' failed: {stderr.decode().strip()}"
            )


async def add_physical_printer(cups_name: str, display_name: str, uri: str) -> None:
    """Create hold queue (papyrus backend) + release queue (IPP) and advertise on Avahi."""
    # Hold queue — receives network/AirPrint jobs via papyrus backend
    await _run([
        "lpadmin", "-p", cups_name,
        "-v", "papyrus:/",
        "-P", PPD_PATH,
        "-o", "printer-is-shared=true",
        *ERROR_POLICY_OPTS,
        "-E",
    ])
    await _enable_queue(cups_name)

    # Release queue — internal, used by FastAPI when releasing a held job
    release = f"{cups_name}_release"
    await _run([
        "lpadmin", "-p", release,
        "-v", uri,
        "-m", "everywhere",
        *ERROR_POLICY_OPTS,
        "-E",
    ])
    await _enable_queue(release)

    await _write_avahi_service(display_name, cups_name)


async def update_physical_printer(cups_name: str, display_name: str, new_uri: str) -> None:
    """Update the release queue URI and Avahi service name.

    Raises RuntimeError (via ``_run``, F33) if lpadmin rejects the new URI --
    callers must not treat this as a silent no-op.
    """
    release = f"{cups_name}_release"
    await _run(["lpadmin", "-p", release, "-v", new_uri])
    # Re-write Avahi service (display_name may have changed)
    await _write_avahi_service(display_name, cups_name)


async def rename_network_queue(cups_name: str, display_name: str) -> None:
    """Update a network (hold-only) queue's Avahi advert after a display-name
    change.

    Unlike ``update_physical_printer``, there is no ``_release`` sibling and
    no URI to update -- ``add_network_queue`` creates only ``cups_name``
    itself, so an lpadmin call against ``{cups_name}_release`` would always
    fail. ``_write_avahi_service`` never raises (it logs and swallows its own
    errors), matching the historical "best-effort rename" behavior for the
    Avahi side.
    """
    await _write_avahi_service(display_name, cups_name)


async def add_network_queue(cups_name: str, display_name: str) -> None:
    """Create a network-only papyrus backend queue and advertise on Avahi."""
    await _run([
        "lpadmin", "-p", cups_name,
        "-v", "papyrus:/",
        "-P", PPD_PATH,
        "-o", "printer-is-shared=true",
        *ERROR_POLICY_OPTS,
        "-E",
    ])
    await _enable_queue(cups_name)
    await _write_avahi_service(display_name, cups_name)


async def ensure_default_queue() -> None:
    """Create the built-in 'Papyrus' hold queue that the static AirPrint advert
    (docker/avahi/airprint.service) points at.

    Jobs sent here are held and — because no ``Printer`` row is named
    ``Papyrus`` — routed to the default printer at ingest time. No Avahi service
    is written: airprint.service already advertises this queue, so writing one
    would double-advertise ``printers/Papyrus``.
    """
    await _run([
        "lpadmin", "-p", DEFAULT_QUEUE_NAME,
        "-v", "papyrus:/",
        "-P", PPD_PATH,
        "-o", "printer-is-shared=true",
        *ERROR_POLICY_OPTS,
        "-E",
    ])
    await _enable_queue(DEFAULT_QUEUE_NAME)


async def remove_printer(cups_name: str) -> None:
    """Remove CUPS queues and Avahi service for a printer."""
    await _run(["lpadmin", "-x", cups_name], ignore_errors=True)
    await _run(["lpadmin", "-x", f"{cups_name}_release"], ignore_errors=True)
    await _remove_avahi_service(cups_name)
