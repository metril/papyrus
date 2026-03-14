import cups
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

class CupsService:
    def __init__(self, printer_name: str | None = None):
        self.printer_name = printer_name or ""

    def _conn(self) -> cups.Connection:
        return cups.Connection()

    def get_printer_status(self) -> dict:
        """Get printer status from CUPS, including marker/toner info."""
        conn = self._conn()
        try:
            attrs = conn.getPrinterAttributes(self.printer_name)
        except cups.IPPError:
            return {
                "state": 5,  # stopped
                "state_message": "Printer not found or unreachable",
                "accepting_jobs": False,
                "markers": [],
                "state_reasons": [],
            }

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


async def get_default_printer(db: AsyncSession):
    """Return the default physical Printer DB object, or None."""
    from app.models import Printer  # avoid circular import at module level
    result = await db.execute(
        select(Printer).where(Printer.is_default == True, Printer.is_network_queue == False)
    )
    return result.scalar_one_or_none()


async def get_default_printer_name(db: AsyncSession) -> str:
    """Return the CUPS queue name of the default physical printer."""
    printer = await get_default_printer(db)
    return printer.cups_name if printer else ""
