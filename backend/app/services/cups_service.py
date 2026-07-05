import asyncio
import time

import cups
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class CupsService:
    """Async wrapper around pycups.

    Every pycups call is blocking C code, so the public methods dispatch the
    synchronous work to a worker thread via ``asyncio.to_thread`` to avoid
    stalling the event loop. A short-lived, in-process cache keyed by the CUPS
    queue name serves ``get_printer_status`` results to reduce redundant CUPS
    round-trips under concurrent load (single-worker deployment).
    """

    _STATUS_TTL = 12.0  # seconds

    # Shape returned when the printer is unreachable (IPPError). Kept identical
    # to the historical fallback so API response shapes are unchanged.
    _STATUS_FALLBACK = {
        "state": 5,  # stopped
        "state_message": "Printer not found or unreachable",
        "accepting_jobs": False,
        "markers": [],
        "state_reasons": [],
    }

    # Class-level caches shared across instances. Callers construct a fresh
    # ``CupsService(printer_name=...)`` per request, so per-instance state would
    # never be reused; the cache/locks must live on the class keyed by queue.
    _status_cache: dict[str, tuple[float, dict]] = {}
    _status_locks: dict[str, asyncio.Lock] = {}

    def __init__(self, printer_name: str | None = None):
        self.printer_name = printer_name or ""

    def _conn(self) -> cups.Connection:
        return cups.Connection()

    # ------------------------------------------------------------------
    # Synchronous bodies (run inside a worker thread).
    # ------------------------------------------------------------------

    def _get_printer_status_sync(self) -> dict:
        """Fetch printer status from CUPS. Raises ``cups.IPPError`` if the
        printer is not found or unreachable."""
        conn = self._conn()
        attrs = conn.getPrinterAttributes(self.printer_name)

        # Parse marker (toner/ink) levels
        marker_names = attrs.get("marker-names", [])
        marker_levels = attrs.get("marker-levels", [])
        marker_colors = attrs.get("marker-colors", [])
        if isinstance(marker_names, str):
            marker_names = [marker_names]
        if isinstance(marker_levels, int):
            marker_levels = [marker_levels]
        if isinstance(marker_colors, str):
            marker_colors = [marker_colors]

        markers = []
        for i, name in enumerate(marker_names):
            markers.append({
                "name": name,
                "level": marker_levels[i] if i < len(marker_levels) else -1,
                "color": marker_colors[i] if i < len(marker_colors) else "",
            })

        # State reasons
        reasons = attrs.get("printer-state-reasons", [])
        if isinstance(reasons, str):
            reasons = [reasons]

        return {
            "state": attrs.get("printer-state", 5),
            "state_message": attrs.get("printer-state-message", ""),
            "accepting_jobs": attrs.get("printer-is-accepting-jobs", False),
            "markers": markers,
            "state_reasons": reasons,
        }

    def _get_printer_options_sync(self) -> dict:
        conn = self._conn()
        try:
            attrs = conn.getPrinterAttributes(self.printer_name)
        except cups.IPPError:
            return {}
        return {
            "media_supported": attrs.get("media-supported", []),
            "media_default": attrs.get("media-default", "A4"),
            "sides_supported": attrs.get("sides-supported", []),
            "color_supported": attrs.get("color-supported", False),
        }

    def _create_held_job_sync(
        self,
        filepath: str,
        title: str,
        copies: int = 1,
        duplex: bool = False,
        media: str = "A4",
    ) -> int:
        conn = self._conn()
        options = {
            "copies": str(copies),
            "job-hold-until": "indefinite",
        }
        if duplex:
            options["sides"] = "two-sided-long-edge"
        if media:
            options["media"] = media

        return conn.printFile(self.printer_name, filepath, title, options)

    def _release_job_sync(self, job_id: int) -> None:
        conn = self._conn()
        conn.setJobHoldUntil(job_id, "no-hold")

    def _cancel_job_sync(self, job_id: int) -> None:
        conn = self._conn()
        conn.cancelJob(job_id)

    def _get_job_attributes_sync(self, job_id: int) -> dict:
        conn = self._conn()
        try:
            return conn.getJobAttributes(job_id)
        except cups.IPPError:
            return {}

    def _get_all_jobs_sync(self) -> dict:
        conn = self._conn()
        return conn.getJobs(which_jobs="all", my_jobs=False)

    # ------------------------------------------------------------------
    # Async public API.
    # ------------------------------------------------------------------

    async def get_printer_status(self) -> dict:
        """Get printer status from CUPS, including marker/toner info.

        Served from a per-queue cache for up to ``_STATUS_TTL`` seconds.
        Concurrent misses for the same queue are coalesced behind a per-key
        lock so only one CUPS round-trip happens. Error/fallback results are
        NOT cached, so a recovered printer is reflected on the next request.
        """
        key = self.printer_name

        cached = self._status_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < self._STATUS_TTL:
            return cached[1]

        lock = self._status_locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Re-check: another coroutine may have refreshed while we waited.
            cached = self._status_cache.get(key)
            if cached is not None and time.monotonic() - cached[0] < self._STATUS_TTL:
                return cached[1]

            try:
                result = await asyncio.to_thread(self._get_printer_status_sync)
            except cups.IPPError:
                # Preserve the historical fallback shape; do not cache errors.
                return dict(self._STATUS_FALLBACK)

            self._status_cache[key] = (time.monotonic(), result)
            return result

    async def get_printer_options(self) -> dict:
        """Get available printer options/capabilities."""
        return await asyncio.to_thread(self._get_printer_options_sync)

    async def create_held_job(
        self,
        filepath: str,
        title: str,
        copies: int = 1,
        duplex: bool = False,
        media: str = "A4",
    ) -> int:
        """Create a print job in held state. Returns the CUPS job ID."""
        return await asyncio.to_thread(
            self._create_held_job_sync, filepath, title, copies, duplex, media
        )

    async def release_job(self, job_id: int) -> None:
        """Release a held job to start printing."""
        await asyncio.to_thread(self._release_job_sync, job_id)

    async def cancel_job(self, job_id: int) -> None:
        """Cancel a job."""
        await asyncio.to_thread(self._cancel_job_sync, job_id)

    async def get_job_attributes(self, job_id: int) -> dict:
        """Get attributes for a specific job."""
        return await asyncio.to_thread(self._get_job_attributes_sync, job_id)

    async def get_all_jobs(self) -> dict:
        """Get all jobs from CUPS."""
        return await asyncio.to_thread(self._get_all_jobs_sync)


cups_service = CupsService()


async def get_default_printer(db: AsyncSession):
    """Return the default physical Printer DB object, or None."""
    from app.models import Printer  # avoid circular import at module level
    result = await db.execute(
        select(Printer).where(Printer.is_default.is_(True), Printer.is_network_queue.is_(False))
    )
    return result.scalar_one_or_none()


async def get_default_printer_name(db: AsyncSession) -> str:
    """Return the CUPS queue name of the default physical printer."""
    printer = await get_default_printer(db)
    return printer.cups_name if printer else ""
