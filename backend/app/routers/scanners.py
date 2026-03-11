import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_db
from app.models import Scanner, User

router = APIRouter()


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
    """Probe a scanner at the given IP address via eSCL.

    Tries port 80, 54921 (Brother/standard AirScan), and 8080 in order,
    returning the first URL that yields a valid eSCL ScannerCapabilities response.
    """
    import xml.etree.ElementTree as ET
    from urllib.request import urlopen
    from urllib.error import URLError

    fallback_device = f"airscan:e:Scanner_{ip.replace('.', '_')}:http://{ip}/eSCL"
    loop = asyncio.get_event_loop()
    last_error: str = "No eSCL endpoint responded on ports 80, 54921, or 8080"

    for base_url in [
        f"http://{ip}/eSCL",
        f"http://{ip}:54921/eSCL",
        f"http://{ip}:8080/eSCL",
    ]:
        capabilities_url = base_url + "/ScannerCapabilities"
        try:
            def _fetch(u: str = capabilities_url) -> bytes:
                with urlopen(u, timeout=3) as resp:
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
            return {"reachable": True, "device": device, "make_model": make_model, "error": None}
        except TimeoutError:
            last_error = f"{base_url}: timed out"
        except URLError as exc:
            last_error = f"{base_url}: {exc.reason}"
        except OSError as exc:
            last_error = f"{base_url}: {exc}"
        except Exception as exc:
            last_error = f"{base_url}: {exc}"

    return {"reachable": False, "device": fallback_device, "make_model": None, "error": last_error}


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

    scanner = Scanner(
        name=body.name,
        device=body.device,
        description=body.description,
        auto_deliver=body.auto_deliver,
        post_scan_config=body.post_scan_config,
    )
    db.add(scanner)
    await db.commit()
    await db.refresh(scanner)
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
