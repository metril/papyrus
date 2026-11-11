"""Pure-ASGI middleware mapping the legacy ``/api/*`` paths onto ``/api/v1/*``.

Old paths are not registered as routes (so they stay out of the OpenAPI
schema); the path is rewritten before routing, so the target route's auth
dependencies still apply. Applies to HTTP and WebSocket scopes.

``TABLE`` is ordered; each line is ``METHOD old [-> new] [!]``:
  * ``new`` omitted => same path under ``/api/v1``;
  * ``{name}`` is a single path segment, carried over by name;
  * trailing ``!`` => permanent alias (externally registered URL: no
    Deprecation/Sunset headers, only the successor ``Link``).
Method ``WS`` is a WebSocket path.
"""
import re
from urllib.parse import quote

from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SUNSET = "Fri, 31 Dec 2027 23:59:59 GMT"

_RAW = """
GET /system/health
WS /system/ws/jobs -> /ws/jobs
WS /system/ws/scans -> /ws/scans
WS /system/ws/printers -> /ws/printers
GET /auth/providers
POST /auth/local-login
GET /auth/login !
GET /auth/callback !
POST /auth/device/code
GET /auth/device/{user_code}
POST /auth/device/approve
POST /auth/device/token
POST /auth/logout
GET /auth/me
GET /auth/tokens
POST /auth/tokens
DELETE /auth/tokens/{token_id}
GET /printer/status -> /printers/default/status
GET /printer/settings -> /printers/default/settings
GET /printer/paper -> /printers/default/paper
POST /share-target -> /jobs/share-target !
POST /jobs/internal/ingest !
POST /jobs/upload
POST /jobs/bulk-delete
GET /jobs
GET /jobs/{job_id}
DELETE /jobs/{job_id}
GET /jobs/{job_id}/download
GET /jobs/{job_id}/preview
GET /jobs/{job_id}/thumbnail
PATCH /jobs/{job_id}/printer
POST /jobs/{job_id}/release
POST /jobs/{job_id}/cancel
POST /jobs/{job_id}/reprint
GET /scanner/status -> /scanners/default/status
GET /scanner/options -> /scanners/default/options
POST /scanner/scan -> /scans
POST /scanner/scan/batch -> /scans/batch
GET /scanner/scans -> /scans
POST /scanner/scans/bulk-delete -> /scans/bulk-delete
POST /scanner/collate -> /scans/collate
GET /scanner/scans/{scan_id}/download -> /scans/{scan_id}/download
GET /scanner/scans/{scan_id}/thumbnail -> /scans/{scan_id}/thumbnail
POST /scanner/scans/{scan_id}/email -> /scans/{scan_id}/email
POST /scanner/scans/{scan_id}/cloud -> /scans/{scan_id}/cloud
POST /scanner/scans/{scan_id}/paperless -> /scans/{scan_id}/paperless
POST /scanner/scans/{scan_id}/ocr -> /scans/{scan_id}/ocr
POST /scanner/scans/{scan_id}/enhance -> /scans/{scan_id}/enhance
POST /scanner/scans/{scan_id}/backs -> /scans/{scan_id}/backs
POST /scanner/scans/{scan_id}/smb -> /scans/{scan_id}/smb
DELETE /scanner/scans/{scan_id} -> /scans/{scan_id}
GET /scanner/profiles -> /scan-profiles
POST /scanner/profiles -> /scan-profiles
PUT /scanner/profiles/{profile_id} -> /scan-profiles/{profile_id}
DELETE /scanner/profiles/{profile_id} -> /scan-profiles/{profile_id}
WS /scanner/ws/scan/{scan_id} -> /ws/scans/{scan_id}
POST /copy
GET /smb/shares
POST /smb/shares
DELETE /smb/shares/{share_id}
GET /smb/browse/{share_id}
GET /smb/download/{share_id}
GET /email/config
PUT /email/config
POST /email/test
POST /email/receive !
GET /email/webhook-info
POST /email/webhook-secret
GET /cloud/providers
DELETE /cloud/disconnect/{provider_id} -> /cloud/providers/{provider_id}
GET /cloud/authorize/{provider}
GET /cloud/callback/{provider} !
GET /cloud/files/{provider_id}
GET /cloud/download/{provider_id}
GET /settings
PUT /settings
GET /printers
POST /printers
GET /printers/discover
GET /printers/probe
PATCH /printers/{printer_id}
DELETE /printers/{printer_id}
POST /printers/{printer_id}/default
POST /printers/{printer_id}/resume
POST /printers/{printer_id}/test-page
POST /printers/{printer_id}/refresh-info
GET /scanners
POST /scanners
GET /scanners/probe
GET /scanners/diagnostics
GET /scanners/discover
POST /scanners/register-brscan4
GET /scanners/{scanner_id}/test
PATCH /scanners/{scanner_id}
DELETE /scanners/{scanner_id}
POST /scanners/{scanner_id}/default
GET /admin/audit
GET /admin/stats
GET /admin/backup
POST /admin/restore
POST /admin/retention
GET /admin/users
PATCH /admin/users/{user_id}
DELETE /admin/users/{user_id}
POST /webdav/connect
GET /webdav/{provider_id}/files
POST /webdav/{provider_id}/upload
GET /webhooks/events
GET /webhooks
POST /webhooks
PUT /webhooks/{webhook_id}
DELETE /webhooks/{webhook_id}
"""

