# Papyrus — Future Work

## NFC Kiosk Login (HomeKey + Home Assistant)
**Priority:** Medium
**Status:** Planned

Use ESP32 HomeKey + Home Assistant to auto-login users on a kiosk near the printer. User taps iPhone/Watch on ESP32 → HA identifies person → HA calls Papyrus API → kiosk auto-logs in.

**Implementation:**
- Backend: 3 new endpoints (`nfc-login`, `nfc-pending`, `nfc-claim`) in `routers/auth.py`
- Frontend: Poll for NFC login on the LoginScreen in `AppShell.tsx`
- In-memory pending session (60s expiry, one-time token)
- `nfc-login` requires admin API token (only HA can trigger)

**Details:** See `.claude/plans/gentle-knitting-cherny.md` for full plan.

## Phase 3+ backlog (from Phase 3 final review)
- [ ] Probe URI is pathless (`ipp://ip:631`) when IPP answers on resource "" — normalize or fall back to /ipp
- [ ] PrinterDiscovery mount effect double-scans under React StrictMode in dev (harmless, noisy)
- [ ] Probe worst-case latency ~20s (2×3s TCP + 3×5s IPP) — lower per-resource timeout for the interactive path

## Phase 4+ backlog (from Phase 3 final review)
- Replace the as-unknown-as cast round-trip in JobQueue/UploadForm with typed upsertJobInCache/removeJobFromCache bridge helpers
- Widen getPrinterStatus() return type (markers/state_reasons) and drop PrinterStatus.tsx cast — `/printer/status` (schemas.PrinterStatus) still doesn't send those fields today
- Add MutationCache-path assertion to queryClient.test.tsx

## Post-Phase-6 backlog (from P6 review)
- SupplyMeter reads only the default printer's status; extend to all printers when multi-printer becomes real

## Deferred from the 2026-08-26 audit

Four findings from the audit remediation were confirmed as real but need a deployment decision rather than a code change, so they were left unimplemented:

- **F94 — root + `CAP_SYS_ADMIN`**: the container runs as root with `CAP_SYS_ADMIN`; cupsd/avahi need root today. Recommendation: try `cap_drop: [ALL]` + a minimal `cap_add` against a real container run and see whether `SYS_ADMIN` can actually be dropped before committing to a change.
- **F95 — API on `0.0.0.0:8080` plain HTTP**: splitting the eSCL/IPP surface onto its own port is a deployment change, not a code fix. Mitigated today by the eSCL LAN-only restriction and secure session cookies. Recommendation: leave as-is.
- **F100 — no backend lockfile**: `backend/pyproject.toml` has no pinned lockfile. Recommendation: `uv pip compile pyproject.toml -o backend/requirements.lock` and install from it in the Dockerfile.
- **F153 — hard-coded eSCL UUID per install**: only matters if multiple Papyrus instances share one LAN. Recommendation: leave as-is.

## Review minors deferred from the audit remediation

Lower-severity findings confirmed during the 2026-08-26 audit remediation but not worth fixing immediately — mostly latent races, missing edge-case handling, and test-coverage gaps. Grouped by area; see the remediation's review ledger for full detail if one of these needs picking up.

### Backend perimeter / auth
- Health-check cache: no test for negative-result caching, no autouse reset fixture (latent cross-test state), timestamp sampled before probing shortens the effective TTL under a slow probe, and the module-level `asyncio.Lock` binds to the first contending event loop
- No test that `lifespan` itself refuses to start on a default session secret (only the validation helper is tested)
- The CUPS leg of the health probe has no timeout and now runs inside the health-check lock
- `system.py`'s `shutil.disk_usage` call runs inline on the async handler (pre-existing, unauthenticated path)
- Login/PIN throttle is a fixed window anchored at the first failure (not sliding); its sweep only bounds stale entries (O(n) every 1000 failures); behind Traefik (no `--proxy-headers`) the login lockout key collapses to per-username since `request.client.host` is always the proxy
- `auth.py` only catches `VerifyMismatchError` — other argon2 exceptions 500 without recording a failure
- Release PIN `.encode()` raises `UnicodeEncodeError` (→ 500) on a lone UTF-16 surrogate instead of a clean 403

### Print pipeline
- `convert_service` cleanup doesn't catch `CancelledError`, so a client disconnect mid-LibreOffice-conversion leaks the temp dir
- `release_job` still throttles on a *missing* PIN (unlike the file/reprint gates) — repeated no-PIN releases by any print user can lock the job's owner out for 5 minutes
- Reprint's PIN gate is checked after the "file no longer available" 400, letting an unauthorized requester distinguish file-gone from PIN-needed; `reprint_job` has no status check, so a held job can be reprinted
- `HistoryRow` still renders the delete control and selection checkbox for a PIN-locked item (backend still enforces the 403/skip)
- `os.makedirs` runs on the event loop in reprint/copy right next to an otherwise-offloaded `copy2`
- `PrintPage` renders "NaN shared files…" for a hand-edited `?share_failed=` query param

### Ingest / CUPS / printers
- Network-ingest `ingest_key` dedupe is TOCTOU — two in-flight requests with the same key can hit the unique index and 500 with an orphaned upload file instead of returning the existing job
- The ingest-token comparison (`secrets.compare_digest`) can raise `TypeError` on a non-ASCII header, 500ing instead of 403ing
- `ingest_key` (`boot_id:printer:job_id`) can collide if cupsd's job counter resets without a reboot, silently re-serving a stale job
- `_validate_probe_ip` is duplicated verbatim in `printers.py`/`scanners.py` and misses IPv4-mapped IPv6 addresses, leaving a residual localhost port-oracle path
- `models.py`'s `ingest_key` unique constraint is unnamed, unlike the migration's named index — a future autogenerate would show drift
- Default-printer/scanner promotion-on-delete isn't atomic with the delete commit; a losing concurrent "set default" request hits an unhandled `IntegrityError` 500
- `add_physical_printer` can orphan the hold queue in CUPS if only the second `lpadmin` call fails
- `resume_printer` re-enables the hold queue while status reads the `_release` queue, so Resume may not clear a stopped release queue

