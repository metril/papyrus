"""eSCL (AirScan) protocol endpoints for network scanner discovery.

Implements the eSCL protocol so devices on the LAN can discover and use
the scanner via Apple AirScan, Mopria, and Windows WSD-eSCL.
"""

import asyncio
import os
import uuid
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session, get_db
from app.services.scan_service import ScanError, get_default_scanner_device, scan_service

router = APIRouter(prefix="/eSCL")

ESCL_NS = "http://schemas.hp.com/imaging/escl/2011/05/03"
PWG_NS = "http://www.pwg.org/schemas/2010/12/sm"

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
    SubElement(root, "scan:State").text = state

    # StateReasons required by eSCL 2.6
    reasons = SubElement(root, "pwg:StateReasons")
    SubElement(reasons, "pwg:StateReason").text = "none"

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
    """Background task: execute scan and update job state."""
    job = _scan_jobs.get(job_id)
    if job is None:
        return
    job["state"] = "Processing"
    try:
        async with async_session() as db:
            device = await get_default_scanner_device(db)
        scan_id, filepath = await scan_service.scan(
            resolution=job["resolution"],
            mode=job["color_mode"],
            fmt=job["format"],
            source=job["source"],
            device=device,
        )
        job["filepath"] = filepath
        job["state"] = "Completed"
    except Exception as e:
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

    # Parse XML settings (best-effort)
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(body)

        # Extract resolution
        for tag in ("XResolution", "scan:XResolution",
                     "{%s}XResolution" % ESCL_NS):
            elem = root.find(f".//{tag}")
            if elem is not None and elem.text:
                resolution = int(elem.text)
                break

        # Extract color mode
        for tag in ("ColorMode", "scan:ColorMode",
                     "{%s}ColorMode" % ESCL_NS):
            elem = root.find(f".//{tag}")
            if elem is not None and elem.text:
                color_mode = ESCL_COLOR_MAP.get(elem.text, "Color")
                break

        # Extract document format
        for tag in ("DocumentFormatExt", "scan:DocumentFormatExt",
                     "{%s}DocumentFormatExt" % ESCL_NS,
                     "DocumentFormat", "pwg:DocumentFormat",
                     "{%s}DocumentFormat" % PWG_NS):
            elem = root.find(f".//{tag}")
            if elem is not None and elem.text:
                mime = elem.text.lower()
                if "jpeg" in mime:
                    fmt = "jpeg"
                elif "png" in mime:
                    fmt = "png"
                else:
                    fmt = "pdf"
                break

        # Extract input source
        for tag in ("InputSource", "scan:InputSource",
                     "{%s}InputSource" % ESCL_NS):
            elem = root.find(f".//{tag}")
            if elem is not None and elem.text:
                text = elem.text.lower()
                if "adf" in text or "feeder" in text:
                    source = "ADF"
                else:
                    source = "Flatbed"
                break
    except Exception:
        pass  # Use defaults if XML parsing fails

    job_id = str(uuid.uuid4())
    _scan_jobs[job_id] = {
        "state": "Pending",
        "resolution": resolution,
        "color_mode": color_mode,
        "format": fmt,
        "source": source,
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

    # Clean up any generated file
    if job.get("filepath") and os.path.exists(job["filepath"]):
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
