import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str

    model_config = {"from_attributes": True}


class APITokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    permissions: list[str] = Field(default_factory=list)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: list[str]) -> list[str]:
        from app.auth.dependencies import ALL_PERMISSIONS

        if not v:
            raise ValueError("At least one permission is required")
        invalid = set(v) - set(ALL_PERMISSIONS)
        if invalid:
            raise ValueError(f"Unknown permissions: {', '.join(sorted(invalid))}")
        return list(dict.fromkeys(v))  # dedupe preserving order


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


class WebhookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=500)
    secret: str | None = None
    events: list[str] = Field(min_length=1)
    enabled: bool = True


class WebhookUpdate(BaseModel):
    """PATCH-shaped body for `PUT /api/webhooks/{id}` (F18).

    Every field is optional and, when omitted, leaves the stored value
    unchanged — a toggle-enabled request can send just `{"enabled": ...}`
    without having to round-trip (and risk clobbering) the name/url/events
    it can't see. `secret` is additionally never cleared by omission *or* by
    an explicit `null` — only a non-null value (including `""`, which
    clears it) changes what's stored, since `WebhookResponse` never returns
    the secret for the client to echo back.
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, min_length=1, max_length=500)
    secret: str | None = None
    events: list[str] | None = Field(default=None, min_length=1)
    enabled: bool | None = None


class WebhookResponse(BaseModel):
    id: int
    name: str
    url: str
    events: list[str]
    enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


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


class AuditEntryResponse(BaseModel):
    id: int
    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    user_id: str | None = None
    source: str | None = None
    ip_address: str | None = None
    detail: dict | None = None
    created_at: str | None = None

