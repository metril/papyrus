import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

_TS = "2026-01-15T09:30:00Z"


class MessageResponse(BaseModel):
    message: str = Field(
        description="Human-readable confirmation.", examples=["Operation successful"]
    )


class OkResponse(BaseModel):
    ok: bool = Field(description="Always true on success.", examples=[True])


class UserResponse(BaseModel):
    id: uuid.UUID = Field(description="User id.")
    email: str = Field(description="Email address.", examples=["alice@example.com"])
    display_name: str = Field(description="Display name.", examples=["Alice Example"])
    role: str = Field(description="Role: 'user' or 'admin'.", examples=["admin"])

    model_config = {"from_attributes": True}


class AdminUserResponse(BaseModel):
    id: str = Field(
        description="User id (UUID).", examples=["3f2b8c1e-5a7d-4b9e-8c11-2d6f0a9e4b77"]
    )
    email: str = Field(description="Email address.", examples=["alice@example.com"])
    display_name: str = Field(description="Display name.", examples=["Alice Example"])
    role: str = Field(description="Role: 'user' or 'admin'.", examples=["user"])
    created_at: str | None = Field(description="ISO 8601 creation time.", examples=[_TS])
    last_login: str | None = Field(
        description="ISO 8601 time of the last login, null if never.", examples=[_TS]
    )


class UserRoleUpdate(BaseModel):
    role: str = Field(pattern=r"^(user|admin)$", description="New role.", examples=["admin"])


class UserRoleResult(BaseModel):
    id: str = Field(
        description="User id (UUID).", examples=["3f2b8c1e-5a7d-4b9e-8c11-2d6f0a9e4b77"]
    )
    role: str = Field(description="The user's new role.", examples=["admin"])


class AuthProviders(BaseModel):
    local_enabled: bool = Field(description="Username/password login is enabled.", examples=[True])
    oidc_enabled: bool = Field(
        description="OIDC login is enabled and an issuer is configured.", examples=[False]
    )
    admin_override: bool = Field(description="An environment-configured admin can always log in "
        "locally.", examples=[False])


class LocalLoginRequest(BaseModel):
    username: str = Field(description="Local account username.", examples=["admin"])
    password: str = Field(description="Local account password.")


class LoginResponse(BaseModel):
    message: str = Field(description="Always 'logged in'.", examples=["logged in"])
    user_id: str = Field(
        description="Id of the authenticated user.",
        examples=["3f2b8c1e-5a7d-4b9e-8c11-2d6f0a9e4b77"],
    )


class APITokenCreate(BaseModel):
    name: str = Field(
        min_length=1, max_length=100, description="Label for the token.", examples=["ci-uploader"]
    )
    permissions: list[str] = Field(
        default_factory=list,
        description="Permissions granted to the token (at least one; must be known permission "
            "names).",
        examples=[["print", "scan"]],
    )
    expires_in_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description="Days until expiry; omit for a non-expiring token.",
        examples=[90],
    )

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
    id: uuid.UUID = Field(description="Token id.")
    name: str = Field(description="Token label.", examples=["ci-uploader"])
    permissions: list[str] = Field(description="Granted permissions.", examples=[["print", "scan"]])
    expires_at: datetime | None = Field(description="Expiry time, null if it never expires.")
    created_at: datetime = Field(description="Creation time.")
    last_used_at: datetime | None = Field(
        description="Last time the token authenticated a request."
    )

    model_config = {"from_attributes": True}


class APITokenCreated(APITokenResponse):
    token: str = Field(description="The plaintext token. Returned only once, at creation; store it "
        "securely.")


class SMBShareCreate(BaseModel):
    name: str = Field(
        min_length=1, max_length=100, description="Display name.", examples=["Office NAS"]
    )
    server: str = Field(
        min_length=1,
        max_length=255,
        description="SMB server host name or IP.",
        examples=["nas.local"],
    )
    share_name: str = Field(
        min_length=1, max_length=255, description="Share name on the server.", examples=["scans"]
    )
    username: str | None = Field(
        default=None, description="Username; omit for guest access.", examples=["scanner"]
    )
    password: str | None = Field(default=None, description="Password; stored encrypted and never "
        "returned.")
    domain: str = Field(
        default="WORKGROUP", description="Windows domain or workgroup.", examples=["WORKGROUP"]
    )


