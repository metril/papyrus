from app.services.cups_service import cups_service
from app.services.scan_service import ScanError, scan_service


class CopyError(Exception):
    pass


class CopyService:
    async def copy(
        self,
        resolution: int = 300,
        mode: str = "Color",
        source: str = "Flatbed",
        copies: int = 1,
        duplex: bool = False,
        media: str = "A4",
        progress_callback=None,
    ) -> dict:
        """Perform a copy: scan a page then print it.

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
            )
        except ScanError as e:
            raise CopyError(f"Scan failed: {e}")

        # Step 2: Print the scanned image
        try:
            cups_job_id = cups_service.create_held_job(
                filepath=filepath,
                title=f"Copy_{scan_id}",
                copies=copies,
                duplex=duplex,
                media=media,
            )
            cups_service.release_job(cups_job_id)
        except Exception as e:
            raise CopyError(f"Print failed: {e}")

        return {
            "scan_id": scan_id,
            "cups_job_id": cups_job_id,
            "filepath": filepath,
        }


copy_service = CopyService()
