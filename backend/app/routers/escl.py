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
from app.database import get_db
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

    SubElement(platen_caps, "scan:MinWidth").text = "0"
    SubElement(platen_caps, "scan:MaxWidth").text = "2550"  # A4 at 300dpi
    SubElement(platen_caps, "scan:MinHeight").text = "0"
    SubElement(platen_caps, "scan:MaxHeight").text = "3508"

    profiles = SubElement(platen_caps, "scan:SettingProfiles")
    profile = SubElement(profiles, "scan:SettingProfile")

    # Color modes
    color_modes = SubElement(profile, "scan:ColorModes")
    for mode in ("RGB24", "Grayscale8", "BlackAndWhite1"):
        SubElement(color_modes, "scan:ColorMode").text = mode

    # Resolutions
    resolutions = SubElement(profile, "scan:SupportedResolutions")
    discrete = SubElement(resolutions, "scan:DiscreteResolutions")
    for dpi in (75, 150, 300, 600):
        res = SubElement(discrete, "scan:DiscreteResolution")
        SubElement(res, "scan:XResolution").text = str(dpi)
        SubElement(res, "scan:YResolution").text = str(dpi)

    # Document formats
    formats = SubElement(profile, "scan:DocumentFormats")
    for fmt in ("application/pdf", "image/jpeg", "image/png"):
        SubElement(formats, "pwg:DocumentFormat").text = fmt
        SubElement(formats, "scan:DocumentFormatExt").text = fmt

    # ADF capabilities
    adf = SubElement(root, "scan:Adf")
    adf_caps = SubElement(adf, "scan:AdfSimplexInputCaps")
    SubElement(adf_caps, "scan:MinWidth").text = "0"
    SubElement(adf_caps, "scan:MaxWidth").text = "2550"
    SubElement(adf_caps, "scan:MinHeight").text = "0"
    SubElement(adf_caps, "scan:MaxHeight").text = "3508"

    adf_profiles = SubElement(adf_caps, "scan:SettingProfiles")
    adf_profile = SubElement(adf_profiles, "scan:SettingProfile")
    adf_colors = SubElement(adf_profile, "scan:ColorModes")
    for mode in ("RGB24", "Grayscale8", "BlackAndWhite1"):
        SubElement(adf_colors, "scan:ColorMode").text = mode
    adf_res = SubElement(adf_profile, "scan:SupportedResolutions")
    adf_discrete = SubElement(adf_res, "scan:DiscreteResolutions")
    for dpi in (75, 150, 300, 600):
        res = SubElement(adf_discrete, "scan:DiscreteResolution")
        SubElement(res, "scan:XResolution").text = str(dpi)
        SubElement(res, "scan:YResolution").text = str(dpi)
    adf_formats = SubElement(adf_profile, "scan:DocumentFormats")
    for fmt in ("application/pdf", "image/jpeg", "image/png"):
        SubElement(adf_formats, "pwg:DocumentFormat").text = fmt
        SubElement(adf_formats, "scan:DocumentFormatExt").text = fmt

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

    # Report active jobs
    jobs_elem = SubElement(root, "scan:Jobs")
    for job_id, job in _scan_jobs.items():
        if job["state"] in ("Processing", "Pending"):
            job_info = SubElement(jobs_elem, "scan:JobInfo")
            SubElement(job_info, "pwg:JobUri").text = f"/eSCL/ScanJobs/{job_id}"
            SubElement(job_info, "pwg:JobUuid").text = job_id
            SubElement(job_info, "scan:Age").text = "0"
            SubElement(job_info, "pwg:JobState").text = job["state"]

    return _xml_response(root)


@router.post("/ScanJobs")
async def create_scan_job(request: Request):
    """Create a new eSCL scan job from client-submitted XML settings."""
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
    }

    return Response(
        status_code=201,
        headers={"Location": f"/eSCL/ScanJobs/{job_id}"},
    )


@router.get("/ScanJobs/{job_id}/NextDocument")
async def get_next_document(
    job_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Execute the scan and return the result to the client."""
    job = _scan_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Scan job not found")

    if job["state"] == "Completed" and job["filepath"]:
        if job["served"]:
            # No more pages — signal end of job to client
            raise HTTPException(status_code=404, detail="No more pages")
        job["served"] = True
        return _file_response(job)

    if job["state"] == "Processing":
        raise HTTPException(status_code=503, detail="Scan in progress")

    job["state"] = "Processing"

    try:
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
        return _file_response(job)

    except ScanError as e:
        job["state"] = "Canceled"
        raise HTTPException(status_code=503, detail=str(e))


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