class SMBShareResponse(BaseModel):
    id: int = Field(description="Share id.", examples=[1])
    name: str = Field(description="Display name.", examples=["Office NAS"])
    server: str = Field(description="SMB server host name or IP.", examples=["nas.local"])
    share_name: str = Field(description="Share name on the server.", examples=["scans"])
    username: str | None = Field(
        description="Username, null for guest access.", examples=["scanner"]
    )
    domain: str = Field(description="Windows domain or workgroup.", examples=["WORKGROUP"])
    created_at: datetime = Field(description="Creation time.")

    model_config = {"from_attributes": True}


class SMBFileEntry(BaseModel):
    name: str = Field(description="File or directory name.", examples=["invoice.pdf"])
    is_directory: bool = Field(description="True for a directory.", examples=[False])
    size: int = Field(description="Size in bytes (0 for directories).", examples=[48213])
    modified_at: datetime | None = Field(description="Last modification time.")


class CloudProviderResponse(BaseModel):
    id: int = Field(description="Connection id.", examples=[1])
    provider: str = Field(
        description="Provider key: gdrive, dropbox, onedrive or webdav.", examples=["gdrive"]
    )
    connected_at: datetime = Field(description="When the account was connected.")

    model_config = {"from_attributes": True}


class CloudProviderEntry(BaseModel):
    id: int = Field(description="Connection id.", examples=[1])
    provider: str = Field(
        description="Provider key: gdrive, dropbox, onedrive or webdav.", examples=["gdrive"]
    )
    connected_at: str = Field(description="ISO 8601 connection time.", examples=[_TS])


class CloudProviderList(BaseModel):
    providers: list[CloudProviderEntry] = Field(description="The caller's connected providers.")


class CloudFileEntry(BaseModel):
    name: str = Field(description="File or folder name.", examples=["Taxes 2025.pdf"])
    id: str = Field(description="Provider-specific id (Google Drive/OneDrive file id, or path for "
        "Dropbox).")
    is_directory: bool = Field(description="True for a folder.", examples=[False])
    size: int | None = Field(description="Size in bytes, null when unknown.", examples=[102400])
    modified_at: datetime | None = Field(description="Last modification time.")
    mime_type: str | None = Field(default=None, description="MIME type when the provider reports "
        "one.", examples=["application/pdf"])


class WebDAVConnectRequest(BaseModel):
    url: str = Field(
        default="",
        description="WebDAV base URL (http or https).",
        examples=["https://cloud.example.com/remote.php/dav/files/alice/"],
    )
    username: str = Field(default="", description="WebDAV username.", examples=["alice"])
    password: str = Field(default="", description="WebDAV password or app password; stored "
        "encrypted.")


class WebDAVConnection(BaseModel):
    id: int = Field(description="Connection id (use as provider_id).", examples=[2])
    provider: str = Field(description="Always 'webdav'.", examples=["webdav"])
    url: str = Field(
        description="The WebDAV URL that was connected.",
        examples=["https://cloud.example.com/remote.php/dav/files/alice/"],
    )
    connected_at: str = Field(description="ISO 8601 connection time.", examples=[_TS])


class WebDAVFileEntry(BaseModel):
    name: str = Field(description="File or directory name.", examples=["scan.pdf"])
    path: str = Field(
        description="Path relative to the connection's base URL.", examples=["/Documents/scan.pdf"]
    )
    is_directory: bool = Field(description="True for a collection.", examples=[False])
    size: int | None = Field(description="Size in bytes, null when unknown.", examples=[48213])
    modified_at: str | None = Field(description="ISO 8601 last modification time.", examples=[_TS])
    mime_type: str | None = Field(
        description="MIME type reported by the server.", examples=["application/pdf"]
    )


