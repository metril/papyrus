import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


# --- Auth ---


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str

    model_config = {"from_attributes": True}


class APITokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    permissions: list[str] = Field(default_factory=lambda: ["print", "scan"])
    expires_in_days: int | None = None


class APITokenResponse(BaseModel):
    id: uuid.UUID
    name: str
    permissions: list[str]
    expires_at: datetime | None
    created_at: datetime
    last_used_at: datetime | None

    model_config = {"from_attributes": True}


class APITokenCreated(APITokenResponse):
    token: str  # Only returned once on creation


# --- Print Jobs ---


class PrintJobUpload(BaseModel):
    copies: int = Field(default=1, ge=1, le=99)
    duplex: bool = False
    media: str = "A4"
    hold: bool = True


class PrintJobResponse(BaseModel):
    id: int
    cups_job_id: int | None
    title: str
    filename: str
    file_size: int
    mime_type: str
    status: str
    copies: int
    duplex: bool
    media: str
    source_type: str
    printer_id: int | None = None
    has_pin: bool = False
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

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


class PrintJobList(BaseModel):
    jobs: list[PrintJobResponse]
    total: int


# --- Scanner ---


class ScanRequest(BaseModel):
    resolution: int = Field(default=300, ge=75, le=600)
    mode: str = Field(default="Color", pattern="^(Color|Gray|Lineart)$")
    format: str = Field(default="pdf", pattern="^(png|jpeg|tiff|pdf)$")
    source: str = Field(default="Flatbed", pattern="^(Flatbed|ADF)$")


class ScanBatchRequest(ScanRequest):
    source: str = "ADF"


class ScanResponse(BaseModel):
    id: int
    scan_id: str
    status: str
    resolution: int
    mode: str
    format: str
    source: str
    page_count: int
    file_size: int | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class ScanList(BaseModel):
    scans: list[ScanResponse]
    total: int


# --- Copy ---


class CopyRequest(BaseModel):
    resolution: int = Field(default=300, ge=75, le=600)
    mode: str = Field(default="Color", pattern="^(Color|Gray|Lineart)$")
    source: str = Field(default="Flatbed", pattern="^(Flatbed|ADF)$")
    copies: int = Field(default=1, ge=1, le=99)
    duplex: bool = False
    media: str = "A4"


# --- SMB ---


class SMBShareCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    server: str = Field(min_length=1, max_length=255)
    share_name: str = Field(min_length=1, max_length=255)
    username: str | None = None
    password: str | None = None
    domain: str = "WORKGROUP"


class SMBShareResponse(BaseModel):
    id: int
    name: str
    server: str
    share_name: str
    username: str | None
    domain: str
    created_at: datetime

    model_config = {"from_attributes": True}


class SMBFileEntry(BaseModel):
    name: str
    is_directory: bool
    size: int
    modified_at: datetime | None


# --- Cloud ---


class CloudProviderResponse(BaseModel):
    id: int
    provider: str
    connected_at: datetime

    model_config = {"from_attributes": True}


class CloudFileEntry(BaseModel):
    name: str
    id: str
    is_directory: bool
    size: int | None
    modified_at: datetime | None
    mime_type: str | None = None


# --- Email ---


class EmailConfig(BaseModel):
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_password: str
    smtp_from: str


class EmailConfigStatus(BaseModel):
    configured: bool
    smtp_host: str | None
    smtp_from: str | None


class EmailSendRequest(BaseModel):
    to: str
    subject: str = "Scanned Document"
    body: str = ""


# --- Printer ---


class MarkerLevel(BaseModel):
    name: str
    level: int  # 0-100, -1 = unknown
    color: str


class PrinterStatus(BaseModel):
    state: int  # 3=idle, 4=printing, 5=stopped
    state_message: str
    accepting_jobs: bool
    markers: list[MarkerLevel] = []
    state_reasons: list[str] = []


class PrinterSettings(BaseModel):
    media: str = "A4"
    duplex: bool = False
    quality: str = "normal"


# --- Scan Profiles ---


class ScanProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    resolution: int = Field(default=300, ge=75, le=600)
    color_mode: str = Field(default="Color", pattern="^(Color|Gray|Lineart)$")
    format: str = Field(default="pdf", pattern="^(png|jpeg|tiff|pdf)$")
    source: str = Field(default="Flatbed", pattern="^(Flatbed|ADF)$")
    ocr_enabled: bool = False


class ScanProfileResponse(BaseModel):
    id: int
    name: str
    resolution: int
    color_mode: str
    format: str
    source: str
    ocr_enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Collation ---


class CollateRequest(BaseModel):
    scan_ids: list[str] = Field(min_length=2, max_length=50)
    output_filename: str = "merged.pdf"


# --- Bulk Delete ---


class BulkDeleteJobsRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=100)


class BulkDeleteScansRequest(BaseModel):
    scan_ids: list[str] = Field(min_length=1, max_length=100)


class BulkDeleteResponse(BaseModel):
    deleted: int


# --- Webhooks ---


class WebhookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=500)
    secret: str | None = None
    events: list[str] = Field(min_length=1)
    enabled: bool = True


class WebhookResponse(BaseModel):
    id: int
    name: str
    url: str
    events: list[str]
    enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# --- System ---


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    cups_running: bool = False
    scanner_available: bool = False
    db_connected: bool = False
    disk_free_mb: int = 0
    uptime_seconds: int = 0


class SystemStatus(BaseModel):
    cups_running: bool
    scanner_available: bool
    db_connected: bool
    disk_free_mb: int


class BackupResponse(BaseModel):
    settings: dict
    exported_at: str
