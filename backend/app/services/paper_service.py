"""Paper size currently loaded in the default printer, read over IPP.

Forms use it as their initial paper choice. Anything uncertain (no printer,
unreachable, unrecognised media) yields ``None`` and the UI falls back to A4.
"""

import asyncio
import re
import time
from urllib.parse import urlparse

import httpx

from app.services.ipp_client import IppError, get_printer_attributes

IPP_TIMEOUT_SECONDS = 2.0
OVERALL_TIMEOUT_SECONDS = 3.0
CACHE_TTL_SECONDS = 60.0

_MEDIA_NAMES = {
    "na_letter": "Letter",
    "na_legal": "Legal",
    "iso_a4": "A4",
}
_MEDIA_RE = re.compile(r"^([a-z]+_[a-z0-9]+)_")

_cache: dict[str, tuple[float, str | None]] = {}


def media_label(ipp_media: object) -> str | None:
    """Map an IPP media keyword (``na_letter_8.5x11in``) to a form label."""
    if not isinstance(ipp_media, str):
        return None
    match = _MEDIA_RE.match(ipp_media.strip().lower())
    return _MEDIA_NAMES.get(match.group(1)) if match else None


def _pick(attributes: dict) -> str | None:
    for key in ("media-ready", "media-default"):
        value = attributes.get(key)
        for candidate in value if isinstance(value, list) else [value]:
            label = media_label(candidate)
            if label:
                return label
    return None


async def get_loaded_paper(printer_uri: str | None) -> str | None:
    """Loaded paper label for the printer behind ``printer_uri``, or None.

    Failures are cached too, so an offline printer costs one short timeout
    per TTL rather than one per page view.
    """
    try:
        parsed = urlparse(printer_uri or "")
        host, port, scheme = parsed.hostname, parsed.port or 631, parsed.scheme
    except ValueError:
        return None
    if scheme not in ("ipp", "ipps") or not host:
        return None
    key = f"{scheme}://{host}:{port}"
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]

    resources = list(dict.fromkeys([parsed.path, "/ipp/print", "/ipp"]))

    async def _query() -> str | None:
        for resource in resources:
            try:
                attributes = await get_printer_attributes(
                    host, port, resource, IPP_TIMEOUT_SECONDS, scheme
                )
            except (IppError, ValueError, httpx.InvalidURL):
                continue
            return _pick(attributes)
        return None

    try:
        label = await asyncio.wait_for(_query(), OVERALL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        label = None
    _cache[key] = (now, label)
    return label
