"""Shared OpenAPI documentation helpers: tag metadata, error models, reusable
``responses=`` constants and the operationId generator.

Router idiom (use these, don't hand-roll response dicts)::

    @router.get("/x", summary="...", responses=std_responses(AUTH_ERRORS, NOT_FOUND))
    @router.get("/f", summary="...", response_class=Response,
                responses=file_response(["application/pdf"], "The scan file.")
                | std_responses(AUTH_ERRORS, NOT_FOUND))

``std_responses`` merges the constant groups (AUTH_ERRORS, NOT_FOUND, CONFLICT,
VALIDATION, RATE_LIMITED, SERVER); ``file_response`` documents a binary 200.
422 always renders as ``ErrorResponse`` (see ``install_openapi``). Paginated
lists return ``schemas.Page[T]`` with ``limit`` (1-200) / ``offset`` params.
"""
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

TAGS_METADATA: list[dict[str, str]] = [
    {"name": "system", "description": "Health checks, version and system information."},
    {"name": "auth", "description": "Login (local and OIDC), session and API-token management."},
    {"name": "printer", "description": "Default printer status, settings and loaded paper."},
    {"name": "jobs", "description": "Print jobs: upload, hold queue, release, reprint, history and "
        "share-target."},
    {"name": "scanner", "description": "Scanning: scans, batches, profiles, collation, history and "
        "back-side passes."},
    {"name": "copy", "description": "Copy (scan then print) operations."},
    {"name": "smb", "description": "SMB network share browsing, reading and writing."},
    {"name": "email", "description": "Sending scans by email and receiving email-to-print via "
        "webhook."},
    {"name": "cloud", "description": "Cloud storage providers (Google Drive, Dropbox, OneDrive, "
        "Nextcloud)."},
    {"name": "settings", "description": "UI-managed application settings (admin)."},
    {"name": "printers", "description": "Printer registry: discovery, probing and CUPS queue "
        "management (admin)."},
    {"name": "scanners", "description": "Scanner registry: discovery, probing and registration "
        "(admin)."},
    {"name": "admin", "description": "Users, statistics, audit log, backup and restore (admin)."},
    {"name": "webdav", "description": "WebDAV (Nextcloud and compatible) browsing and transfer."},
    {"name": "webhooks", "description": "Outgoing webhook configuration (admin)."},
]


class FieldError(BaseModel):
    loc: list[str | int] = Field(description="Location of the invalid field, e.g. ['body', "
        "'copies'].")
    msg: str = Field(description="Human-readable validation message.")
    type: str = Field(description="Machine-readable validation error type.")


class ErrorResponse(BaseModel):
    detail: str = Field(description="Message written for end users.")
    code: str = Field(description="Stable snake_case error code.")
    request_id: str | None = Field(
        default=None, description="Correlation id, also in X-Request-ID."
    )
    errors: list[FieldError] | None = Field(default=None, description="Per-field errors "
        "(validation only).")


def error_response(description: str, headers: dict[str, Any] | None = None) -> dict[str, Any]:
    r: dict[str, Any] = {"model": ErrorResponse, "description": description}
    if headers:
        r["headers"] = headers
    return r


def error_group(code: int, description: str) -> dict[int | str, dict[str, Any]]:
    """A one-entry ``responses=`` dict documenting ``code`` as an ErrorResponse."""
    return {code: error_response(description)}


AUTH_ERRORS: dict[int | str, dict[str, Any]] = {
    401: error_response("Not authenticated."),
    403: error_response("Authenticated but lacking the required role or permission."),
}
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: error_response("Resource not found.")}
CONFLICT: dict[int | str, dict[str, Any]] = {409: error_response("Conflicts with the current state "
    "of the resource.")}
VALIDATION: dict[int | str, dict[str, Any]] = {422: error_response("Request validation failed.")}
RATE_LIMITED: dict[int | str, dict[str, Any]] = {
    429: error_response(
        "Too many requests.",
        {
            "Retry-After": {
                "description": "Seconds to wait before retrying.",
                "schema": {"type": "integer"},
            }
        },
    )
}
SERVER: dict[int | str, dict[str, Any]] = {500: error_response("Unexpected server error.")}
BAD_REQUEST: dict[int | str, dict[str, Any]] = {400: error_response("Invalid request or state.")}
TOO_LARGE: dict[int | str, dict[str, Any]] = {413: error_response("Upload exceeds "
    "max_upload_size_mb.")}