### Scanning / eSCL
- A poisoned DB session (an earlier failed `db.execute`) can make the terminal scan commit raise `PendingRollbackError`, flipping a completed+broadcast scan to failed and 500ing
- A client-supplied `scan_id` that collides with the unique column raises an uncaught `IntegrityError` 500 instead of a clean 409
- An eSCL `DELETE`-cancelled job's row stays `scanning` until the stale-job sweep runs (a phantom in-progress scan in the UI); a stranded non-terminal job with no `DELETE` ever sent gates admission (503) until restart
- `crypto.decrypt_value_lenient` can't tell "Fernet ciphertext under a rotated key" from plaintext, and logs an unsalted dedupe key derived from the secret
- `scanners.py`'s `is_encrypted(existing)` assumes a string — a stored `None` secret followed by a `"*set*"` PATCH raises `AttributeError` (500)
- `file_locks.lock_for` isn't keyed on a normalized (realpath) path
- A malformed `Content-Length` header on a scanner probe raises an uncaught `int()` error
- XFF entries with brackets/zone-ids/`host:port` fail IP parsing and 403 (fail-closed; Traefik never emits those forms in practice)

### WebSockets
- `Origin`/`Host` comparison in the WS same-origin check is case-sensitive and scheme-blind; a proxy that rewrites `Host` without `Origin` would break every socket with an unexplained 1008
- A malformed session `user_id` raises inside `authenticate_websocket` despite its "never raises" docstring
- Reconnect backoff has no jitter — clients retry in lockstep after an API restart
- On a URL swap, the superseded socket's `onclose` can leave the `connected` store flag stale (briefly lies)
- The frontend auth probe (`GET /auth/me` after 3 failed reconnects) has no cross-hook dedupe — a real auth failure fires one probe per channel
- Session-cookie WS auth is only unit-tested against a fake WebSocket, not a real `SessionMiddleware`-backed handshake

### Integrations
- `ftp_service.test_sftp` still connects with no host-key verification (currently unused by any caller)
- Webhook background dispatch tasks aren't drained on shutdown (`wait_for_pending_dispatches()` exists but nothing calls it) — last-moment webhooks can be dropped
- Inbound email commits per attachment, so a mid-batch 413 leaves earlier attachments already committed while the response is 413 (a forwarder retry would duplicate them); zero-byte attachments are no longer skipped
- `email_service.EmailError` still leaks raw SMTP relay text into the client-visible detail
- `net_guard.py` exists but the older `_validate_probe_ip` copies in `printers.py`/`scanners.py` haven't been consolidated onto it
- A padding-only pinned SFTP fingerprint normalizes to empty and silently falls through to accept-and-log instead of refusing
- `ftp_service`'s `hmac.compare_digest` on a non-ASCII pinned fingerprint raises `TypeError` (generic 500)

### Settings / alerts / retention
- `cleanup_old_audit_log` deletes expired rows one at a time instead of a single bulk `DELETE`
- `audit_retention_days` has no Settings UI field yet (env/API-only); "0 = never prune" isn't documented anywhere visible to an admin
- Several near-identical "collect → commit → cleanup file" blocks (across jobs/scans delete paths) could collapse into one shared helper
- Settings coercion happens inside the write loop, and `restore_settings` still writes raw (uncoerced) values
- A sustained cupsd outage produces no `printer.error` alert at all (log-and-return by design; still visible via printer status)
- A corrupt-but-valid-JSON `alert_state` row can raise `AttributeError` outside the per-printer try/except

### Frontend
- `UploadForm`'s `onDrop` depends on a fresh `toast` object every render, so its `useCallback` never actually memoizes
- The mobile "More" sheet has no Escape handler, focus trap, or `aria-modal`, and stays open across back-navigation
- `registerType: 'prompt'` PWA config has no prompt UI wired up yet (a new service worker just waits for all tabs to close)
- `useJobs()`'s `limit=200` queue fetch still truncates held jobs older than the 200 newest (needs a backend status filter to fully fix); `useScans()` still sends no limit at all
- `/internal/ingest`'s `copies` field is still unbounded (localhost-only CUPS path)
- History delete mutations rely entirely on the WS broadcast to invalidate list queries (no direct invalidation)
- WebDAV entries in Files still have no download/print route (browse-only); percent-encoded folder names aren't normalized against server-returned hrefs

### Docker / CI
- cupsd readiness in `entrypoint.sh` is a blind `sleep 2` rather than a bounded `lpstat -r` poll
- Every tag push runs CI twice (`push` + `workflow_call` from `release.yml`)
- `release.yml`'s `package.json`/`main.py` version extraction is unanchored (unlike the `pyproject.toml` one)
- `config.py`'s `db_url` default still embeds `papyrus:secret@localhost` (non-compose deployments only)
- A `set -e` abort (e.g. a failed Alembic migration) still exits `entrypoint.sh` with cupsd/avahi running, relying on namespace teardown to SIGKILL them
- `.env.example` still prefills a weak default admin password, and `PAPYRUS_ENCRYPTION_KEY` has no `:?` guard in compose
