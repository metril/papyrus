"""eSCL (AirScan) protocol endpoints for network scanner discovery.

Implements the eSCL protocol so devices on the LAN can discover and use
the scanner via Apple AirScan, Mopria, and Windows WSD-eSCL.
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session, get_db
from app.models import ScanJob
from app.services.scan_service import ScanError, get_default_scanner_device, scan_service
from app.services.ws_manager import ws_manager

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/eSCL")

ESCL_NS = "http://schemas.hp.com/imaging/escl/2011/05/03"
PWG_NS = "http://www.pwg.org/schemas/2010/12/sm"


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

# eSCL color mode mapping to scanimage modes
ESCL_COLOR_MAP = {
    "RGB24": "Color",
    "Grayscale8": "Gray",
    "BlackAndWhite1": "Lineart",
}

SCANIMAGE_COLOR_MAP = {v: k for k, v in ESCL_COLOR_MAP.items()}


def _xml_response(root: Element) -> Response:
    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode()
    return Response(content=xml_bytes, media_type="text/xml; charset=utf-8")


@router.get("/ScannerCapabilities")
async def scanner_capabilities():
    """Return scanner capabilities in eSCL XML format."""
    if not settings.escl_enabled:
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
async def scanner_status():
    """Return current scanner status in eSCL XML format."""
    if not settings.escl_enabled:
        raise HTTPException(status_code=503, detail="eSCL scanner disabled")

    state = "Processing" if scan_service._lock.locked() else "Idle"

    root = Element("scan:ScannerStatus")
    root.set("xmlns:scan", ESCL_NS)
    root.set("xmlns:pwg", PWG_NS)

    SubElement(root, "pwg:Version").text = "2.6"
    SubElement(root, "pwg:State").text = state

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
    job = _scan_jobs.get(job_id)
    if job is None:
        return
    job["state"] = "Processing"

    db_job_id: int | None = None

    try:
        async with async_session() as db:
            device = await get_default_scanner_device(db)

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
        CAPS_DPI = 300  # must match MaxWidth/MaxHeight in ScannerCapabilities
        left_mm = top_mm = width_mm = height_mm = None
        region = job.get("scan_region") or {}
        res = job["resolution"]
        if region.get("width"):
            width_mm  = region["width"]    / CAPS_DPI * 25.4
            height_mm = region["height"]   / CAPS_DPI * 25.4
            left_mm   = region["x_offset"] / CAPS_DPI * 25.4
            top_mm    = region["y_offset"] / CAPS_DPI * 25.4

        req_w = round(region["width"]  * res / CAPS_DPI) if region.get("width")  else None
        req_h = round(region["height"] * res / CAPS_DPI) if region.get("height") else None

        _log.info(
            "eSCL job %s: res=%s fmt=%s src=%s region_px=%sx%s mm=%.1fx%.1f",
            job_id, res, job["format"], job["source"],
            req_w, req_h, width_mm or 0, height_mm or 0,
        )

        scan_id, filepath = await scan_service.scan(
            resolution=res,
            mode=job["color_mode"],
            fmt=job["format"],
            source=job["source"],
            device=device,
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
        async with async_session() as db:
            result = await db.get(ScanJob, db_job_id)
            if result:
                result.scan_id = scan_id
                result.filepath = filepath
                result.file_size = file_size
                result.status = "completed"
                result.completed_at = datetime.now(timezone.utc)
                await db.commit()

        # Notify web UI via WebSocket
        await ws_manager.broadcast("scans", {
            "type": "scan_completed",
            "data": {"scan_id": scan_id},
        })

        job["filepath"] = filepath
        job["state"] = "Completed"

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


@router.post("/ScanJobs")
async def create_scan_job(request: Request):
    """Create a new eSCL scan job and immediately start scanning in the background."""
    if not settings.escl_enabled:
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

        # ScanRegion coords are in caps coordinate system (CAPS_DPI=300 in _run_scan)
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
    }

    # Start scan immediately in background — clients poll ScannerStatus for Completed
    asyncio.create_task(_run_scan(job_id))

    return Response(
        status_code=201,
        headers={"Location": f"/eSCL/ScanJobs/{job_id}"},
    )


@router.get("/ScanJobs/{job_id}/NextDocument")
async def get_next_document(job_id: str):
    """Return the scanned document once the background scan has completed."""
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
    """Cancel a scan job and clean up."""
    job = _scan_jobs.pop(job_id, None)
    if job is None:
        raise HTTPException(status_code=404, detail="Scan job not found")

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
