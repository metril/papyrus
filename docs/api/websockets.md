# WebSockets

Channels push the **full object** on every change (never a partial payload); clients apply it
incrementally. Clients never send meaningful messages (the server just reads and discards text).

| URL | Permission | Messages |
|---|---|---|
| `/api/v1/ws/jobs` | `print` | `job_created`, `job_updated` (data: `PrintJobResponse`), `job_deleted` (`{id}`) |
| `/api/v1/ws/scans` | `scan` | `scan_completed` (data: `ScanResponse`; also re-sent when a row changes), `scan_deleted` (`{scan_id}`) |
| `/api/v1/ws/printers` | `print` | `printer_status` (printer `id`, `cups_name` plus the status fields) |
| `/api/v1/ws/notifications` | any signed-in identity | per-user only: `kiosk_login_request` (`{id, client_name, choices, expires_at}`), `kiosk_login_resolved` (`{id}`), `kiosk_pin_cleared` (`{message}`) |
| `/api/v1/ws/scans/{scan_id}` | `scan` | `scan_progress` (`{scan_id, progress}`), `scan_completed` (`{scan_id, page_count?}`) |

Each message is `{"type": "...", "data": {...}}`. The schemas are components in `/openapi.json`:
`JobCreatedMessage`, `JobUpdatedMessage`, `JobDeletedMessage`, `ScanCompletedMessage`,
`ScanDeletedMessage`, `PrinterStatusMessage`, `ScanProgressMessage`, `ScanDoneMessage`
(each has a `type` literal; the per-scan `scan_completed` is `ScanDoneMessage`). Release PINs are
never included in job payloads (only `has_pin`).

Printer status is pushed every ~15 s, only when a printer's status changed.

## Authentication

Resolved during the handshake, in this order (same as HTTP):

1. `Authorization: Bearer <token>` header
2. `?token=<token>` query parameter (for browsers, which cannot set handshake headers)
3. `session` cookie

An invalid token is rejected (no fallback to the cookie). Missing identity or missing permission
closes the socket with code `1008` before it is accepted. API-token users need the channel's
permission; session users always pass.

## Origin check

WebSockets are not subject to CORS, so when a browser sends an `Origin` header its host must equal
the request's `Host`; otherwise the handshake is rejected (1008). Clients that send no `Origin`
(curl, scripts, native apps) pass this check and rely on authentication alone.

## Legacy paths

`/api/system/ws/{jobs,scans,printers}` and `/api/scanner/ws/scan/{scan_id}` map to the URLs above.
