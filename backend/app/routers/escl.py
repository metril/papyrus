"""eSCL (AirScan) protocol endpoints for network scanner discovery.

Implements the eSCL protocol so devices on the LAN can discover and use
the scanner via Apple AirScan, Mopria, and Windows WSD-eSCL.
"""

import asyncio
import contextlib
import ipaddress
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_db
from app.exceptions import ScannerBusyError
from app.models import ScanJob
from app.routers.settings import get_setting
from app.schemas import serialize_scan_job
from app.services.scan_service import get_default_scanner_device, scan_service
from app.services.ws_manager import ws_manager

_log = logging.getLogger(__name__)


def _is_lan_address(host: str) -> bool:
    """Whether `host` parses as a private/loopback/link-local address.
    Unparseable input is treated as not-LAN (fail closed)."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def _require_lan_client(request: Request) -> None:
    """Reject any request that doesn't resolve to a plausible LAN host (F3).

    The eSCL router has no auth of its own by design — real AirScan clients
    (Apple/Mopria/WSD-eSCL) hit it directly with no credentials — so without
    this, anyone who can reach the app's URL at all could drive a scan and
    read back whatever's on the platen.

    The deployment fronts the whole app with Traefik on the same host
    (`network_mode: host`, uvicorn started with no `--proxy-headers`), so
    `request.client.host` — the raw TCP peer — is loopback/private for
    *every* request that reaches this process, proxied or not; on its own
    it can never distinguish a LAN caller from one relayed from the public
    internet. So: when the peer is loopback/private (i.e. it could
    plausibly be the trusted local proxy) AND an `X-Forwarded-For` header is
    present, the *rightmost* XFF entry — the one appended by that single
    trusted proxy hop, everything before it is client-supplied and
    untrustworthy — is evaluated instead of the peer. A peer that isn't
    loopback/private is never trusted to supply XFF at all (nothing stops a
    direct internet caller from setting that header itself), so its own
    address is what's checked, XFF or not. Unparseable/missing host, or an
    XFF whose rightmost entry isn't LAN, → 403 either way: fail closed.
    """
    host = request.client.host if request.client else None
    if host is None:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not _is_lan_address(host):
        raise HTTPException(status_code=403, detail="Forbidden")

    xff = request.headers.get("x-forwarded-for")
    if xff:
        rightmost = xff.rsplit(",", 1)[-1].strip()
        if not _is_lan_address(rightmost):
            raise HTTPException(status_code=403, detail="Forbidden")


router = APIRouter(prefix="/eSCL", dependencies=[Depends(_require_lan_client)])

ESCL_NS = "http://schemas.hp.com/imaging/escl/2011/05/03"
PWG_NS = "http://www.pwg.org/schemas/2010/12/sm"

# F4: matches the DiscreteResolutions advertised in ScannerCapabilities.
ESCL_RESOLUTIONS = (75, 100, 150, 200, 300, 600)
# F4: matches the Max{Width,Height} advertised in ScannerCapabilities (A4 at
# the caps' 300dpi coordinate system) -- the platen/ADF caps this module
# advertises, in px at that same 300dpi basis.
ESCL_MAX_WIDTH = 2550
ESCL_MAX_HEIGHT = 3508


def _find_local(root, local_name):
    """Find first descendant element with given local name, ignoring XML namespace."""
    for elem in root.iter():
        tag = elem.tag
        lname = tag.split("}")[-1] if "}" in tag else tag
        if lname == local_name:
            return elem
    return None

# In-memory scan job store (eSCL jobs are transient)
_scan_jobs: dict[str, dict] = {}

# Jobs are evicted only once they've been in a terminal state (Completed or
# Canceled) for longer than this, never based on total job age — a job stuck
# in Pending/Processing (e.g. a very slow scan, or a client that never came
# back) must never be evicted out from under itself.
_JOB_TTL_SECONDS = 3600.0  # 1 hour
_TERMINAL_STATES = frozenset({"Completed", "Canceled"})


def _purge_stale_jobs() -> None:
    """Evict terminal-state jobs whose ``terminal_at`` stamp is older than
    ``_JOB_TTL_SECONDS``. Called opportunistically on every access/mutation of
    ``_scan_jobs`` so the dict can't grow unboundedly when an eSCL client
    fetches a completed job's document but never issues the final
    ``DELETE /ScanJobs/{id}`` (or never comes back at all after a failure).
    """
    now = time.monotonic()
    stale_ids = [
        job_id
        for job_id, job in _scan_jobs.items()
        if job["state"] in _TERMINAL_STATES
        and job.get("terminal_at") is not None
        and now - job["terminal_at"] > _JOB_TTL_SECONDS
    ]
    for job_id in stale_ids:
        _scan_jobs.pop(job_id, None)


def _snap_resolution(value: int) -> int:
    """Snap a client-requested XResolution to the nearest advertised
    discrete resolution (F4) — an out-of-range value (e.g. a client sending
    an arbitrary DPI) must never reach `scanimage --resolution` unclamped."""
    return min(ESCL_RESOLUTIONS, key=lambda r: abs(r - value))


def _clamp_scan_region(region: dict) -> dict:
    """Validate/clamp a parsed ScanRegion.

    Requires both width and height to be present and positive; if either is
    missing or non-positive, falls back to a full-bed scan (F135 — a client
    that sends only one of the two used to crash `_run_scan`'s mm
    conversion). Otherwise offsets and size are clamped to the advertised
    platen/ADF caps (F4), so an absurd region (e.g. a 200000x200000 request)
    can't make `_trim` allocate an unbounded PIL canvas.
    """
    width = region.get("width")
    height = region.get("height")
    if not width or not height or width <= 0 or height <= 0:
        return {"width": None, "height": None, "x_offset": 0, "y_offset": 0}

    x_offset = max(0, min(region.get("x_offset") or 0, ESCL_MAX_WIDTH))
    y_offset = max(0, min(region.get("y_offset") or 0, ESCL_MAX_HEIGHT))
    width = max(1, min(width, ESCL_MAX_WIDTH - x_offset))
    height = max(1, min(height, ESCL_MAX_HEIGHT - y_offset))

    return {"width": width, "height": height, "x_offset": x_offset, "y_offset": y_offset}


# eSCL color mode mapping to scanimage modes
ESCL_COLOR_MAP = {
    "RGB24": "Color",
    "Grayscale8": "Gray",
    "BlackAndWhite1": "Lineart",
}

SCANIMAGE_COLOR_MAP = {v: k for k, v in ESCL_COLOR_MAP.items()}


def _xml_response(root: Element) -> Response:
    xml_bytes = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode()
    )
    return Response(content=xml_bytes, media_type="text/xml; charset=utf-8")


@router.get("/ScannerCapabilities")
async def scanner_capabilities(db: AsyncSession = Depends(get_db)):
    """Return scanner capabilities in eSCL XML format."""
    escl_enabled = (await get_setting(db, "escl_enabled") or "").lower() in ("true", "1", "yes")
    if not escl_enabled:
        raise HTTPException(status_code=503, detail="eSCL scanner disabled")

    root = Element("scan:ScannerCapabilities")
    root.set("xmlns:scan", ESCL_NS)
    root.set("xmlns:pwg", PWG_NS)

    SubElement(root, "pwg:Version").text = "2.6"
    SubElement(root, "pwg:MakeAndModel").text = "Papyrus Network Scanner"
    SubElement(root, "scan:UUID").text = str(uuid.uuid5(uuid.NAMESPACE_DNS, "papyrus.scanner"))

    # Platen (flatbed) capabilities
    platen = SubElement(root, "scan:Platen")
    platen_caps = SubElement(platen, "scan:PlatenInputCaps")

    SubElement(platen_caps, "scan:MinWidth").text = "16"
    SubElement(platen_caps, "scan:MaxWidth").text = "2550"  # A4 at 300dpi
    SubElement(platen_caps, "scan:MinHeight").text = "16"
    SubElement(platen_caps, "scan:MaxHeight").text = "3508"
    SubElement(platen_caps, "scan:MaxPhysicalWidth").text = "2550"
    SubElement(platen_caps, "scan:MaxPhysicalHeight").text = "3508"
    SubElement(platen_caps, "scan:MaxScanRegions").text = "1"

    profiles = SubElement(platen_caps, "scan:SettingProfiles")
    profile = SubElement(profiles, "scan:SettingProfile")

    # Color modes
    color_modes = SubElement(profile, "scan:ColorModes")
    for mode in ("RGB24", "Grayscale8", "BlackAndWhite1"):
        SubElement(color_modes, "scan:ColorMode").text = mode

    # Content types
    content_types = SubElement(profile, "scan:ContentTypes")
    for ct in ("Photo", "Text", "TextAndPhoto"):
        SubElement(content_types, "pwg:ContentType").text = ct

    # Document formats (must come before SupportedResolutions per eSCL 2.6 schema)
    formats = SubElement(profile, "scan:DocumentFormats")
    for fmt in ("application/pdf", "image/jpeg", "image/png"):
        SubElement(formats, "pwg:DocumentFormat").text = fmt
    for fmt in ("application/pdf", "image/jpeg", "image/png"):
        SubElement(formats, "scan:DocumentFormatExt").text = fmt

    # Resolutions
    resolutions = SubElement(profile, "scan:SupportedResolutions")
    discrete = SubElement(resolutions, "scan:DiscreteResolutions")
    for dpi in (75, 150, 300, 600):
        res = SubElement(discrete, "scan:DiscreteResolution")
        SubElement(res, "scan:XResolution").text = str(dpi)
        SubElement(res, "scan:YResolution").text = str(dpi)

    # ADF (simplex) capabilities — same profile as platen
    adf = SubElement(root, "scan:Adf")
    adf_caps = SubElement(adf, "scan:AdfSimplexInputCaps")

    SubElement(adf_caps, "scan:MinWidth").text = "16"
    SubElement(adf_caps, "scan:MaxWidth").text = "2550"
    SubElement(adf_caps, "scan:MinHeight").text = "16"
    SubElement(adf_caps, "scan:MaxHeight").text = "3508"
    SubElement(adf_caps, "scan:MaxPhysicalWidth").text = "2550"
    SubElement(adf_caps, "scan:MaxPhysicalHeight").text = "3508"
    SubElement(adf_caps, "scan:MaxScanRegions").text = "1"

    adf_profiles = SubElement(adf_caps, "scan:SettingProfiles")
    adf_profile = SubElement(adf_profiles, "scan:SettingProfile")

    adf_color_modes = SubElement(adf_profile, "scan:ColorModes")
    for mode in ("RGB24", "Grayscale8", "BlackAndWhite1"):
        SubElement(adf_color_modes, "scan:ColorMode").text = mode

    adf_content_types = SubElement(adf_profile, "scan:ContentTypes")
    for ct in ("Photo", "Text", "TextAndPhoto"):
        SubElement(adf_content_types, "pwg:ContentType").text = ct

    adf_formats = SubElement(adf_profile, "scan:DocumentFormats")
    for fmt in ("application/pdf", "image/jpeg", "image/png"):
        SubElement(adf_formats, "pwg:DocumentFormat").text = fmt
    for fmt in ("application/pdf", "image/jpeg", "image/png"):
        SubElement(adf_formats, "scan:DocumentFormatExt").text = fmt

    adf_resolutions = SubElement(adf_profile, "scan:SupportedResolutions")
    adf_discrete = SubElement(adf_resolutions, "scan:DiscreteResolutions")
    for dpi in (75, 150, 300, 600):
        adf_res = SubElement(adf_discrete, "scan:DiscreteResolution")
        SubElement(adf_res, "scan:XResolution").text = str(dpi)
        SubElement(adf_res, "scan:YResolution").text = str(dpi)

    return _xml_response(root)


@router.get("/ScannerStatus")
async def scanner_status(db: AsyncSession = Depends(get_db)):
    """Return current scanner status in eSCL XML format."""
    escl_enabled = (await get_setting(db, "escl_enabled") or "").lower() in ("true", "1", "yes")
    if not escl_enabled:
        raise HTTPException(status_code=503, detail="eSCL scanner disabled")

    state = "Processing" if scan_service.is_busy() else "Idle"

    root = Element("scan:ScannerStatus")
    root.set("xmlns:scan", ESCL_NS)
    root.set("xmlns:pwg", PWG_NS)

    SubElement(root, "pwg:Version").text = "2.6"
    SubElement(root, "pwg:State").text = state

    _purge_stale_jobs()

    # Report active jobs so clients can track state transitions
    active_jobs = {jid: j for jid, j in _scan_jobs.items() if j["state"] != "Canceled"}
    if active_jobs:
        jobs_elem = SubElement(root, "scan:Jobs")
        for job_id, job in active_jobs.items():
            job_info = SubElement(jobs_elem, "scan:JobInfo")
            SubElement(job_info, "pwg:JobUri").text = f"/eSCL/ScanJobs/{job_id}"
            SubElement(job_info, "pwg:JobUuid").text = job_id
            SubElement(job_info, "scan:Age").text = "0"
            SubElement(job_info, "pwg:JobState").text = job["state"]

    return _xml_response(root)


async def _run_scan(job_id: str) -> None:
    """Background task: execute scan, persist to DB, and update job state."""
    _purge_stale_jobs()
    job = _scan_jobs.get(job_id)
    if job is None:
        return
    job["state"] = "Processing"

    db_job_id: int | None = None

    try:
        async with async_session() as db:
            device = await get_default_scanner_device(db)
            # F43: resolved fresh per job rather than read off the shared
            # scan_service singleton, which is no longer kept in sync
            # per-request.
            scan_dir = await get_setting(db, "scan_dir") or "/app/data/scans"

            # Create DB record so scan appears in web UI (user_id=None for network jobs)
            db_job = ScanJob(
                user_id=None,
                resolution=job["resolution"],
                mode=job["color_mode"],
                format=job["format"],
                source=job["source"],
                status="scanning",
            )
            db.add(db_job)
            await db.commit()
            await db.refresh(db_job)
            db_job_id = db_job.id

        # eSCL ScanRegion coordinates are in the scanner's capability coordinate
        # system (our MaxWidth/MaxHeight are defined at 300dpi), NOT at the
        # actual scan resolution. Convert to mm using the caps DPI base.
        caps_dpi = 300  # must match MaxWidth/MaxHeight in ScannerCapabilities
        left_mm = top_mm = width_mm = height_mm = None
        region = job.get("scan_region") or {}
        res = job["resolution"]
        if region.get("width"):
            width_mm  = region["width"]    / caps_dpi * 25.4
            height_mm = region["height"]   / caps_dpi * 25.4
            left_mm   = region["x_offset"] / caps_dpi * 25.4
            top_mm    = region["y_offset"] / caps_dpi * 25.4

        req_w = round(region["width"]  * res / caps_dpi) if region.get("width")  else None
        req_h = round(region["height"] * res / caps_dpi) if region.get("height") else None

        _log.info(
            "eSCL job %s: res=%s fmt=%s src=%s region_px=%sx%s mm=%.1fx%.1f",
            job_id, res, job["format"], job["source"],
            req_w, req_h, width_mm or 0, height_mm or 0,
        )

        def _capture_process(proc: asyncio.subprocess.Process) -> None:
            # F110: keep a handle to the running scanimage subprocess so
            # DELETE /ScanJobs/{id} has one available, alongside the task
            # handle set on job creation, even though scan()'s own
            # CancelledError handling is what actually kills it.
            job["process"] = proc

        scan_id, filepath = await scan_service.scan(
            resolution=res,
            mode=job["color_mode"],
            fmt=job["format"],
            source=job["source"],
            device=device,
            scan_dir=scan_dir,
            on_process_start=_capture_process,
            left_mm=left_mm,
            top_mm=top_mm,
            width_mm=width_mm,
            height_mm=height_mm,
        )

        # Always re-save JPEG/PNG with correct DPI and exact dimensions.
        # ICA pre-allocates an exact buffer (width×height×3 bytes) and will
        # corrupt the saved file if our dimensions don't match precisely.
        if req_w and req_h and job["format"] in ("jpeg", "png"):
            from PIL import Image as _PILImage

            def _trim() -> None:
                with _PILImage.open(filepath) as img:
                    actual = img.size
                    _log.info(
                        "eSCL job %s: actual=%s expected=%sx%s dpi=%s",
                        job_id, actual, req_w, req_h, img.info.get("dpi"),
                    )
                    if actual != (req_w, req_h):
                        canvas = _PILImage.new(img.mode, (req_w, req_h),
                                               (255,) * len(img.mode))
                        canvas.paste(img.copy(), (0, 0))
                    else:
                        canvas = img.copy()
                    fmt_str = "JPEG" if job["format"] == "jpeg" else "PNG"
                    canvas.save(filepath, format=fmt_str, dpi=(res, res))

            await asyncio.get_event_loop().run_in_executor(None, _trim)

        file_size = os.path.getsize(filepath)

        # Update DB record with completed scan details
        scan_payload: dict | None = None
        async with async_session() as db:
            result = await db.get(ScanJob, db_job_id)
            if result:
                result.scan_id = scan_id
                result.filepath = filepath
                result.file_size = file_size
                result.status = "completed"
                result.completed_at = datetime.now(timezone.utc)
                await db.commit()
                await db.refresh(result)
                scan_payload = serialize_scan_job(result)

        # Notify web UI via WebSocket with the full scan object (matches the
        # scan list endpoint shape) so clients can apply it incrementally.
        # If the row was deleted concurrently, there's no full object to send —
        # skip the broadcast entirely rather than violate the full-object
        # invariant the "scans" channel relies on.
        if scan_payload is not None:
            await ws_manager.broadcast("scans", {
                "type": "scan_completed",
                "data": scan_payload,
            })

        job["filepath"] = filepath
        job["state"] = "Completed"
        job["terminal_at"] = time.monotonic()

    except Exception as e:
        _log.error("eSCL scan %s failed: %s", job_id, e, exc_info=True)
        if db_job_id is not None:
            try:
                async with async_session() as db:
                    result = await db.get(ScanJob, db_job_id)
                    if result:
                        result.status = "failed"
                        result.error_message = str(e)
                        await db.commit()
            except Exception:
                pass
        job["state"] = "Canceled"
        job["error"] = str(e)
        job["terminal_at"] = time.monotonic()


@router.post("/ScanJobs")
async def create_scan_job(request: Request, db: AsyncSession = Depends(get_db)):
    """Create a new eSCL scan job and immediately start scanning in the background."""
    escl_enabled = (await get_setting(db, "escl_enabled") or "").lower() in ("true", "1", "yes")
    if not escl_enabled:
        raise HTTPException(status_code=503, detail="eSCL scanner disabled")

    # Parse scan settings from request body XML
    body = await request.body()

    resolution = 300
    color_mode = "Color"
    fmt = "pdf"
    source = "Flatbed"
    scan_region: dict = {"width": None, "height": None, "x_offset": 0, "y_offset": 0}

    # Parse XML settings (best-effort; use local-name search to avoid namespace issues)
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(body)

        elem = _find_local(root, "XResolution")
        if elem is not None and elem.text:
            resolution = int(elem.text)

        elem = _find_local(root, "ColorMode")
        if elem is not None and elem.text:
            color_mode = ESCL_COLOR_MAP.get(elem.text, "Color")

        elem = _find_local(root, "DocumentFormatExt") or _find_local(root, "DocumentFormat")
        if elem is not None and elem.text:
            mime = elem.text.lower()
            if "jpeg" in mime:
                fmt = "jpeg"
            elif "png" in mime:
                fmt = "png"
            else:
                fmt = "pdf"

        elem = _find_local(root, "InputSource")
        if elem is not None and elem.text:
            text = elem.text.lower()
            source = "ADF" if ("adf" in text or "feeder" in text) else "Flatbed"

        # ScanRegion coords are in caps coordinate system (caps_dpi=300 in _run_scan)
        elem = _find_local(root, "Width")
        if elem is not None and elem.text:
            scan_region["width"] = int(elem.text)
        elem = _find_local(root, "Height")
        if elem is not None and elem.text:
            scan_region["height"] = int(elem.text)
        elem = _find_local(root, "XOffset")
        if elem is not None and elem.text:
            scan_region["x_offset"] = int(elem.text)
        elem = _find_local(root, "YOffset")
        if elem is not None and elem.text:
            scan_region["y_offset"] = int(elem.text)

    except Exception:
        pass  # Use defaults if XML parsing fails

    # F4/F135: snap resolution to an advertised discrete value and
    # validate/clamp the requested region against the advertised caps
    # (or fall back to a full-bed scan) before anything is stored or acted on.
    resolution = _snap_resolution(resolution)
    scan_region = _clamp_scan_region(scan_region)

    _purge_stale_jobs()

    # F3: reject a new job outright while the scanner is already busy (either
    # a real scan in progress, or another eSCL job still Pending/Processing)
    # instead of inserting a DB row and a job entry that's doomed to fail
    # with "Scanner is busy" once _run_scan actually reaches scan_service.scan.
    if scan_service.is_busy() or any(
        j["state"] in ("Pending", "Processing") for j in _scan_jobs.values()
    ):
        raise ScannerBusyError("Scanner is busy. Try again later.")

    job_id = str(uuid.uuid4())
    _scan_jobs[job_id] = {
        "state": "Pending",
        "resolution": resolution,
        "color_mode": color_mode,
        "format": fmt,
        "source": source,
        "scan_region": scan_region,
        "filepath": None,
        "served": False,
        "error": None,
        "terminal_at": None,
    }

    # Start scan immediately in background — clients poll ScannerStatus for
    # Completed. The task handle is kept on the job entry (alongside the
    # subprocess handle _run_scan captures once scanning starts) so
    # DELETE /ScanJobs/{id} can actually cancel an in-flight scan (F110).
    task = asyncio.create_task(_run_scan(job_id))
    _scan_jobs[job_id]["task"] = task

    return Response(
        status_code=201,
        headers={"Location": f"/eSCL/ScanJobs/{job_id}"},
    )


@router.get("/ScanJobs/{job_id}/NextDocument")
async def get_next_document(job_id: str):
    """Return the scanned document once the background scan has completed."""
    _purge_stale_jobs()
    job = _scan_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Scan job not found")

    if job["state"] == "Canceled":
        raise HTTPException(status_code=503, detail=job.get("error") or "Scan failed")

    if job["state"] in ("Pending", "Processing"):
        raise HTTPException(status_code=503, detail="Scan in progress")

    # state == "Completed"
    if job["served"]:
        # No more pages — signal end of job to client
        raise HTTPException(status_code=404, detail="No more pages")

    job["served"] = True
    return _file_response(job)


@router.delete("/ScanJobs/{job_id}")
async def cancel_scan_job(job_id: str):
    """Cancel a scan job and clean up.

    F110: a Pending/Processing job's background task is actually cancelled
    (not just forgotten) — `scan_service.scan`'s own CancelledError handling
    kills the scanimage subprocess and removes any partial output before the
    cancellation propagates back here, so a client that cancels no longer
    gets a 200 while the scan silently keeps running, holds the scanner
    lock, and shows up in the web UI anyway.
    """
    _purge_stale_jobs()
    job = _scan_jobs.pop(job_id, None)
    if job is None:
        raise HTTPException(status_code=404, detail="Scan job not found")

    task = job.get("task")
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # Only delete the file for canceled/failed scans; completed scans live in the web UI
    if job.get("state") != "Completed" and job.get("filepath") and os.path.exists(job["filepath"]):
        os.unlink(job["filepath"])

    return Response(status_code=200)


def _file_response(job: dict) -> FileResponse:
    """Return the scanned file with appropriate MIME type."""
    filepath = job["filepath"]
    fmt = job["format"]

    media_types = {
        "pdf": "application/pdf",
        "jpeg": "image/jpeg",
        "png": "image/png",
    }

    return FileResponse(
        path=filepath,
        media_type=media_types.get(fmt, "application/octet-stream"),
        filename=f"scan.{fmt}",
    )