BAD_GATEWAY: dict[int | str, dict[str, Any]] = {502: error_response("An external service failed.")}
UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    503: error_response("Service or device unavailable.")
}


def generate_unique_id(route: APIRoute) -> str:
    """operationId = ``{first tag}_{handler name}`` (handler name alone if untagged)."""
    return f"{route.tags[0]}_{route.name}" if route.tags else route.name


def std_responses(*groups: dict[int | str, dict[str, Any]]) -> dict[int | str, dict[str, Any]]:
    """Merge response-constant groups into one ``responses=`` dict."""
    merged: dict[int | str, dict[str, Any]] = {}
    for g in groups:
        merged.update(g)
    return merged


def file_response(media_types: list[str], description: str) -> dict[int | str, dict[str, Any]]:
    """Document a binary 200 response with the given media types."""
    return {
        200: {
            "description": description,
            "content": {m: {"schema": {"type": "string", "format": "binary"}} for m in media_types},
        }
    }


_VALIDATION_REF = "#/components/schemas/HTTPValidationError"
_ERROR_REF = {"$ref": "#/components/schemas/ErrorResponse"}


def _swap_validation_refs(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("$ref") == _VALIDATION_REF:
            node["$ref"] = _ERROR_REF["$ref"]
        for v in node.values():
            _swap_validation_refs(v)
    elif isinstance(node, list):
        for v in node:
            _swap_validation_refs(v)


def _register_ws_schemas(comps: dict[str, Any]) -> None:
    """Add the WebSocket message models to components (OpenAPI has no WS messages)."""
    from app.schemas.events import WS_MESSAGE_MODELS

    for models in WS_MESSAGE_MODELS.values():
        for model in models:
            js = model.model_json_schema(ref_template="#/components/schemas/{model}")
            for name, sub in js.pop("$defs", {}).items():
                comps.setdefault(name, sub)
            comps[model.__name__] = js


def _register_webhooks(app: FastAPI) -> None:
    from app.schemas.events import WEBHOOK_MODELS

    for event, model in WEBHOOK_MODELS.items():
        def handler(payload: model) -> None:  # type: ignore[valid-type]
            """Delivered as an HTTP POST; verify X-Papyrus-Signature (see docs/api/webhooks.md)."""

        handler.__name__ = "webhook_" + event.replace(".", "_")
        app.webhooks.post(
            event,
            summary=event,
            description=(model.__doc__ or "")
            + " Headers: `X-Papyrus-Event`, and `X-Papyrus-Signature` (hex HMAC-SHA256 of the "
            "raw body) when the webhook has a secret.",
        )(handler)


def _add_rate_limited(paths: dict[str, Any]) -> None:
    """Every rate-limited (/api, non-health) operation can answer 429."""
    for path, ops in paths.items():
        if not path.startswith("/api/") or path.endswith("/system/health"):
            continue
        for op in ops.values():
            if isinstance(op, dict) and "responses" in op:
                op["responses"].setdefault("429", {
                    "description": "Too many requests.",
                    "headers": RATE_LIMITED[429]["headers"],
                    "content": {"application/json": {"schema": _ERROR_REF}},
                })


def install_openapi(app: FastAPI) -> None:
    """Replace FastAPI's default 422 body (HTTPValidationError) with ErrorResponse; publish
    webhook and WebSocket message models."""
    _register_webhooks(app)

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
            servers=app.servers,
            contact=app.contact,
            webhooks=app.webhooks.routes,
        )
        _swap_validation_refs(schema["paths"])
        _add_rate_limited(schema["paths"])
        comps = schema.setdefault("components", {}).setdefault("schemas", {})
        _register_ws_schemas(comps)
        for name in ("HTTPValidationError", "ValidationError"):
            comps.pop(name, None)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
