import asyncio
import logging
import os
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_db
from app.models import Scanner, User

logger = logging.getLogger(__name__)
router = APIRouter()

AIRSCAN_PAPYRUS_CONF = "/etc/sane.d/airscan.d/papyrus.conf"
DEFAULT_WSD_PATH = "/WebServices/ScannerService"


def _extract_ip_from_device(device: str) -> str | None:
    """Try to extract an IP address from an airscan device string or name."""
    m = re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", device)
    return m.group(1) if m else None


def _ensure_airscan_config(scanner_name: str, device: str, post_scan_config: dict | None) -> None:
    """Ensure this scanner has a [devices] entry in airscan.conf.

    Works for both new scanners (with post_scan_config) and legacy
    scanners (without it, by deriving URL from the device string).
    """
    cfg = post_scan_config or {}
    url = cfg.get("airscan_url")
    protocol = cfg.get("airscan_protocol")

    if not url:
        # Derive from device string
        ip = _extract_ip_from_device(device)
        if not ip:
            return
        if device.startswith("airscan:w:"):
            url = f"http://{ip}:80{DEFAULT_WSD_PATH}"
            protocol = "wsd"
        elif device.startswith("airscan:e:"):
            # eSCL URL is embedded in the device string: airscan:e:Name:http://...
            m = re.search(r"(https?://\S+)", device)
            url = m.group(1) if m else f"http://{ip}/eSCL"
            protocol = "eSCL"
        else:
            return

    # Extract the display name from the device string (after "airscan:X:")
    name = scanner_name
    if device.startswith("airscan:"):
        parts = device.split(":", 2)
        if len(parts) >= 3:
            name = parts[2]

    _write_airscan_device(name, url, protocol)


def _write_airscan_device(name: str, url: str, protocol: str) -> None:
    """Write a device entry to our own drop-in config file.

    Manages /etc/sane.d/airscan.d/papyrus.conf as a complete file.
    Reads existing entries, adds/updates this one, writes the whole file.
    """
    os.makedirs(os.path.dirname(AIRSCAN_PAPYRUS_CONF), exist_ok=True)

    # Read existing entries from our config file
    devices: dict[str, str] = {}
    try:
        with open(AIRSCAN_PAPYRUS_CONF) as f:
            for line in f:
                line = line.strip()
                if line.startswith('"') and "=" in line:
                    devices[line.split("=")[0].strip()] = line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass

    # Add/update this device
    devices[f'"{name}"'] = f"{url}, {protocol}"

    # Write the complete file
    with open(AIRSCAN_PAPYRUS_CONF, "w") as f:
        f.write("[devices]\n")
        for dev_name, dev_val in devices.items():
            f.write(f"{dev_name} = {dev_val}\n")

    logger.info("Wrote %s with %d device(s)", AIRSCAN_PAPYRUS_CONF, len(devices))


class ScannerCreate(BaseModel):
    name: str
    device: str
    description: str | None = None
    auto_deliver: bool = False
    post_scan_config: dict | None = None


class ScannerUpdate(BaseModel):
    name: str | None = None
    device: str | None = None
    description: str | None = None
    auto_deliver: bool | None = None
    post_scan_config: dict | None = None


def _scanner_response(s: Scanner) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "device": s.device,
        "description": s.description,
        "is_default": s.is_default,
        "auto_deliver": s.auto_deliver,
        "post_scan_config": s.post_scan_config,
        "created_at": s.created_at,
    }


