# Papyrus API

Concepts and conventions. Endpoint details live in the OpenAPI document, not here.

| Resource | Where |
|---|---|
| Interactive docs (Swagger UI) | `/docs` |
| ReDoc | `/redoc` |
| OpenAPI 3.1 JSON | `/openapi.json` |

Related: [websockets.md](websockets.md), [webhooks.md](webhooks.md), [escl.md](escl.md).

## Base URL and versioning

All endpoints are under `/api/v1`. Operation ids are `{tag}_{handler}` and stable.

The legacy unversioned `/api/*` paths still work: the server rewrites them onto `/api/v1/*`
(WebSocket paths included), so authentication and permissions are identical. Legacy responses carry:

- `Deprecation: true` and `Sunset: Fri, 31 Dec 2027 23:59:59 GMT`
- `Link: </api/v1/...>; rel="successor-version"`

Some legacy paths are **permanent aliases** because their URL is registered externally (OIDC
`/api/auth/login` and `/api/auth/callback`, the CUPS ingest endpoint, the PWA share-target).
They get only the `Link` header, no `Deprecation`/`Sunset`. Legacy paths are not in the OpenAPI schema.

## Authentication

Two mechanisms; `Authorization: Bearer` is checked first, then the session cookie.

- **Session cookie** (`session`): set by local login (`POST /auth/local-login`) or OIDC. Full access
  for the user's role.
- **API token**: `Authorization: Bearer <token>`. An invalid or expired token is a 401 (it does not
  fall back to the cookie). Tokens are scoped to a list of permissions.

Permissions (`ALL_PERMISSIONS` in `app/auth/dependencies.py`): `print`, `scan`, `files`, `admin`,
`email`. Session users are not scoped; a token can only do what its permissions allow.

Creating a token: an admin calls `POST /api/v1/auth/tokens` with `name`, `permissions` (at least
one) and optionally `expires_in_days`. The plaintext token is in that response only; Papyrus stores
a hash. Tokens can be listed and revoked (`GET`/`DELETE /auth/tokens`), also from the Settings UI.

### Device sign-in (kiosks)

A kiosk holding a shared API token with the `device` permission can have a user sign it in from
their phone (RFC 8628 style):

1. `POST /auth/device/code` (optional `{client_name}`) returns `device_code`, `user_code`
   (`XXXX-XXXX`), `verification_uri_complete` (render as a QR code), `interval` (5s) and
   `expires_in` (300s).
2. The user opens the URL, signs in and approves on the `/device` page (session only;
   `POST /auth/device/approve {user_code, approve}`).
3. The kiosk polls `POST /auth/device/token {device_code}` (same token that requested it). Errors
   are HTTP 400 whose `code` is `authorization_pending`, `slow_down` (interval grows by 5s),
   `access_denied`, `expired_token` or `invalid_grant`. On success it gets a single-use
   `access_token` owned by the user (permissions print, scan, files, email; 15-minute expiry).
4. Revoke early with `DELETE /auth/tokens/{token_id}`, authenticated by the minted token itself or
   by the kiosk's `device` token. Scans created this way can be listed with `GET /scans?mine=true`.

#### Tap-to-approve and PIN sign-in

Both are off by default (admin: Settings → Kiosks; `kiosk_approve_enabled`, `kiosk_pin_enabled`,
`kiosk_pin_length` 4-12). Kiosk calls need the `device` permission.

- `GET /auth/kiosk-login/config` returns `{approve, pin, pin_length}`; `GET /auth/kiosk-login/users`
  returns `[{id, display_name, has_pin}]` (kiosk service users excluded).
- `POST /auth/device/approve-request {user_id, client_name}` returns `{device_code, match_code,
  expires_in, interval}`; show `match_code` (10-99) on the kiosk and poll `POST /auth/device/token`
  exactly as above. The user's open browser sessions get a prompt (WebSocket `notifications`)
  with three numbers and must tap `match_code`; a wrong pick or Deny gives `access_denied`.
  403 when disabled, 404 unknown user, 429 above 5/minute per token; a new request for the same user
  denies the previous one. Phone side (session only): `GET /auth/device/requests`,
  `POST /auth/device/requests/{id}/approve {match_code}` (wrong number: request denied, 409
  `match_code_mismatch`), `POST /auth/device/requests/{id}/deny`.
- `POST /auth/device/pin {user_id, pin, client_name}` returns the same body as `/auth/device/token`.
  401 `{detail, code: pin_incorrect, remaining}` wrong PIN; 423 `{detail, code: pin_locked,
  retry_after}` (also `Retry-After`) after 5 failures (15 minutes); 403 when disabled, the user
  is unknown or has no PIN (same response either way). 10 failures in a row remove the PIN and
  notify the user (WebSocket `kiosk_pin_cleared`, plus email when SMTP is set).
