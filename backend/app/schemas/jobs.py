# ruff: noqa: E501
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class PrintJobUpload(BaseModel):
    """Print options accepted by the upload endpoint (sent as form fields)."""

    copies: int = Field(default=1, ge=1, le=99, description="Number of copies.", examples=[1])
    duplex: bool = Field(default=False, description="Print on both sides.")
    media: str = Field(default="A4", description="Paper size.", examples=["A4", "Letter"])
    hold: bool = Field(default=True, description="Hold in the queue until released.")


class PrintJobResponse(BaseModel):
    """A print job as stored; also the payload of jobs-channel WebSocket events."""

    id: int = Field(description="Job id.", examples=[42])
    user_id: uuid.UUID | None = Field(
        default=None, description="Owner; null for network (CUPS) jobs.",
        examples=["3f1c2b4e-8a57-4c1d-9e0b-6a2f5d7c1b90"],
    )
    cups_job_id: int | None = Field(description="CUPS job id once queued, else null.", examples=[17])
    title: str = Field(description="Document title.", examples=["Quarterly report"])
    filename: str = Field(description="Stored file name.", examples=["report.pdf"])
    file_size: int = Field(description="File size in bytes.", examples=[184320])
    mime_type: str = Field(description="Detected MIME type.", examples=["application/pdf"])
    status: str = Field(
        description="Lifecycle state (held, printing, completed, failed, cancelled, ...).",
        examples=["held"],
    )
    copies: int = Field(description="Number of copies.", examples=[1])
    duplex: bool = Field(description="Two-sided printing.")
    media: str = Field(description="Paper size.", examples=["A4"])
    source_type: str = Field(description="Origin of the job (upload, email, network, ...).", examples=["upload"])
    printer_id: int | None = Field(default=None, description="Assigned printer; null means the default.", examples=[1])
    has_pin: bool = Field(default=False, description="True when a release PIN is set (the PIN itself is never returned).")
    error_message: str | None = Field(description="Failure reason, if any.", examples=[None])
    created_at: datetime = Field(description="Creation time (UTC).")
    updated_at: datetime = Field(description="Last update time (UTC).")
    completed_at: datetime | None = Field(description="Completion time (UTC), if finished.")

    model_config = {"from_attributes": True}

    @model_validator(mode="wrap")
    @classmethod
    def _compute_has_pin(cls, values, handler):
        obj = handler(values)
        # Compute has_pin from the source ORM object or dict
        if hasattr(values, "release_pin"):
            obj.has_pin = bool(values.release_pin)
        elif isinstance(values, dict) and "release_pin" in values:
            obj.has_pin = bool(values["release_pin"])
        return obj


def serialize_print_job(job) -> dict:
    """Serialize a PrintJob ORM object into the exact JSON shape returned by the
    print-job list/detail endpoints (``PrintJobResponse``).

    Reused for WebSocket broadcasts so realtime payloads match the REST shape
    field-for-field. ``release_pin`` is deliberately never included — only the
    computed ``has_pin`` bool is exposed — so broadcasting a full job to every
    connected client can never leak a PIN.
    """
    return PrintJobResponse.model_validate(job, from_attributes=True).model_dump(mode="json")


class CopyRequest(BaseModel):
    """Scan-then-print request."""

    resolution: int = Field(default=300, ge=75, le=600, description="Scan resolution in dpi.", examples=[300])
    mode: str = Field(default="Color", pattern="^(Color|Gray|Lineart)$", description="Scan colour mode.", examples=["Color"])
    source: str = Field(default="Flatbed", pattern="^(Flatbed|ADF)$", description="Scan source.", examples=["Flatbed"])
    copies: int = Field(default=1, ge=1, le=99, description="Copies to print.", examples=[1])
    duplex: bool = Field(default=False, description="Print on both sides.")
    media: str = Field(default="A4", description="Paper size.", examples=["A4"])


class CopyResponse(BaseModel):
    """Result of a started copy."""

    message: str = Field(description="Status message.", examples=["Copy initiated"])
    scan_id: str = Field(description="Id of the scan that was created.", examples=["a1b2c3d4"])
    cups_job_id: int = Field(description="CUPS job id of the print.", examples=[18])


class MarkerLevel(BaseModel):
    """One consumable (toner/drum) level."""

    name: str = Field(description="Marker name.", examples=["Black Toner"])
    level: int = Field(description="Percent 0-100; -1 = unknown.", examples=[80])
    color: str = Field(description="Marker colour as reported by CUPS.", examples=["#000000"])


class PrinterStatus(BaseModel):
    """Live CUPS status of a printer queue."""

    state: int = Field(description="IPP printer-state: 3=idle, 4=printing, 5=stopped.", examples=[3])
    state_message: str = Field(description="Human-readable state message.", examples=[""])
    accepting_jobs: bool = Field(description="Whether the queue accepts jobs.")
    markers: list[MarkerLevel] = Field(default=[], description="Consumable levels.")
    state_reasons: list[str] = Field(default=[], description="IPP printer-state-reasons.", examples=[["none"]])


class PrinterSettings(BaseModel):
    """Default print settings."""

    media: str = Field(default="A4", description="Paper size.", examples=["A4"])
    duplex: bool = Field(default=False, description="Two-sided printing.")
    quality: str = Field(default="normal", description="Print quality.", examples=["normal"])