_PARAM = re.compile(r"\{(\w+)\}")


def _build() -> list[tuple[str, re.Pattern[str], str, str | None]]:
    out = []
    for line in _RAW.strip().splitlines():
        permanent = line.endswith("!")
        parts = line.rstrip("!").strip().split(" ", 1)
        method, rest = parts[0], parts[1]
        old, _, new = (p.strip() for p in rest.partition("->"))
        new = new or old
        rx = "/api" + _PARAM.sub(lambda m: f"(?P<{m.group(1)}>[^/]+)", old)
        out.append((method, re.compile(rx), "/api/v1" + new, None if permanent else SUNSET))
    return out


TABLE = _build()


def route_exists(app: ASGIApp, scope: Scope) -> bool:
    """True when ``app``'s router has a full match for this (rewritten) scope."""
    # The SPA catch-all would match everything; it is not an API route.
    return any(
        r.matches(scope)[0] == Match.FULL
        for r in app.routes  # type: ignore[attr-defined]
        if getattr(r, "path", None) != "/{path:path}"
    )


def _method(scope: Scope) -> str:
    return "WS" if scope["type"] == "websocket" else scope.get("method", "GET")


class LegacyAliasMiddleware:
    def __init__(self, app: ASGIApp, route_exists=None) -> None:
        self.app = app
        self._route_exists = route_exists

    def _resolve(self, scope: Scope) -> tuple[str, str | None] | None:
        path: str = scope["path"]
        if not path.startswith("/api/") or path.startswith("/api/v1/") or path == "/api/v1":
            return None
        method = _method(scope)
        loose = None
        for m, rx, tpl, sunset in TABLE:
            hit = rx.fullmatch(path.rstrip("/") if len(path) > 5 else path)
            if not hit:
                continue
            new = tpl.format(**hit.groupdict())
            if m == method:
                return new, sunset
            if loose is None and (m == "WS") == (method == "WS"):
                loose = (new, sunset)  # right path, wrong method -> real 405
        if loose:
            return loose
        # Fallback: generic /api/<rest> -> /api/v1/<rest>, only if it exists.
        new = "/api/v1/" + path[len("/api/"):]
        if self._route_exists is not None:
            probe = {**scope, "path": new}
            if self._route_exists(probe):
                return new, SUNSET
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        res = self._resolve(scope)
        if res is None:
            await self.app(scope, receive, send)
            return
        new, sunset = res
        scope = {**scope, "path": new, "raw_path": quote(new, safe="/").encode("ascii")}
        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                if sunset:
                    headers.append((b"deprecation", b"true"))
                    headers.append((b"sunset", sunset.encode()))
                safe_new = quote(new, safe="/")  # no CR/LF/control chars in a header
                headers.append((b"link", f'<{safe_new}>; rel="successor-version"'.encode()))
            await send(message)

        await self.app(scope, receive, send_wrapper)