- Users manage their own PIN with `GET|PUT|DELETE /auth/me/kiosk-pin` (session only; digits only,
  exactly `pin_length`). Changing `kiosk_pin_length` clears every PIN.

### Kiosks and address book

Admin routes manage kiosk identities; each kiosk is a non-login service user with its own token.

- `GET /kiosks` lists `{id, name, token_id, last_seen_at, created_at, updated_at}`;
  `GET|PUT|DELETE /kiosks/{id}` (PUT `{name}` renames; DELETE revokes the token and removes the
  service user).
- `POST /kiosks {name}` returns `{kiosk, token}`; the plaintext `token` (permissions `print`,
  `scan`, `device`, no expiry) is shown only in this response. `POST /kiosks/{id}/rotate-token`
  revokes the old token and returns a new `{kiosk, token}`.
- `last_seen_at` is updated by any authenticated request made with the kiosk's token (at most one
  write per 30 s).
- The address book is personal. `GET /contacts` lists the caller's own `{id, name, email, created_at, updated_at}`;
  `POST /contacts`, `PUT /contacts/{id}` and `DELETE /contacts/{id}` act on them. All four need a session or a
  token with `scan`; another user's contact id returns 404 (no admin override).

## Errors

Every error body is an `ErrorResponse`:

```json
{"detail": "Job not found", "code": "not_found", "request_id": "…", "errors": null}
```

- `detail`: message for end users. `code`: stable snake_case identifier.
- `request_id`: same value as the `X-Request-ID` response header (send your own header to correlate).
- `errors`: per-field list (`loc`, `msg`, `type`) on 422 validation failures only.
- 401 = not authenticated; 403 = authenticated but missing role/permission; 413 = upload larger
  than `max_upload_size_mb`.

## Pagination

List endpoints return a `Page` envelope:

```json
{"items": [], "total": 120, "limit": 50, "offset": 0}
```

Query with `limit` (1-200, default 50) and `offset` (>= 0). `total` ignores limit/offset.

## Rate limits

Every `/api` request counts against a per-client-IP bucket (default 300 requests/minute). A request
with a valid API token also counts against a per-token bucket (default 600/minute); invalid bearer
values only count against the IP. Exempt: `/health`, `/eSCL`, the web app's static files,
WebSockets and the CUPS ingest call from the local host. Over the limit the API answers `429` with a
`Retry-After` header (seconds) and an `ErrorResponse` body (`code: "rate_limited"`); see the
`RATE_LIMITED` response in the OpenAPI schema.

Admins configure this under Settings > API (`rate_limit_enabled`, `rate_limit_token_rpm`,
`rate_limit_ip_rpm`, `trusted_proxies`); there are no environment variables. `X-Forwarded-For`
(rightmost entry) is honoured only when the TCP peer is in `trusted_proxies` (comma-separated
IPs/CIDRs, default `127.0.0.1/32,::1/128,172.16.0.0/12`, i.e. loopback and docker bridges); any other
client, including LAN hosts, is bucketed by its own address. Set it to your reverse proxy's address. Login attempts and release-PIN guesses
additionally use an in-memory lockout.

`/docs`, `/redoc` and `/openapi.json` require a signed-in session or Bearer token unless the admin
enables `public_api_docs` (Settings > API).

## Scan and copy progress / cancel

- `GET /scans/{scan_id}` returns the scan; while `status` is `scanning`, `progress_pct` (0-100) and
  `page_count` (feeder sheets so far) are live. The same overlay is applied in `GET /scans`.
- `POST /scans/{scan_id}/cancel` (`scan` permission, owner/admin, else 404) answers 202. The
  original `POST /scans` (or `/scans/batch`) then fails with 409 `scan_cancelled` and the row ends
  as `cancelled`. A scan not in `scanning` returns 409 `scan_not_running`.
- `POST /copy` accepts an optional `copy_id` (UUID; 409 `copy_id_in_use` if taken) and returns it.
  `GET /copy/{copy_id}` gives `{copy_id, phase, progress_pct, scan_id, job_id}` with `phase` one of
  `scanning`, `printing`, `done`, `failed`, `cancelled` (kept ~10 min after finishing; process
  memory only). `POST /copy/{copy_id}/cancel` answers 202; a finished copy returns 409
  `copy_not_running`. Cancelling while scanning fails `POST /copy` with 409 `scan_cancelled`.
- No WebSocket event is emitted for a cancelled scan; poll the endpoints above.