class WebDAVUploadRequest(BaseModel):
    scan_id: str | None = Field(default=None, description="Scan identifier (ScanJob.scan_id) to "
        "upload. Required.", examples=["a1b2c3d4"])
    destination_folder: str = Field(
        default="/", description="Remote folder to upload into.", examples=["/Scans"]
    )


class EmailConfig(BaseModel):
    smtp_host: str = Field(description="SMTP server host.", examples=["smtp.example.com"])
    smtp_port: int = Field(default=587, description="SMTP server port.", examples=[587])
    smtp_user: str = Field(description="SMTP username.", examples=["scanner@example.com"])
    smtp_password: str = Field(description="SMTP password; stored encrypted and never returned.")
    smtp_from: str = Field(
        description="From address on outgoing mail.", examples=["Papyrus <scanner@example.com>"]
    )


class EmailConfigStatus(BaseModel):
    configured: bool = Field(description="SMTP is fully configured.", examples=[True])
    smtp_host: str | None = Field(
        description="Configured SMTP host.", examples=["smtp.example.com"]
    )
    smtp_from: str | None = Field(
        description="Configured From address.", examples=["scanner@example.com"]
    )


class EmailSendRequest(BaseModel):
    to: str = Field(description="Recipient address.", examples=["bob@example.com"])
    subject: str = Field(
        default="Scanned Document", description="Subject line.", examples=["Scanned Document"]
    )
    body: str = Field(
        default="", description="Plain-text body.", examples=["Attached is the scan."]
    )


class EmailReceivedJob(BaseModel):
    id: int = Field(description="Print job id.", examples=[42])
    title: str = Field(description="Job title (the email subject).", examples=["Invoice March"])


class EmailReceiveResponse(BaseModel):
    jobs: list[EmailReceivedJob] = Field(description="Held print jobs created, one per printable "
        "attachment.")
    total: int = Field(description="Number of jobs created.", examples=[1])


class WebhookInfo(BaseModel):
    webhook_url: str = Field(
        description="URL external services should POST attachments to.",
        examples=["https://papyrus.example.com/api/v1/email/receive"],
    )
    configured: bool = Field(description="A webhook secret has been generated.", examples=[True])


class WebhookSecretResponse(BaseModel):
    secret: str = Field(description="The newly generated shared secret (the `token` form field). "
        "Shown only once.")
    webhook_url: str = Field(
        description="URL external services should POST attachments to.",
        examples=["https://papyrus.example.com/api/v1/email/receive"],
    )


class WebhookCreate(BaseModel):
    name: str = Field(
        min_length=1, max_length=100, description="Label.", examples=["Home Assistant"]
    )
    url: str = Field(
        min_length=1,
        max_length=500,
        description="Delivery URL.",
        examples=["https://hooks.example.com/papyrus"],
    )
    secret: str | None = Field(default=None, description="HMAC signing secret; stored encrypted "
        "and never returned.")
    events: list[str] = Field(min_length=1, description="Subscribed event names (see the events "
        "endpoint).", examples=[["print.release", "scan.complete"]])
    enabled: bool = Field(default=True, description="Whether deliveries are sent.", examples=[True])


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

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="New label.",
        examples=["Home Assistant"],
    )
    url: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="New delivery URL.",
        examples=["https://hooks.example.com/papyrus"],
    )
    secret: str | None = Field(default=None, description="New signing secret; '' clears it, "
        "null/omitted keeps it.")
    events: list[str] | None = Field(default=None, min_length=1, description="New event "
        "subscription list.", examples=[["print.release"]])
    enabled: bool | None = Field(
        default=None, description="Enable or disable deliveries.", examples=[False]
    )


class WebhookResponse(BaseModel):
    id: int = Field(description="Webhook id.", examples=[1])
    name: str = Field(description="Label.", examples=["Home Assistant"])
    url: str = Field(description="Delivery URL.", examples=["https://hooks.example.com/papyrus"])
    events: list[str] = Field(
        description="Subscribed event names.", examples=[["print.release", "scan.complete"]]
    )
    enabled: bool = Field(description="Whether deliveries are sent.", examples=[True])
    created_at: datetime = Field(description="Creation time.")

    model_config = {"from_attributes": True}


