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
