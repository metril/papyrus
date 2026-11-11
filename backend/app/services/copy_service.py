import logging

from app.exceptions import PapyrusError
from app.services.cups_service import CupsService
from app.services.scan_service import (
    ScanError,
    feeder_takes_every_sheet,
    is_native_device,
    scan_service,
)

logger = logging.getLogger(__name__)


class CopyError(PapyrusError):
    status_code = 502


class CopyService:
    async def copy(
        self,
        cups: CupsService,
        device: str,
        resolution: int = 300,
        mode: str = "Color",
        source: str = "Flatbed",
        copies: int = 1,
        duplex: bool = False,
        media: str = "A4",
        progress_callback=None,
        scan_dir: str | None = None,
        on_scanned=None,
        run_scan=None,
        on_page_count=None,
        on_cups_job=None,
    ) -> dict:
        """Perform a copy: scan a page then print it.

        `cups`, `device`, and `scan_dir` are resolved by the caller
        (routers/copy.py) from the DB's default printer/scanner/scan_dir
        setting — the module-level singletons used to have an empty printer
        name and never-configured scanner device, so every copy failed
        (F10), and `scan_dir` was silently ignored (falling back to the
        `scan_service` singleton's default) since copy() never passed it
        through (F43).

        `run_scan(coro)` (optional) awaits the scan coroutine on the caller's behalf, e.g. as a
        registered cancellable task; `on_page_count(n)` mirrors live sheet counts and
        `on_cups_job(id)` receives the CUPS job id as soon as it exists.

        Returns dict with scan_id and cups_job_id.
        """
        # These feeders take every sheet and are PDF-only.
        native_feeder = feeder_takes_every_sheet(device) and source.lower() == "adf"
        fmt = "pdf" if native_feeder else "tiff"
        page_count = 1
        fed_source = None

        def set_page_count(count: int) -> None:
            nonlocal page_count
            page_count = count
            if on_page_count is not None:
                on_page_count(count)

        def set_fed_by_feeder() -> None:
            nonlocal fed_source
            fed_source = "ADF"

        # Step 1: Scan
        try:
            scan_coro = scan_service.scan(
                resolution=resolution,
                mode=mode,
                fmt=fmt,  # TIFF for best print quality
                **(
                    {"page_count_callback": set_page_count, "feeder_callback": set_fed_by_feeder}
                    if native_feeder or is_native_device(device)
                    else {}
                ),
                source=source,
                progress_callback=progress_callback,
                **({"live_pages_callback": on_page_count} if on_page_count else {}),
                device=device,
                scan_dir=scan_dir,
            )
            scan_id, filepath = await (run_scan(scan_coro) if run_scan else scan_coro)
        except ScanError as e:
            logger.warning("Copy: scan step failed: %s", e)
            raise CopyError("Scan failed while making a copy")

        # Paper in the feeder takes over a glass request: the whole stack is one PDF.
        if fed_source:
            fmt = "pdf"
            source = fed_source

        # The scan is kept in history even if printing fails below.
        if on_scanned is not None:
            await on_scanned({
                "scan_id": scan_id, "filepath": filepath, "format": fmt,
                "page_count": page_count, "source": source,
            })

        # Step 2: Print the scanned image
        try:
            cups_job_id = await cups.create_held_job(
                filepath=filepath,
                title=f"Copy_{scan_id}",
                copies=copies,
                duplex=duplex,
                media=media,
            )
            if on_cups_job is not None:
                on_cups_job(cups_job_id)
            await cups.release_job(cups_job_id)
        except Exception as e:
            logger.warning("Copy: print step failed: %s", e)
            raise CopyError(
                "Print failed while making a copy. The scan was saved to the scan history."
            )

        return {
            "scan_id": scan_id,
            "cups_job_id": cups_job_id,
            "filepath": filepath,
            "format": fmt,
            "page_count": page_count,
            "source": source,
        }


copy_service = CopyService()
