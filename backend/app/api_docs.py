# ruff: noqa: E501
"""Shared OpenAPI documentation helpers: tag metadata, error models, reusable
``responses=`` constants and the operationId generator."""
from typing import Any

from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

TAGS_METADATA: list[dict[str, str]] = [
    {"name": "system", "description": "Health checks, version and system information."},
    {"name": "auth", "description": "Login (local and OIDC), session and API-token management."},
    {"name": "printer", "description": "Default printer status, settings and loaded paper."},
    {"name": "jobs", "description": "Print jobs: upload, hold queue, release, reprint, history and share-target."},
    {"name": "scanner", "description": "Scanning: scans, batches, profiles, collation, history and back-side passes."},
    {"name": "copy", "description": "Copy (scan then print) operations."},
    {"name": "smb", "description": "SMB network share browsing, reading and writing."},
    {"name": "email", "description": "Sending scans by email and receiving email-to-print via webhook."},
    {"name": "cloud", "description": "Cloud storage providers (Google Drive, Dropbox, OneDrive, Nextcloud)."},
    {"name": "settings", "description": "UI-managed application settings (admin)."},
    {"name": "printers", "description": "Printer registry: discovery, probing and CUPS queue management (admin)."},
    {"name": "scanners", "description": "Scanner registry: discovery, probing and registration (admin)."},
    {"name": "admin", "description": "Users, statistics, audit log, backup and restore (admin)."},
    {"name": "webdav", "description": "WebDAV (Nextcloud and compatible) browsing and transfer."},
    {"name": "webhooks", "description": "Outgoing webhook configuration (admin)."},
    {"name": "escl", "description": "eSCL network scanner protocol at the server root (LAN clients only)."},
]


class FieldError(BaseModel):
    loc: list[str | int] = Field(description="Location of the invalid field, e.g. ['body', 'copies'].")
    msg: str = Field(description="Human-readable validation message.")
    type: str = Field(description="Machine-readable validation error type.")


class ErrorResponse(BaseModel):
    detail: str = Field(description="Message written for end users.")
    code: str = Field(description="Stable snake_case error code.")
    request_id: str | None = Field(default=None, description="Correlation id, also in X-Request-ID.")
    errors: list[FieldError] | None = Field(default=None, description="Per-field errors (validation only).")


def _err(description: str, headers: dict[str, Any] | None = None) -> dict[str, Any]:
    r: dict[str, Any] = {"model": ErrorResponse, "description": description}
    if headers:
        r["headers"] = headers
    return r


AUTH_ERRORS: dict[int | str, dict[str, Any]] = {
    401: _err("Not authenticated."),
    403: _err("Authenticated but lacking the required role or permission."),
}
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: _err("Resource not found.")}
CONFLICT: dict[int | str, dict[str, Any]] = {409: _err("Conflicts with the current state of the resource.")}
VALIDATION: dict[int | str, dict[str, Any]] = {422: _err("Request validation failed.")}
RATE_LIMITED: dict[int | str, dict[str, Any]] = {
    429: _err(
        "Too many requests.",
        {"Retry-After": {"description": "Seconds to wait before retrying.", "schema": {"type": "integer"}}},
    )
}
SERVER: dict[int | str, dict[str, Any]] = {500: _err("Unexpected server error.")}


def generate_unique_id(route: APIRoute) -> str:
    """operationId = ``{first tag}_{handler name}`` (handler name alone if untagged)."""
    return f"{route.tags[0]}_{route.name}" if route.tags else route.name