class HealthResponse(BaseModel):
    status: str = Field(default="ok", description="'ok' when CUPS and the database are up, "
        "otherwise 'degraded'.", examples=["ok"])
    version: str = Field(description="Papyrus release version.", examples=["1.4.0"])
    cups_running: bool = Field(
        default=False, description="CUPS answered (cached ~15s).", examples=[True]
    )
    scanner_available: bool = Field(default=False, description="A scanner device was detected "
        "(cached ~15s).", examples=[True])
    db_connected: bool = Field(
        default=False, description="The database answered a probe query.", examples=[True]
    )
    disk_free_mb: int = Field(default=0, description="Free space on the scan directory's disk, in "
        "MB.", examples=[20480])
    uptime_seconds: int = Field(
        default=0, description="Seconds since the process started.", examples=[3600]
    )


class SystemStatus(BaseModel):
    cups_running: bool = Field(description="CUPS answered.")
    scanner_available: bool = Field(description="A scanner device was detected.")
    db_connected: bool = Field(description="The database answered.")
    disk_free_mb: int = Field(description="Free disk space in MB.")


class BackupResponse(BaseModel):
    settings: dict[str, str] = Field(
        description="Every AppConfig row as key to value. Encrypted values stay encrypted, so a "
            "backup is only restorable with the same encryption key.",
    )
    exported_at: str = Field(description="ISO 8601 export time.", examples=[_TS])


class RestoreResult(BaseModel):
    restored: int = Field(description="Number of settings written.", examples=[12])


class RetentionResult(BaseModel):
    scans_deleted: int = Field(
        description="Scans removed by the scan retention policy.", examples=[3]
    )
    jobs_deleted: int = Field(
        description="Print jobs removed by the print retention policy.", examples=[5]
    )
    audit_entries_deleted: int = Field(description="Audit rows pruned by the audit retention "
        "policy.", examples=[40])


class TrendPoint(BaseModel):
    date: str = Field(description="UTC calendar day (YYYY-MM-DD).", examples=["2026-01-15"])
    prints: int = Field(description="Print jobs created that day.", examples=[4])
    scans: int = Field(description="Scan jobs created that day.", examples=[2])


class UserUsage(BaseModel):
    username: str = Field(description="User label; 'Network' for unauthenticated jobs, 'Other' for "
        "the rolled-up tail.", examples=["alice"])
    prints: int = Field(description="Print jobs by this user.", examples=[12])
    scans: int = Field(description="Scan jobs by this user.", examples=[7])


class UsageStats(BaseModel):
    print_counts: dict[str, int] = Field(
        description=(
            "Print jobs per status (held, converting, printing, completed, failed, "
            "cancelled), zero-filled."
        ),
        examples=[{"held": 0, "completed": 20}],
    )
    scan_counts: dict[str, int] = Field(description="Scan jobs per status (completed, failed, "
        "scanning, cancelled), zero-filled.",
        examples=[{"completed": 15, "failed": 1, "scanning": 0, "cancelled": 0}])
    trend_30d: list[TrendPoint] = Field(description="Last 30 UTC days, oldest first, zero-filled.")
    per_user: list[UserUsage] = Field(description="Top 10 users by total jobs plus an 'Other' row.")


class AuditEntryResponse(BaseModel):
    id: int = Field(description="Audit entry id.", examples=[100])
    action: str = Field(description="Action name.", examples=["settings.update"])
    entity_type: str | None = Field(
        default=None, description="Kind of entity affected.", examples=["settings"]
    )
    entity_id: str | None = Field(default=None, description="Id of the entity affected.")
    user_id: str | None = Field(default=None, description="Acting user id, null for system/network "
        "actions.")
    source: str | None = Field(default=None, description="Origin of the action (web, api, escl, "
        "...).", examples=["web"])
    ip_address: str | None = Field(
        default=None, description="Client IP address.", examples=["192.168.1.20"]
    )
    detail: dict[str, Any] | None = Field(default=None, description="Action-specific details.")
    created_at: str | None = Field(default=None, description="ISO 8601 time.", examples=[_TS])
