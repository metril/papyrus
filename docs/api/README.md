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

Reserved. Rate-limited responses will be `429` with a `Retry-After` header (seconds) and the usual
`ErrorResponse` body; the OpenAPI `RATE_LIMITED` response documents this shape. Enforcement
arrives in a later phase. Today only login attempts and release-PIN guesses are throttled
(in-memory lockout).
