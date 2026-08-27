"""API suite for `POST /api/email/receive` (F55/F25/F71/F57).

Goes through the ASGI app end-to-end, like test_api_jobs.py. The endpoint
authenticates via a shared secret token (not OIDC), so every test uses the
plain unauthenticated `client` fixture and seeds the webhook secret as a
real (Fernet-encrypted) AppConfig row, mirroring what
`POST /api/email/webhook-secret` writes.

``job_created``/``dispatch_webhook`` are captured the same way
test_api_jobs.py does: `_create_print_job_from_upload` lives in
`app.routers.jobs`, so that's where they're patched, even though these
requests are routed through `app.routers.email`.
"""
import io

from app.models import AppConfig, PrintJob
from app.routers import jobs as jobs_router
from app.services import settings_cache
from app.services.crypto import encrypt_value

_MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n"
_SECRET = "whsec_email_test_123"


async def _seed_setting(db, key: str, value: str) -> None:
    db.add(AppConfig(key=key, value=value))
    await db.commit()
    settings_cache.invalidate_all()


async def _seed_webhook_secret(db, secret: str = _SECRET) -> None:
    await _seed_setting(db, "email_webhook_secret", encrypt_value(secret))


async def _seed_upload_dir(db, tmp_path) -> None:
    await _seed_setting(db, "upload_dir", str(tmp_path))


def _email_files(*attachments: tuple[str, bytes]) -> list[tuple[str, tuple]]:
    """Build the multipart `files` part list for `files: list[UploadFile]`
    (FastAPI/Starlette expects every part to repeat the same field name)."""
    return [
        ("files", (name, io.BytesIO(data), "application/pdf")) for name, data in attachments
    ]


def _capture_webhooks(monkeypatch) -> list:
    events: list = []

    async def fake_dispatch(_db, event, data):
        events.append((event, data))

    monkeypatch.setattr(jobs_router, "dispatch_webhook", fake_dispatch)
    return events


def _capture_broadcasts(monkeypatch) -> list:
    broadcasts: list = []

    async def fake_broadcast(channel, message):
        broadcasts.append((channel, message))

    monkeypatch.setattr(jobs_router.ws_manager, "broadcast", fake_broadcast)
    return broadcasts


