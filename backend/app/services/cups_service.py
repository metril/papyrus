import cups

from app.config import settings


class CupsService:
    def __init__(self, printer_name: str | None = None):
        self.printer_name = printer_name or settings.printer_name

    def _conn(self) -> cups.Connection:
        return cups.Connection()

    def get_printer_status(self) -> dict:
        """Get printer status from CUPS."""
        conn = self._conn()
        try:
            attrs = conn.getPrinterAttributes(self.printer_name)
        except cups.IPPError:
            return {
                "state": 5,  # stopped
                "state_message": "Printer not found or unreachable",
                "accepting_jobs": False,
            }
        return {
            "state": attrs.get("printer-state", 5),
            "state_message": attrs.get("printer-state-message", ""),
            "accepting_jobs": attrs.get("printer-is-accepting-jobs", False),
        }

    def get_printer_options(self) -> dict:
        """Get available printer options/capabilities."""
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

    def create_held_job(
        self,
        filepath: str,
        title: str,
        copies: int = 1,
        duplex: bool = False,
        media: str = "A4",
    ) -> int:
        """Create a print job in held state.

        Returns the CUPS job ID.
        """
        conn = self._conn()
        options = {
            "copies": str(copies),
            "job-hold-until": "indefinite",
        }
        if duplex:
            options["sides"] = "two-sided-long-edge"
        if media:
            options["media"] = media

        job_id = conn.printFile(self.printer_name, filepath, title, options)
        return job_id

    def release_job(self, job_id: int) -> None:
        """Release a held job to start printing."""
        conn = self._conn()
        conn.setJobHoldUntil(job_id, "no-hold")

    def cancel_job(self, job_id: int) -> None:
        """Cancel a job."""
        conn = self._conn()
        conn.cancelJob(job_id)

    def get_job_attributes(self, job_id: int) -> dict:
        """Get attributes for a specific job."""
        conn = self._conn()
        try:
            return conn.getJobAttributes(job_id)
        except cups.IPPError:
            return {}

    def get_all_jobs(self) -> dict:
        """Get all jobs from CUPS."""
        conn = self._conn()
        return conn.getJobs(which_jobs="all", my_jobs=False)


cups_service = CupsService()