@router.get("")
async def list_scanners(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[dict]:
    result = await db.execute(select(Scanner).order_by(Scanner.id))
    return [_scanner_response(s) for s in result.scalars()]


@router.get("/probe")
async def probe_scanner_ip(
    ip: str,
    _user: User = Depends(require_admin),
) -> dict:
    """Probe a scanner at the given IP using airscan-discover (WSD + eSCL).

    Falls back to manual eSCL port probing if airscan-discover doesn't find the device.
    """
    import xml.etree.ElementTree as ET
    from urllib.request import urlopen
    from urllib.error import URLError

    # --- 1. Try airscan-discover (finds both WSD and eSCL devices) ---
    try:
        proc = await asyncio.create_subprocess_exec(
            "airscan-discover",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode()

        # Parse lines like: "Brother DCP-L2540DW" = http://10.10.77.50:80/wsd, wsd
        for line in output.splitlines():
            if ip not in line:
                continue
            m = re.match(r'\s*"([^"]+)"\s*=\s*(\S+),\s*(\w+)', line)
            if not m:
                continue
            name, url, protocol = m.group(1), m.group(2), m.group(3).lower()
            prefix = "w" if protocol == "wsd" else "e"
            device = f"airscan:{prefix}:{name}"
            _write_airscan_device(name, url, protocol)
            return {
                "reachable": True,
                "device": device,
                "make_model": name,
                "protocol": protocol,
                "airscan_url": url,
                "error": None,
            }
    except (asyncio.TimeoutError, FileNotFoundError) as exc:
        logger.warning("airscan-discover failed: %s", exc)

    # --- 2. Fallback: manual eSCL port probing ---
    loop = asyncio.get_event_loop()
    last_error: str = "No scanner found via discovery or eSCL probe"

    for base_url in [
        f"http://{ip}/eSCL",
        f"http://{ip}:54921/eSCL",
        f"http://{ip}:8080/eSCL",
    ]:
        capabilities_url = base_url + "/ScannerCapabilities"
        try:
            def _fetch(u: str = capabilities_url) -> bytes:
                with urlopen(u, timeout=5) as resp:
                    return resp.read()

            raw = await loop.run_in_executor(None, _fetch)
            make_model = None
            try:
                root = ET.fromstring(raw)
                for elem in root.iter():
                    if elem.tag.endswith("}MakeAndModel") or elem.tag == "MakeAndModel":
                        make_model = elem.text
                        break
            except ET.ParseError:
                pass
            label = make_model or f"Scanner_{ip.replace('.', '_')}"
            device = f"airscan:e:{label}:{base_url}"
            _write_airscan_device(label, base_url, "eSCL")
            return {
                "reachable": True,
                "device": device,
                "make_model": make_model,
                "protocol": "escl",
                "airscan_url": base_url,
                "error": None,
            }
        except TimeoutError:
            last_error = f"{base_url}: timed out"
        except URLError as exc:
            last_error = f"{base_url}: {exc.reason}"
        except OSError as exc:
            last_error = f"{base_url}: {exc}"
        except Exception as exc:
            last_error = f"{base_url}: {exc}"

    # --- 3. Fallback: if host is reachable on port 80, configure as WSD ---
    # WSD uses SOAP over HTTP and can't be detected with a simple GET request.
    # If the host is up and eSCL failed, it's likely a WSD-only printer.
    # Use the known Brother WSD path as default (most common WSD scanner).
    try:
        import socket
        def _check_port(h: str = ip) -> bool:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            try:
                s.connect((h, 80))
                return True
            finally:
                s.close()

        reachable = await loop.run_in_executor(None, _check_port)
        if reachable:
            label = f"Scanner {ip}"
            wsd_url = f"http://{ip}:80/WebServices/ScannerService"
            _write_airscan_device(label, wsd_url, "wsd")
            device = f"airscan:w:{label}"
            return {
                "reachable": True,
                "device": device,
                "make_model": None,
                "protocol": "wsd",
                "airscan_url": wsd_url,
                "error": None,
            }
    except Exception as exc:
        last_error = f"WSD fallback: {exc}"

    return {"reachable": False, "device": f"airscan:e:Scanner_{ip.replace('.', '_')}:http://{ip}/eSCL", "make_model": None, "protocol": None, "airscan_url": None, "error": last_error}


@router.get("/diagnostics")
async def scanner_diagnostics(
    _user: User = Depends(require_admin),
) -> dict:
    """Return scanner diagnostics: config files, scanimage -L, airscan-discover."""
    # Read our papyrus.conf
    papyrus_conf = ""
    try:
        with open(AIRSCAN_PAPYRUS_CONF) as f:
            papyrus_conf = f.read()
    except Exception as exc:
        papyrus_conf = f"Error reading: {exc}"

    # Read main airscan.conf
    airscan_conf = ""
    try:
        with open("/etc/sane.d/airscan.conf") as f:
            airscan_conf = f.read()
    except Exception as exc:
        airscan_conf = f"Error reading: {exc}"

    # Run scanimage -L
    scanimage_list = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "scanimage", "-L",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        scanimage_list = (stdout.decode() + stderr.decode()).strip()
    except Exception as exc:
        scanimage_list = f"Error: {exc}"

    # Run airscan-discover
    discover_output = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "airscan-discover",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        discover_output = (stdout.decode() + stderr.decode()).strip()
    except Exception as exc:
        discover_output = f"Error: {exc}"

    # Check airscan.d/ directory
    airscan_d_files: list[str] = []
    try:
        d = "/etc/sane.d/airscan.d"
        if os.path.isdir(d):
            for fname in os.listdir(d):
                fpath = os.path.join(d, fname)
                with open(fpath) as f:
                    airscan_d_files.append(f"{fname}:\n{f.read()}")
    except Exception as exc:
        airscan_d_files.append(f"Error: {exc}")

    return {
        "papyrus_conf": papyrus_conf,
        "airscan_conf": airscan_conf,
        "scanimage_list": scanimage_list,
        "airscan_discover": discover_output,
        "airscan_d_files": airscan_d_files,
    }


class Brscan4Register(BaseModel):
    name: str
    model: str
    ip: str


@router.post("/register-brscan4")
async def register_brscan4(
    body: Brscan4Register,
    _user: User = Depends(require_admin),
) -> dict:
    """Register a Brother scanner with brsaneconfig4 and return its SANE device string."""
    if not re.match(r"^[\d.]+$", body.ip):
        raise HTTPException(status_code=400, detail="Invalid IP address")

    proc = await asyncio.create_subprocess_exec(
        "brsaneconfig4", "-a",
        f"name={body.name}", f"model={body.model}", f"ip={body.ip}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
    if proc.returncode != 0:
        return {"device": None, "error": stderr.decode().strip() or "brsaneconfig4 failed"}

    proc2 = await asyncio.create_subprocess_exec(
        "scanimage", "-L",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=15)
    output = stdout2.decode()

    device = None
    for line in output.splitlines():
        m = re.search(r"device `(brother4:[^']+)'", line)
        if m:
            device = m.group(1)
            break

    if not device:
        return {"device": None, "error": "Registered but device not found in scanimage -L"}
    return {"device": device, "error": None}


@router.get("/{scanner_id}/test")
async def test_scanner(
    scanner_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict:
    """Test an already-saved scanner: eSCL HTTP check + SANE device check."""
    import re
    import xml.etree.ElementTree as ET
    from urllib.request import urlopen

    scanner = await db.get(Scanner, scanner_id)
    if not scanner:
        raise HTTPException(status_code=404, detail="Scanner not found")

    device = scanner.device

    # Ensure airscan.conf has this device's entry (self-healing after container rebuild)
    if device.startswith("airscan:"):
        _ensure_airscan_config(scanner.name, device, scanner.post_scan_config)

    escl_ok = False
    escl_error: str | None = None
    sane_ok = False
    sane_error: str | None = None
    make_model: str | None = None

    # 1. eSCL HTTP check for airscan:e: or any device with an embedded URL
    m = re.search(r"(https?://[^\s'\"]+/eSCL)", device)
    if m:
        capabilities_url = m.group(1) + "/ScannerCapabilities"
        try:
            loop = asyncio.get_event_loop()

            def _fetch() -> bytes:
                with urlopen(capabilities_url, timeout=5) as r:
                    return r.read()

            raw = await loop.run_in_executor(None, _fetch)
            try:
                root = ET.fromstring(raw)
                for elem in root.iter():
                    if elem.tag.endswith("}MakeAndModel") or elem.tag == "MakeAndModel":
                        make_model = elem.text
                        break
            except ET.ParseError:
                pass
            escl_ok = True
        except Exception as exc:
            escl_error = str(exc)

    # 2. SANE connectivity check via scanimage -d {device} -A (lists device options)
    try:
        proc = await asyncio.create_subprocess_exec(
            "scanimage", "-d", device, "-A",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        sane_ok = proc.returncode == 0
        if not sane_ok:
            sane_error = stderr.decode().strip()[:400]
    except asyncio.TimeoutError:
        sane_error = "scanimage timed out after 10s"
    except FileNotFoundError:
        sane_error = "scanimage not found — is SANE installed in the container?"
    except Exception as exc:
        sane_error = str(exc)

    return {
        "device": device,
        "escl_ok": escl_ok,
        "escl_error": escl_error,
        "sane_ok": sane_ok,
        "sane_error": sane_error,
        "make_model": make_model,
    }


@router.get("/discover")
async def discover_scanners(_user: User = Depends(require_admin)) -> list[dict]:
    """Run scanimage -L and return found SANE devices."""
    proc = await asyncio.create_subprocess_exec(
        "scanimage", "-L",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = (stdout.decode() + stderr.decode()).strip()

    devices = []
    for line in output.splitlines():
        if line.startswith("device"):
            # Format: device `airscan:w:...' is a ...
            import re
            m = re.search(r"device `([^']+)' is a (.+)", line)
            if m:
                devices.append({"device": m.group(1), "description": m.group(2).strip()})

    return devices


@router.post("", status_code=201)
async def add_scanner(
    body: ScannerCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict:
    existing = await db.execute(select(Scanner).where(Scanner.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Scanner '{body.name}' already exists")

    # Ensure post_scan_config has IP for airscan devices (needed for config restoration)
    psc = body.post_scan_config or {}
    if body.device.startswith("airscan:") and "airscan_url" not in psc:
        ip = _extract_ip_from_device(body.device)
        if ip and body.device.startswith("airscan:w:"):
            psc["airscan_url"] = f"http://{ip}:80{DEFAULT_WSD_PATH}"
            psc["airscan_protocol"] = "wsd"

    scanner = Scanner(
        name=body.name,
        device=body.device,
        description=body.description,
        auto_deliver=body.auto_deliver,
        post_scan_config=psc if psc else None,
    )
    db.add(scanner)
    await db.commit()
    await db.refresh(scanner)

    # Write airscan.conf entry immediately
    if body.device.startswith("airscan:"):
        _ensure_airscan_config(scanner.name, scanner.device, scanner.post_scan_config)

    return _scanner_response(scanner)


@router.patch("/{scanner_id}")
async def update_scanner(
    scanner_id: int,
    body: ScannerUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict:
    scanner = await db.get(Scanner, scanner_id)
    if not scanner:
        raise HTTPException(status_code=404, detail="Scanner not found")

    if body.name is not None:
        scanner.name = body.name
    if body.device is not None:
        scanner.device = body.device
    if body.description is not None:
        scanner.description = body.description
    if body.auto_deliver is not None:
        scanner.auto_deliver = body.auto_deliver
    if body.post_scan_config is not None:
        scanner.post_scan_config = body.post_scan_config

    await db.commit()
    await db.refresh(scanner)
    return _scanner_response(scanner)


@router.delete("/{scanner_id}", status_code=204)
async def delete_scanner(
    scanner_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> None:
    scanner = await db.get(Scanner, scanner_id)
    if not scanner:
        raise HTTPException(status_code=404, detail="Scanner not found")
    await db.delete(scanner)
    await db.commit()


@router.post("/{scanner_id}/default", status_code=200)
async def set_default_scanner(
    scanner_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict:
    scanner = await db.get(Scanner, scanner_id)
    if not scanner:
        raise HTTPException(status_code=404, detail="Scanner not found")

    await db.execute(update(Scanner).where(Scanner.is_default == True).values(is_default=False))
    scanner.is_default = True
    await db.commit()
    await db.refresh(scanner)
    return _scanner_response(scanner)