# --------------------------------------------------------------------------- #
# Happy path — F55: broadcast + print.held now fire
# --------------------------------------------------------------------------- #
async def test_receive_creates_job_broadcasts_and_dispatches_print_held(
    db, client, tmp_path, monkeypatch
):
    await _seed_webhook_secret(db)
    await _seed_upload_dir(db, tmp_path)
    broadcasts = _capture_broadcasts(monkeypatch)
    events = _capture_webhooks(monkeypatch)

    resp = await client.post(
        "/api/email/receive",
        files=_email_files(("invoice.pdf", _MINIMAL_PDF)),
        data={"token": _SECRET, "subject": "Fwd: Invoice"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["total"] == 1
    job_id = body["jobs"][0]["id"]

    created = [m for ch, m in broadcasts if ch == "jobs" and m["type"] == "job_created"]
    assert len(created) == 1
    assert created[0]["data"]["id"] == job_id

    held = [d for e, d in events if e == "print.held"]
    assert len(held) == 1
    assert held[0]["id"] == job_id
    assert held[0]["source_type"] == "email"
    assert held[0]["user_id"] is None  # F55/F25/F71: no authenticated user

    uploaded = [d for e, d in events if e == "print.upload"]
    assert len(uploaded) == 1


async def test_receive_job_row_has_no_user_and_is_held(db, client, tmp_path, monkeypatch):
    await _seed_webhook_secret(db)
    await _seed_upload_dir(db, tmp_path)
    _capture_broadcasts(monkeypatch)
    _capture_webhooks(monkeypatch)

    resp = await client.post(
        "/api/email/receive",
        files=_email_files(("doc.pdf", _MINIMAL_PDF)),
        data={"token": _SECRET, "subject": "A scan"},
    )
    assert resp.status_code == 201
    job_id = resp.json()["jobs"][0]["id"]

    job = await db.get(PrintJob, job_id)
    assert job.user_id is None
    assert job.status == "held"
    assert job.source_type == "email"
    assert job.release_pin is None  # auto_pin=False: never auto-generated


# --------------------------------------------------------------------------- #
# F71 — long subject is truncated, never crashes the insert
# --------------------------------------------------------------------------- #
async def test_receive_truncates_long_subject_to_title_limit(db, client, tmp_path, monkeypatch):
    await _seed_webhook_secret(db)
    await _seed_upload_dir(db, tmp_path)
    _capture_broadcasts(monkeypatch)
    _capture_webhooks(monkeypatch)

    long_subject = "A" * 400  # PrintJob.title is String(255)
    resp = await client.post(
        "/api/email/receive",
        files=_email_files(("doc.pdf", _MINIMAL_PDF)),
        data={"token": _SECRET, "subject": long_subject},
    )

    assert resp.status_code == 201
    title = resp.json()["jobs"][0]["title"]
    assert len(title) == 255
    assert title == "A" * 255


# --------------------------------------------------------------------------- #
# F25 — oversize attachment 413s instead of buffering unbounded in RAM
# --------------------------------------------------------------------------- #
async def test_receive_oversize_attachment_is_413(db, client, tmp_path):
    await _seed_webhook_secret(db)
    await _seed_upload_dir(db, tmp_path)
    await _seed_setting(db, "max_upload_size_mb", "1")

    oversized = b"0" * (2 * 1024 * 1024)  # 2 MiB > 1 MiB cap
    resp = await client.post(
        "/api/email/receive",
        files=_email_files(("big.pdf", oversized)),
        data={"token": _SECRET, "subject": "Big attachment"},
    )

    assert resp.status_code == 413
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# Auth / configuration
# --------------------------------------------------------------------------- #
async def test_receive_without_configured_secret_is_503(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)

    resp = await client.post(
        "/api/email/receive",
        files=_email_files(("doc.pdf", _MINIMAL_PDF)),
        data={"token": "anything"},
    )
    assert resp.status_code == 503


async def test_receive_with_wrong_token_is_403(db, client, tmp_path):
    await _seed_webhook_secret(db)
    await _seed_upload_dir(db, tmp_path)

    resp = await client.post(
        "/api/email/receive",
        files=_email_files(("doc.pdf", _MINIMAL_PDF)),
        data={"token": "wrong-token"},
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# F57 — rate limit is keyed by the validated token, not the client IP
# --------------------------------------------------------------------------- #
async def test_receive_rate_limit_is_not_consumed_by_invalid_token_attempts(
    db, client, tmp_path, monkeypatch
):
    """Regression: an attacker hammering /receive with a bogus token must not
    be able to burn through the legitimate sender's rate-limit budget --
    each bad-token request should 403 without touching the (token-keyed)
    bucket at all."""
    await _seed_webhook_secret(db)
    await _seed_upload_dir(db, tmp_path)
    await _seed_setting(db, "email_webhook_rate_limit", "2")
    _capture_broadcasts(monkeypatch)
    _capture_webhooks(monkeypatch)

    for _ in range(5):
        resp = await client.post(
            "/api/email/receive",
            files=_email_files(("doc.pdf", _MINIMAL_PDF)),
            data={"token": "not-the-real-token"},
        )
        assert resp.status_code == 403

    # The real sender's quota (2/min) must still be fully available.
    for _ in range(2):
        resp = await client.post(
            "/api/email/receive",
            files=_email_files(("doc.pdf", _MINIMAL_PDF)),
            data={"token": _SECRET},
        )
        assert resp.status_code == 201

    resp = await client.post(
        "/api/email/receive",
        files=_email_files(("doc.pdf", _MINIMAL_PDF)),
        data={"token": _SECRET},
    )
    assert resp.status_code == 429


# --------------------------------------------------------------------------- #
# Non-printable attachments are silently skipped, matching prior behaviour
# --------------------------------------------------------------------------- #
async def test_receive_skips_non_printable_attachment(db, client, tmp_path, monkeypatch):
    await _seed_webhook_secret(db)
    await _seed_upload_dir(db, tmp_path)
    _capture_broadcasts(monkeypatch)
    events = _capture_webhooks(monkeypatch)

    resp = await client.post(
        "/api/email/receive",
        files=[("files", ("archive.zip", io.BytesIO(b"PK\x03\x04"), "application/zip"))],
        data={"token": _SECRET},
    )

    assert resp.status_code == 201
    assert resp.json()["total"] == 0
    assert events == []
