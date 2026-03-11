"""CUPS queue and Avahi service management via subprocess."""
import asyncio
import logging
import os
import re

logger = logging.getLogger(__name__)

PPD_PATH = "/etc/cups/ppd/papyrus.ppd"
AVAHI_SERVICES_DIR = "/etc/avahi/services"
CUPS_PORT = 6310


def _sanitize_cups_name(display_name: str) -> str:
    """Convert a display name to a valid CUPS queue name."""
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", display_name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "printer"


async def _run(args: list[str], ignore_errors: bool = False) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0 and not ignore_errors:
        logger.warning("Command %s failed (rc=%d): %s", args, proc.returncode, stderr.decode())
    elif proc.returncode != 0:
        logger.debug("Command %s failed (ignored): %s", args, stderr.decode())


def _avahi_service_xml(display_name: str, cups_name: str) -> str:
    return f"""<?xml version="1.0" standalone="no"?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">{display_name} @ %h</name>
  <service>
    <type>_ipp._tcp</type>
    <subtype>_universal._sub._ipp._tcp</subtype>
    <port>{CUPS_PORT}</port>
    <txt-record>txtvers=1</txt-record>
    <txt-record>qtotal=1</txt-record>
    <txt-record>rp=printers/{cups_name}</txt-record>
    <txt-record>ty={display_name}</txt-record>
    <txt-record>note={display_name} via Papyrus</txt-record>
    <txt-record>product=({display_name})</txt-record>
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
        "-E",
    ])
    await _enable_queue(cups_name)

    # Release queue — internal, used by FastAPI when releasing a held job
    release = f"{cups_name}_release"
    await _run([
        "lpadmin", "-p", release,
        "-v", uri,
        "-m", "everywhere",
        "-E",
    ])
    await _enable_queue(release)

    await _write_avahi_service(display_name, cups_name)


async def update_physical_printer(cups_name: str, display_name: str, new_uri: str) -> None:
    """Update the release queue URI and Avahi service name."""
    release = f"{cups_name}_release"
    await _run(["lpadmin", "-p", release, "-v", new_uri])
    # Re-write Avahi service (display_name may have changed)
    await _write_avahi_service(display_name, cups_name)


async def add_network_queue(cups_name: str, display_name: str) -> None:
    """Create a network-only papyrus backend queue and advertise on Avahi."""
    await _run([
        "lpadmin", "-p", cups_name,
        "-v", "papyrus:/",
        "-P", PPD_PATH,
        "-o", "printer-is-shared=true",
        "-E",
    ])
    await _enable_queue(cups_name)
    await _write_avahi_service(display_name, cups_name)


async def remove_printer(cups_name: str) -> None:
    """Remove CUPS queues and Avahi service for a printer."""
    await _run(["lpadmin", "-x", cups_name], ignore_errors=True)
    await _run(["lpadmin", "-x", f"{cups_name}_release"], ignore_errors=True)
    await _remove_avahi_service(cups_name)