class PrinterOptions(BaseModel):
    """Capabilities reported by the printer. All fields are absent (empty object) if CUPS cannot be queried."""

    media_supported: list[str] = Field(default=[], description="Supported paper sizes.", examples=[["iso_a4_210x297mm"]])
    media_default: str = Field(default="A4", description="Default paper size.", examples=["iso_a4_210x297mm"])
    sides_supported: list[str] = Field(default=[], description="Supported duplex modes.", examples=[["one-sided", "two-sided-long-edge"]])
    color_supported: bool = Field(default=False, description="Whether colour printing is supported.")


class LoadedPaper(BaseModel):
    """Paper loaded in the default printer."""

    media: str | None = Field(description="A4, Letter or Legal; null when unknown.", examples=["A4"])


class BulkDeleteJobsRequest(BaseModel):
    """Job ids to delete."""

    ids: list[int] = Field(min_length=1, max_length=100, description="Job ids (1-100).", examples=[[1, 2, 3]])


class PrinterAssign(BaseModel):
    """Reassign a held job."""

    printer_id: int = Field(description="Target printer id (not a network queue).", examples=[1])


class ReleaseRequest(BaseModel):
    """Optional release PIN."""

    pin: str | None = Field(default=None, description="Release PIN, required if the job has one.", examples=["1234"])


class ReprintRequest(BaseModel):
    """Optional PIN for reprinting a PIN-protected job."""

    pin: str | None = Field(default=None, description="PIN of the original job, if it has one.", examples=["1234"])


class PrinterCreate(BaseModel):
    """New printer / network hold queue."""

    display_name: str = Field(description="Name shown in the UI (also seeds the CUPS queue name).", examples=["Office Brother"])
    uri: str = Field(default="", description="Device URI, e.g. ipp://host/ipp.", examples=["ipp://192.168.1.50:631/ipp/print"])
    description: str | None = Field(default=None, description="Free-text description.")
    is_network_queue: bool = Field(default=False, description="Create a hold-only queue for network clients instead of a physical printer.")
    auto_release: bool = Field(default=False, description="Print network jobs immediately instead of holding them.")


class PrinterUpdate(BaseModel):
    """Partial printer update; omitted fields are unchanged."""

    display_name: str | None = Field(default=None, description="New display name.")
    uri: str | None = Field(default=None, description="New device URI.")
    description: str | None = Field(default=None, description="New description.")
    auto_release: bool | None = Field(default=None, description="Auto-release network jobs.")


class PrinterInfo(BaseModel):
    """A configured printer with live status."""

    id: int = Field(description="Printer id.", examples=[1])
    display_name: str = Field(description="Display name.", examples=["Office Brother"])
    cups_name: str = Field(description="CUPS queue name.", examples=["Office_Brother"])
    uri: str = Field(description="Device URI.", examples=["ipp://192.168.1.50:631/ipp/print"])
    description: str | None = Field(description="Description.")
    make_and_model: str | None = Field(description="Make and model from IPP probing.", examples=["Brother DCP-L2540DW"])
    location: str | None = Field(description="Location from IPP probing.", examples=["Office"])
    is_default: bool = Field(description="Whether this is the default printer.")
    is_network_queue: bool = Field(description="True for a hold-only network queue.")
    auto_release: bool = Field(description="Network jobs print without being held.")
    created_at: datetime = Field(description="Creation time (UTC).")
    cups_status: PrinterStatus = Field(description="Live CUPS status (stopped/Unavailable when CUPS cannot be reached).")


class DiscoveredPrinter(BaseModel):
    """A printer found via mDNS."""

    name: str = Field(description="mDNS service name.", examples=["Brother DCP-L2540DW series"])
    make_model: str | None = Field(default=None, description="Make and model.", examples=["Brother DCP-L2540DW series"])
    location: str | None = Field(default=None, description="Advertised location.")
    uuid: str | None = Field(default=None, description="Device UUID.")
    protocols: list[str] = Field(description="Protocols seen.", examples=[["ipp", "ipps"]])
    uri: str | None = Field(default=None, description="Preferred device URI.", examples=["ipp://192.168.1.50:631/ipp/print"])
    ip: str | None = Field(default=None, description="Device IP address.", examples=["192.168.1.50"])
    port: int | None = Field(default=None, description="Service port.", examples=[631])
    already_configured: bool = Field(description="Already added as a printer.")


class DiscoverPrintersResponse(BaseModel):
    """Result of an mDNS printer scan."""

    printers: list[DiscoveredPrinter] = Field(description="Discovered devices.")


class PrinterProbeResponse(BaseModel):
    """Reachability and IPP details for an IP address."""

    reachable: bool = Field(description="TCP 631 or 80 answered.")
    uri: str = Field(description="Suggested device URI.", examples=["ipp://192.168.1.50:631/ipp/print"])
    make_model: str | None = Field(description="Make and model, if IPP answered.")
    location: str | None = Field(description="Location, if IPP answered.")
    state: int | None = Field(description="IPP printer state, if IPP answered.")
    suggested_display_name: str | None = Field(description="Suggested display name.")
