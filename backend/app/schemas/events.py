"""Pydantic models for outgoing webhook payloads and WebSocket messages.

Webhooks: every entry of ``webhook_service.WEBHOOK_EVENTS`` has an envelope
model in ``WEBHOOK_MODELS`` (``event``, ``timestamp``, ``data``). WebSocket:
``WS_MESSAGE_MODELS`` lists every message model per channel. Both sets are
published in the OpenAPI document (see ``api_docs``).
"""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.jobs import PrinterStatus, PrintJobResponse
from app.schemas.scans import ScanResponse

# --- Webhook payloads ------------------------------------------------------


class PrintReleaseData(BaseModel):
    id: int = Field(description="Print job id.")
    title: str
    copies: int


class PrintDeleteData(BaseModel):
    id: int = Field(description="Print job id.")
    title: str


class PrintJobEventData(BaseModel):
    """print.upload, and print.held from uploads. Network-ingest print.held sends ``username``
    instead of ``user_id``."""

    id: int = Field(description="Print job id.")
    title: str
    user_id: str | None = Field(default=None, description="Uploading user's id, if any.")
    username: str | None = Field(default=None, description="Network-ingest jobs only.")
    source_type: str = Field(description="How the job arrived (upload, email, network, ...).")


class PrintTestPageData(BaseModel):
    printer_id: int
    display_name: str
    job_id: int | None = None


class ScanCompleteData(BaseModel):
    scan_id: str
    format: str
    pages: int | None = Field(default=None, description="Present for multi-page/batch scans.")


class ScanDeleteData(BaseModel):
    scan_id: str


class SettingsUpdateData(BaseModel):
    keys: list[str] = Field(description="Names of the settings that changed.")


class SupplyMarker(BaseModel):
    name: str
    level: int = Field(description="Percent 0-100.")


class PrinterAlertData(BaseModel):
    """Common alert fields; ``resolved`` is true on the recovery notification."""

    printer_id: int
    display_name: str
    cups_name: str
    resolved: bool
    message: str


class SupplyLowData(PrinterAlertData):
    markers: list[SupplyMarker]


class PrinterErrorData(PrinterAlertData):
    reason: str = Field(description="`state_reasons` or `offline`.")
    state_reasons: list[str] | None = None


class _Envelope(BaseModel):
    timestamp: datetime = Field(description="UTC ISO-8601 time of dispatch.")


def _envelope(name: str, event: str, data: type[BaseModel], doc: str) -> type[_Envelope]:
    return type(
        name,
        (_Envelope,),
        {
            "__doc__": doc,
            "__module__": __name__,
            "__annotations__": {"event": Literal[event], "data": data},  # type: ignore[valid-type]
        },
    )


WEBHOOK_MODELS: dict[str, type[_Envelope]] = {
    "print.release": _envelope("PrintReleaseWebhook", "print.release", PrintReleaseData,
        "A held print job was released."),
    "print.delete": _envelope("PrintDeleteWebhook", "print.delete", PrintDeleteData,
        "A print job was deleted."),
    "print.upload": _envelope("PrintUploadWebhook", "print.upload", PrintJobEventData,
        "A job was created via upload, share-target or inbound email (held or auto-printed)."),
    "print.held": _envelope("PrintHeldWebhook", "print.held", PrintJobEventData,
        "A job landed in the hold queue."),
    "print.test_page": _envelope("PrintTestPageWebhook", "print.test_page", PrintTestPageData,
        "A printer test page was sent."),
    "scan.complete": _envelope("ScanCompleteWebhook", "scan.complete", ScanCompleteData,
        "A scan finished."),
    "scan.delete": _envelope("ScanDeleteWebhook", "scan.delete", ScanDeleteData,
        "A scan was deleted."),
    "settings.update": _envelope("SettingsUpdateWebhook", "settings.update", SettingsUpdateData,
        "Settings changed (once per update call that changed a value)."),
    "printer.supply_low": _envelope("PrinterSupplyLowWebhook", "printer.supply_low",
        SupplyLowData, "A supply fell below the alert threshold (or recovered)."),
    "printer.error": _envelope("PrinterErrorWebhook", "printer.error", PrinterErrorData,
        "A printer reported an error or went offline (or recovered)."),
}

# --- WebSocket messages ----------------------------------------------------


class JobDeletedData(BaseModel):
    id: int


class ScanDeletedData(BaseModel):
    scan_id: str


class ScanProgressData(BaseModel):
    scan_id: str
    progress: float = Field(description="Percent complete.")


class ScanDoneData(BaseModel):
    scan_id: str
    page_count: int | None = None


class PrinterStatusData(PrinterStatus):
    id: int = Field(description="Printer id.")
    cups_name: str


class JobCreatedMessage(BaseModel):
    """`jobs` channel: a job was created. Always the full object."""
    type: Literal["job_created"]
    data: PrintJobResponse


class JobUpdatedMessage(BaseModel):
    """`jobs` channel: a job changed. Always the full object."""
    type: Literal["job_updated"]
    data: PrintJobResponse


class JobDeletedMessage(BaseModel):
    """`jobs` channel: a job was deleted."""
    type: Literal["job_deleted"]
    data: JobDeletedData


class ScanCompletedMessage(BaseModel):
    """`scans` channel: a scan finished or changed. Always the full object."""
    type: Literal["scan_completed"]
    data: ScanResponse


class ScanDeletedMessage(BaseModel):
    """`scans` channel: a scan was deleted."""
    type: Literal["scan_deleted"]
    data: ScanDeletedData


class PrinterStatusMessage(BaseModel):
    """`printers` channel: a printer's status changed."""
    type: Literal["printer_status"]
    data: PrinterStatusData


class ScanProgressMessage(BaseModel):
    """Per-scan channel: progress update."""
    type: Literal["scan_progress"]
    data: ScanProgressData


class ScanDoneMessage(BaseModel):
    """Per-scan channel: the scan finished."""
    type: Literal["scan_completed"]
    data: ScanDoneData


JobsMessage = Annotated[
    JobCreatedMessage | JobUpdatedMessage | JobDeletedMessage, Field(discriminator="type")
]
ScansMessage = Annotated[ScanCompletedMessage | ScanDeletedMessage, Field(discriminator="type")]
ScanProgressChannelMessage = Annotated[
    ScanProgressMessage | ScanDoneMessage, Field(discriminator="type")
]

WS_MESSAGE_MODELS: dict[str, list[type[BaseModel]]] = {
    "jobs": [JobCreatedMessage, JobUpdatedMessage, JobDeletedMessage],
    "scans": [ScanCompletedMessage, ScanDeletedMessage],
    "printers": [PrinterStatusMessage],
    "scan:{scan_id}": [ScanProgressMessage, ScanDoneMessage],
}
