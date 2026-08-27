from app.exceptions import PapyrusError
from app.services.cups_service import CupsService
from app.services.scan_service import ScanError, scan_service


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
    ) -> dict:
        """Perform a copy: scan a page then print it.

        `cups`, `device`, and `scan_dir` are resolved by the caller
        (routers/copy.py) from the DB's default printer/scanner/scan_dir
        setting — the module-level singletons used to have an empty printer
        name and never-configured scanner device, so every copy failed
        (F10), and `scan_dir` was silently ignored (falling back to the
        `scan_service` singleton's default) since copy() never passed it
        through (F43).

        Returns dict with scan_id and cups_job_id.
        """
        # Step 1: Scan
        try:
            scan_id, filepath = await scan_service.scan(
                resolution=resolution,
                mode=mode,
                fmt="tiff",  # Use TIFF for best print quality
                source=source,
                progress_callback=progress_callback,
                device=device,
                scan_dir=scan_dir,
            )
        except ScanError as e:
            raise CopyError(f"Scan failed: {e}")

        # Step 2: Print the scanned image
        try:
            cups_job_id = await cups.create_held_job(
                filepath=filepath,
                title=f"Copy_{scan_id}",
                copies=copies,
                duplex=duplex,
                media=media,
            )
            await cups.release_job(cups_job_id)
        except Exception as e:
            raise CopyError(f"Print failed: {e}")

        return {
            "scan_id": scan_id,
            "cups_job_id": cups_job_id,
            "filepath": filepath,
        }


copy_service = CopyService()
