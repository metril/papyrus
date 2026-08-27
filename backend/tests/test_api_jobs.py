"""Job lifecycle API suite — upload, PIN handling, oversize rejection,
cancel/delete/bulk-delete, and network job ingest.

Goes through the ASGI app end-to-end (unlike test_cups_service.py's direct
calls). CupsService is faked at the router's own import site
(``app.routers.jobs.CupsService``) — the same class the existing unit tests
fake, just patched where ``jobs.py`` looks it up so the fake takes effect for
requests routed through HTTP.

``upload_dir``/``max_upload_size_mb``/``require_release_pin`` are seeded as
real AppConfig rows (committed via the ``db`` fixture, with
``settings_cache.invalidate_all()`` after each seed) rather than monkeypatched
— the same mechanism test_api_settings.py uses for settings reads/writes.
"""
import asyncio
import io
import os
import shutil

import pytest
from PIL import Image

from app.auth.tokens import hash_token
from app.exceptions import ExternalServiceError
from app.models import APIToken, AppConfig, Printer, PrintJob, User
from app.routers import jobs as jobs_router
from app.services import settings_cache

_MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n"


def _real_pdf_bytes(size: tuple[int, int] = (850, 1100), color=(20, 120, 200)) -> bytes:
    """A syntactically real single-page PDF that ghostscript can render — unlike
    `_MINIMAL_PDF`, which is just enough bytes to pass upload/mime sniffing."""
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PDF")
    return buf.getvalue()


async def _seed_setting(db, key: str, value: str) -> None:
    db.add(AppConfig(key=key, value=value))
    await db.commit()
    settings_cache.invalidate_all()


async def _seed_upload_dir(db, tmp_path) -> None:
    await _seed_setting(db, "upload_dir", str(tmp_path))


async def _seed_default_printer(db, *, cups_name: str = "printer1") -> Printer:
    printer = Printer(
        display_name="Printer 1", cups_name=cups_name, uri="",
        is_default=True, is_network_queue=False,
    )
    db.add(printer)
    await db.commit()
    await db.refresh(printer)
    return printer


async def _make_user_with_token(
    db, name: str, *, role: str = "user", permissions=("print",)
) -> tuple[User, str]:
    """A committed User plus a real Bearer token for it — used for F27's
    cross-user tests, where two different identities must act in the same
    test (dependency_overrides only supports one "current user" at a time)."""
    user = User(
        email=f"{name}@example.com", display_name=name.title(),
        role=role, is_local=True, username=name,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    plaintext = f"pprs_test_{name}"
    db.add(APIToken(
        user_id=user.id, name=f"{name}-token",
        token_hash=hash_token(plaintext), permissions=list(permissions),
    ))
    await db.commit()
    return user, plaintext


def _pdf_file(name: str = "test.pdf", data: bytes = _MINIMAL_PDF) -> dict:
    return {"file": (name, io.BytesIO(data), "application/pdf")}


class _FakeCupsService:
    """Stand-in for CupsService, patched at ``app.routers.jobs.CupsService``.

    Records calls so tests can assert create/release/cancel happened without
    touching pycups.
    """

    last_instance: "_FakeCupsService | None" = None

    def __init__(self, printer_name: str = "") -> None:
        self.printer_name = printer_name
        self.created: list[tuple] = []
        self.released: list[int] = []
        self.cancelled: list[int] = []
        _FakeCupsService.last_instance = self

    async def create_held_job(self, filepath, title, copies=1, duplex=False, media="A4"):
        self.created.append((filepath, title, copies, duplex, media))
        return 777

    async def release_job(self, job_id):
        self.released.append(job_id)

    async def cancel_job(self, job_id):
        self.cancelled.append(job_id)


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
async def test_upload_creates_held_job_with_file_on_disk(db, user_client, tmp_path):
    await _seed_upload_dir(db, tmp_path)

    resp = await user_client.post("/api/jobs/upload", files=_pdf_file())
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "held"
    assert body["filename"] == "test.pdf"
    assert "release_pin" not in body

    on_disk = list(tmp_path.iterdir())
    assert len(on_disk) == 1
    assert on_disk[0].name.endswith("_test.pdf")

    get_resp = await user_client.get(f"/api/jobs/{body['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "held"


async def test_upload_with_non_digit_pin_is_400_with_no_file_left_on_disk(
    db, user_client, tmp_path
):
    """Regression (F115): release_pin used to be validated only by the
    String(10) DB column, so a too-long/non-numeric PIN blew up at commit
    time as a generic 500 *after* save_upload_streaming had already written
    the file — orphaning it on disk with no DB row. The PIN must be rejected
    before any file I/O happens."""
    await _seed_upload_dir(db, tmp_path)

    resp = await user_client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "my-long-passphrase"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "PIN must be 4–10 digits"
    assert list(tmp_path.iterdir()) == []


async def test_upload_with_too_short_pin_is_400(db, user_client, tmp_path):
    await _seed_upload_dir(db, tmp_path)

    resp = await user_client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "123"}
    )
    assert resp.status_code == 400
    assert list(tmp_path.iterdir()) == []


async def test_upload_with_ten_digit_pin_is_accepted(db, user_client, tmp_path):
    await _seed_upload_dir(db, tmp_path)

    resp = await user_client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234567890"}
    )
    assert resp.status_code == 201
    assert resp.json()["release_pin"] == "1234567890"


async def test_upload_with_non_ascii_digit_pin_is_400(db, user_client, tmp_path):
    """Regression: Python's `\\d` is Unicode-aware, so `^\\d{4,10}$` accepted
    non-ASCII digit PINs like the Arabic-Indic "١٢٣٤" — which then made
    every subsequent release attempt 500 (secrets.compare_digest rejects
    non-ASCII input). The PIN pattern must be ASCII-digits-only."""
    await _seed_upload_dir(db, tmp_path)

    resp = await user_client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "١٢٣٤"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "PIN must be 4–10 digits"
    assert list(tmp_path.iterdir()) == []


async def test_upload_with_required_pin_setting_returns_generated_pin(db, user_client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    await _seed_setting(db, "require_release_pin", "true")

    resp = await user_client.post("/api/jobs/upload", files=_pdf_file())
    assert resp.status_code == 201
    body = resp.json()
    assert "release_pin" in body
    assert len(body["release_pin"]) == 4
    assert body["release_pin"].isdigit()
    assert body["has_pin"] is True


# --------------------------------------------------------------------------- #
# Release
# --------------------------------------------------------------------------- #
async def test_release_with_wrong_pin_is_403(db, user_client, tmp_path, monkeypatch):
    await _seed_upload_dir(db, tmp_path)
    monkeypatch.setattr(jobs_router, "CupsService", _FakeCupsService)

    upload_resp = await user_client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"}
    )
    job_id = upload_resp.json()["id"]

    release_resp = await user_client.post(f"/api/jobs/{job_id}/release", json={"pin": "0000"})
    assert release_resp.status_code == 403

    # Rejected release must not have touched CUPS or changed status.
    get_resp = await user_client.get(f"/api/jobs/{job_id}")
    assert get_resp.json()["status"] == "held"


async def test_release_with_non_ascii_pin_is_403_not_500(db, user_client, tmp_path, monkeypatch):
    """Regression: secrets.compare_digest raises TypeError on a non-ASCII
    `str` operand, so a release attempt with e.g. {"pin": "café"} used to
    500 through the catch-all handler instead of counting as a normal wrong
    guess. Must be a curated 403, and must still consume the attempt
    budget (checked via the lockout it contributes to below)."""
    monkeypatch.setattr(jobs_router, "CupsService", _FakeCupsService)
    await _seed_upload_dir(db, tmp_path)

    upload_resp = await user_client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"}
    )
    job_id = upload_resp.json()["id"]

    release_resp = await user_client.post(f"/api/jobs/{job_id}/release", json={"pin": "café"})
    assert release_resp.status_code == 403

    # It must have counted as a failure: 4 more (any) wrong guesses reach the
    # 5-failure cap, and the 6th attempt — even with the correct PIN — 429s.
    for _ in range(4):
        resp = await user_client.post(f"/api/jobs/{job_id}/release", json={"pin": "0000"})
        assert resp.status_code == 403

    locked_resp = await user_client.post(f"/api/jobs/{job_id}/release", json={"pin": "1234"})
    assert locked_resp.status_code == 429


async def test_release_pin_locks_out_after_five_failed_attempts(
    db, user_client, tmp_path, monkeypatch
):
    """Regression (F68): repeated wrong-PIN guesses used to have no attempt
    counter, so a job's 4-digit PIN space (10,000 values) was sweepable.
    After 5 failures the 6th attempt must 429 even with the correct PIN."""
    monkeypatch.setattr(jobs_router, "CupsService", _FakeCupsService)
    await _seed_upload_dir(db, tmp_path)

    upload_resp = await user_client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"}
    )
    job_id = upload_resp.json()["id"]

    for _ in range(5):
        resp = await user_client.post(f"/api/jobs/{job_id}/release", json={"pin": "0000"})
        assert resp.status_code == 403

    # Locked out now, even with the correct PIN.
    locked_resp = await user_client.post(f"/api/jobs/{job_id}/release", json={"pin": "1234"})
    assert locked_resp.status_code == 429

    get_resp = await user_client.get(f"/api/jobs/{job_id}")
    assert get_resp.json()["status"] == "held"


async def test_release_with_correct_pin_prints_job(db, user_client, tmp_path, monkeypatch):
    await _seed_upload_dir(db, tmp_path)
    monkeypatch.setattr(jobs_router, "CupsService", _FakeCupsService)
    await _seed_default_printer(db)

    upload_resp = await user_client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"}
    )
    job_id = upload_resp.json()["id"]

    release_resp = await user_client.post(f"/api/jobs/{job_id}/release", json={"pin": "1234"})
    assert release_resp.status_code == 200
    body = release_resp.json()
    assert body["status"] == "printing"
    assert body["cups_job_id"] == 777

    fake = _FakeCupsService.last_instance
    assert fake is not None
    assert fake.created  # create_held_job was invoked
    assert fake.released == [777]


async def test_release_with_no_printer_id_targets_default_release_queue(
    db, user_client, tmp_path, monkeypatch
):
    """Regression (F9): a job with no printer_id used to fall back to
    get_default_printer_name(), the hold-queue name — releasing into it
    re-enters the CUPS backend script instead of printing. It must target
    the default printer's `<cups_name>_release` queue, same as every other
    release site."""
    await _seed_upload_dir(db, tmp_path)
    monkeypatch.setattr(jobs_router, "CupsService", _FakeCupsService)
    printer = await _seed_default_printer(db, cups_name="lobby")

    upload_resp = await user_client.post("/api/jobs/upload", files=_pdf_file())
    job_id = upload_resp.json()["id"]
    # The job was assigned this default printer at upload time; clear it so
    # release falls through the printer_id-is-None branch under test.
    job_row = await db.get(PrintJob, job_id)
    job_row.printer_id = None
    await db.commit()

    release_resp = await user_client.post(f"/api/jobs/{job_id}/release")
    assert release_resp.status_code == 200

    fake = _FakeCupsService.last_instance
    assert fake is not None
    assert fake.printer_name == f"{printer.cups_name}_release"


async def test_release_with_no_default_printer_configured_fails_cleanly(db, user_client, tmp_path):
    """get_default_release_queue_name raises PrinterUnavailableError when no
    default printer exists at all — release_job's existing catch-all wraps
    it (like any other release failure) into a curated 502, not a silent
    print into an empty queue name and not a raw exception leaking to the
    client or the WS broadcast (F132)."""
    await _seed_upload_dir(db, tmp_path)

    upload_resp = await user_client.post("/api/jobs/upload", files=_pdf_file())
    job_id = upload_resp.json()["id"]

    release_resp = await user_client.post(f"/api/jobs/{job_id}/release")
    assert release_resp.status_code == 502

    # The job is left in a terminal "failed" state, not stuck "held" forever
    # with no recourse — F132's curated message, not the raw exception text.
    get_resp = await user_client.get(f"/api/jobs/{job_id}")
    assert get_resp.json()["status"] == "failed"
    assert get_resp.json()["error_message"] == (
        "Printing failed — check the printer connection and file format."
    )


async def test_concurrent_release_populate_existing_prevents_double_print(
    db, user_client, tmp_path, monkeypatch
):
    """Regression (F28): `.with_for_update()` alone doesn't close the race —
    `job` is already identity-mapped into the session from the plain select
    at the top of release_job, and with `expire_on_commit=False`
    (app/database.py) nothing expires it, so the locked re-select must use
    `populate_existing=True` or SQLAlchemy just returns the same cached
    (stale, pre-lock) instance instead of what the lock actually just read.

    Deterministically pins the first release mid-critical-section — inside a
    fake `create_held_job` that awaits a gate — so it holds the row's FOR
    UPDATE lock, uncommitted, while the second release is started and given
    time to actually block on that lock (not just lose an unlocked race
    before ever reaching it, which the blind `asyncio.gather` version of
    this test couldn't tell apart from the real fix). Releasing the gate
    then lets the winner finish; the loser must get 409, and CUPS must only
    ever have seen one job across every fake instance created."""
    await _seed_upload_dir(db, tmp_path)
    await _seed_default_printer(db)

    release_gate = asyncio.Event()

    class _PausingCupsService:
        instances: list["_PausingCupsService"] = []

        def __init__(self, printer_name: str = "") -> None:
            self.printer_name = printer_name
            self.created: list[tuple] = []
            self.released: list[int] = []
            _PausingCupsService.instances.append(self)

        async def create_held_job(self, filepath, title, copies=1, duplex=False, media="A4"):
            # Blocks the winner here, mid-critical-section — after it has
            # the row lock (from the with_for_update() re-select) but before
            # it commits status="printing" and releases that lock.
            await release_gate.wait()
            self.created.append((filepath, title, copies, duplex, media))
            return 777

        async def release_job(self, job_id):
            self.released.append(job_id)

    monkeypatch.setattr(jobs_router, "CupsService", _PausingCupsService)

    upload_resp = await user_client.post("/api/jobs/upload", files=_pdf_file())
    job_id = upload_resp.json()["id"]

    task_a = asyncio.create_task(user_client.post(f"/api/jobs/{job_id}/release"))
    # Let A run all the way to create_held_job and start waiting on the gate
    # — several real DB round trips, but no contention, so this is generous.
    await asyncio.sleep(0.1)

    task_b = asyncio.create_task(user_client.post(f"/api/jobs/{job_id}/release"))
    # Let B pass its own plain (unlocked) status check and then genuinely
    # block on the row's FOR UPDATE lock behind A at the Postgres level.
    await asyncio.sleep(0.2)

    release_gate.set()
    resp_a, resp_b = await asyncio.gather(task_a, task_b)

    assert resp_a.status_code == 200
    assert resp_b.status_code == 409

    total_created = sum(len(inst.created) for inst in _PausingCupsService.instances)
    total_released = sum(len(inst.released) for inst in _PausingCupsService.instances)
    assert total_created == 1
    assert total_released == 1


async def test_release_of_office_doc_cleans_up_conversion_temp_dir(
    db, user_client, tmp_path, monkeypatch
):
    """F30/F31: convert_to_pdf now writes into a unique per-call temp dir
    rather than deterministically alongside the original; release_job must
    remove that temp dir once CUPS has copied the file into its own spool,
    or every released office-doc job leaks an orphan PDF forever."""
    await _seed_upload_dir(db, tmp_path)
    monkeypatch.setattr(jobs_router, "CupsService", _FakeCupsService)
    await _seed_default_printer(db)

    convert_tmpdir = tmp_path / "convert_fake"
    convert_tmpdir.mkdir()
    converted_pdf = convert_tmpdir / "x.pdf"
    converted_pdf.write_bytes(b"%PDF-fake%")

    async def _fake_convert(input_path, output_dir):
        return str(converted_pdf)

    monkeypatch.setattr(jobs_router, "convert_to_pdf", _fake_convert)

    upload_resp = await user_client.post(
        "/api/jobs/upload",
        files={"file": ("x.docx", io.BytesIO(b"fake docx"),
                         "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    job_id = upload_resp.json()["id"]

    release_resp = await user_client.post(f"/api/jobs/{job_id}/release")
    assert release_resp.status_code == 200

    assert not convert_tmpdir.exists()


# --------------------------------------------------------------------------- #
# Oversize upload
# --------------------------------------------------------------------------- #
async def test_oversize_upload_is_413_with_no_partial_file(db, user_client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    await _seed_setting(db, "max_upload_size_mb", "1")

    oversized = b"0" * (2 * 1024 * 1024)  # 2 MiB > 1 MiB cap
    resp = await user_client.post(
        "/api/jobs/upload",
        files={"file": ("big.pdf", io.BytesIO(oversized), "application/pdf")},
    )
    assert resp.status_code == 413
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# Cancel / delete / bulk-delete
# --------------------------------------------------------------------------- #
async def test_cancel_job_sets_cancelled_status(db, user_client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    upload_resp = await user_client.post("/api/jobs/upload", files=_pdf_file())
    job_id = upload_resp.json()["id"]

    cancel_resp = await user_client.post(f"/api/jobs/{job_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"


async def test_delete_job_removes_row(db, user_client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    upload_resp = await user_client.post("/api/jobs/upload", files=_pdf_file())
    job_id = upload_resp.json()["id"]

    delete_resp = await user_client.delete(f"/api/jobs/{job_id}")
    assert delete_resp.status_code == 204

    get_resp = await user_client.get(f"/api/jobs/{job_id}")
    assert get_resp.status_code == 404


async def test_bulk_delete_removes_all_rows(db, user_client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    ids = []
    for i in range(3):
        upload_resp = await user_client.post(
            "/api/jobs/upload", files=_pdf_file(f"job{i}.pdf")
        )
        ids.append(upload_resp.json()["id"])

    bulk_resp = await user_client.post("/api/jobs/bulk-delete", json={"ids": ids})
    assert bulk_resp.status_code == 200
    assert bulk_resp.json()["deleted"] == 3

    for job_id in ids:
        get_resp = await user_client.get(f"/api/jobs/{job_id}")
        assert get_resp.status_code == 404


async def test_bulk_delete_token_without_print_permission_is_403(db, client, tmp_path):
    """Regression (F8): bulk-delete used to depend on get_current_user, which
    performs no permission check, so a token scoped to only "scan" could
    bulk-delete print jobs even though DELETE /api/jobs/{id} correctly 403s
    it. Assert the two routes agree.

    Uses real Bearer tokens throughout (not `user_client`, whose
    dependency_overrides on `get_current_user` would apply to every request
    on the shared `app` instance and bypass the token-scoping path entirely).
    """
    await _seed_upload_dir(db, tmp_path)
    uploader = User(
        email="uploader-bulk@example.com", display_name="UploaderBulk", role="user",
        is_local=True, username="uploader-bulk",
    )
    db.add(uploader)
    await db.commit()
    await db.refresh(uploader)
    upload_token = "pprs_test_print_permission_bulk_delete"
    db.add(APIToken(
        user_id=uploader.id, name="print-ok",
        token_hash=hash_token(upload_token), permissions=["print"],
    ))
    await db.commit()

    upload_resp = await client.post(
        "/api/jobs/upload",
        files=_pdf_file(),
        headers={"Authorization": f"Bearer {upload_token}"},
    )
    assert upload_resp.status_code == 201
    job_id = upload_resp.json()["id"]

    scan_only_user = User(
        email="scanonly-bulk@example.com", display_name="ScanOnlyBulk", role="user",
        is_local=True, username="scanonly-bulk",
    )
    db.add(scan_only_user)
    await db.commit()
    await db.refresh(scan_only_user)
    scan_only_token = "pprs_test_scan_only_bulk_delete"
    db.add(APIToken(
        user_id=scan_only_user.id, name="scan-only",
        token_hash=hash_token(scan_only_token), permissions=["scan"],
    ))
    await db.commit()

    bulk_resp = await client.post(
        "/api/jobs/bulk-delete",
        json={"ids": [job_id]},
        headers={"Authorization": f"Bearer {scan_only_token}"},
    )
    assert bulk_resp.status_code == 403

    # The job must survive the rejected bulk-delete.
    get_resp = await client.get(
        f"/api/jobs/{job_id}", headers={"Authorization": f"Bearer {upload_token}"}
    )
    assert get_resp.status_code == 200


# --------------------------------------------------------------------------- #
# F27 — ownership gate on delete_job / cancel_job / bulk_delete_jobs
#
# Every job endpoint used to filter only on PrintJob.id, so any authenticated
# print user could delete or cancel another user's job. Owner ok, other user
# 403, admin ok, and a NULL-owner (network) job is fair game for anyone.
# --------------------------------------------------------------------------- #
async def _upload_as(client, token: str, filename: str = "test.pdf") -> int:
    resp = await client.post(
        "/api/jobs/upload", files=_pdf_file(filename),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_delete_job_other_user_is_403_owner_survives(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    _, owner_token = await _make_user_with_token(db, "ownerdel")
    _, other_token = await _make_user_with_token(db, "otherdel")
    job_id = await _upload_as(client, owner_token)

    resp = await client.delete(
        f"/api/jobs/{job_id}", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert resp.status_code == 403

    get_resp = await client.get(
        f"/api/jobs/{job_id}", headers={"Authorization": f"Bearer {owner_token}"}
    )
    assert get_resp.status_code == 200


async def test_delete_job_admin_can_delete_others_job(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    _, owner_token = await _make_user_with_token(db, "ownerdel2")
    _, admin_token = await _make_user_with_token(db, "admindel", role="admin")
    job_id = await _upload_as(client, owner_token)

    resp = await client.delete(
        f"/api/jobs/{job_id}", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 204


async def test_delete_network_job_null_owner_is_deletable_by_any_print_user(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    _, token = await _make_user_with_token(db, "anyonedel")

    ingest_resp = await client.post(
        "/api/jobs/internal/ingest", files=_pdf_file("network.pdf"),
    )
    assert ingest_resp.status_code == 201
    job_id = ingest_resp.json()["id"]

    resp = await client.delete(
        f"/api/jobs/{job_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 204


async def test_cancel_job_other_user_is_403(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    _, owner_token = await _make_user_with_token(db, "ownercancel")
    _, other_token = await _make_user_with_token(db, "othercancel")
    job_id = await _upload_as(client, owner_token)

    resp = await client.post(
        f"/api/jobs/{job_id}/cancel", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert resp.status_code == 403

    get_resp = await client.get(
        f"/api/jobs/{job_id}", headers={"Authorization": f"Bearer {owner_token}"}
    )
    assert get_resp.json()["status"] == "held"  # untouched by the rejected cancel


async def test_bulk_delete_skips_other_users_jobs_but_deletes_own(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    _, owner_token = await _make_user_with_token(db, "ownerbulk")
    _, other_token = await _make_user_with_token(db, "otherbulk")
    own_id = await _upload_as(client, owner_token, "mine.pdf")
    others_id = await _upload_as(client, other_token, "theirs.pdf")

    resp = await client.post(
        "/api/jobs/bulk-delete",
        json={"ids": [own_id, others_id]},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1  # only the caller's own job

    mine_resp = await client.get(
        f"/api/jobs/{own_id}", headers={"Authorization": f"Bearer {owner_token}"}
    )
    assert mine_resp.status_code == 404

    theirs_resp = await client.get(
        f"/api/jobs/{others_id}", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert theirs_resp.status_code == 200  # survived the unauthorized bulk-delete attempt


# --------------------------------------------------------------------------- #
# F27 — PIN gate on download_job_file / preview_job_file / get_job_thumbnail
#
# release_pin used to protect only the paper output: /download, /preview and
# /thumbnail ignored it entirely, so any print user could fetch another
# user's confidential file by id with no PIN check.
# --------------------------------------------------------------------------- #
async def test_download_pin_protected_job_owner_needs_no_pin(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    _, owner_token = await _make_user_with_token(db, "ownerdl")

    upload_resp = await client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    job_id = upload_resp.json()["id"]

    resp = await client.get(
        f"/api/jobs/{job_id}/download", headers={"Authorization": f"Bearer {owner_token}"}
    )
    assert resp.status_code == 200


async def test_download_pin_protected_job_other_user_is_403_without_pin(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    _, owner_token = await _make_user_with_token(db, "ownerdl2")
    _, other_token = await _make_user_with_token(db, "otherdl")

    upload_resp = await client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    job_id = upload_resp.json()["id"]

    resp = await client.get(
        f"/api/jobs/{job_id}/download", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert resp.status_code == 403

    # Supplying the correct PIN via the query param grants access.
    resp2 = await client.get(
        f"/api/jobs/{job_id}/download?pin=1234", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert resp2.status_code == 200


async def test_download_pin_protected_job_admin_needs_no_pin(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    _, owner_token = await _make_user_with_token(db, "ownerdl3")
    _, admin_token = await _make_user_with_token(db, "admindl", role="admin")

    upload_resp = await client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    job_id = upload_resp.json()["id"]

    resp = await client.get(
        f"/api/jobs/{job_id}/download", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200


async def test_download_network_job_has_no_pin_to_begin_with(db, client, tmp_path):
    """Network-ingest jobs have no user_id and never carry a release_pin (the
    internal ingest endpoint accepts no pin field), so they're never gated."""
    await _seed_upload_dir(db, tmp_path)
    _, token = await _make_user_with_token(db, "anyonedl")

    ingest_resp = await client.post("/api/jobs/internal/ingest", files=_pdf_file("network.pdf"))
    job_id = ingest_resp.json()["id"]

    resp = await client.get(
        f"/api/jobs/{job_id}/download", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200


async def test_preview_pin_protected_job_other_user_is_403(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    _, owner_token = await _make_user_with_token(db, "ownerpv")
    _, other_token = await _make_user_with_token(db, "otherpv")

    upload_resp = await client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    job_id = upload_resp.json()["id"]

    resp = await client.get(
        f"/api/jobs/{job_id}/preview", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert resp.status_code == 403


async def test_thumbnail_pin_protected_job_other_user_is_403(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    _, owner_token = await _make_user_with_token(db, "ownerth")
    _, other_token = await _make_user_with_token(db, "otherth")

    upload_resp = await client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    job_id = upload_resp.json()["id"]

    resp = await client.get(
        f"/api/jobs/{job_id}/thumbnail", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert resp.status_code == 403


async def test_download_non_pin_job_stays_open_to_any_print_user(db, client, tmp_path):
    """The shared print queue is unaffected for jobs without a PIN — F27
    only gates PIN-protected files."""
    await _seed_upload_dir(db, tmp_path)
    _, owner_token = await _make_user_with_token(db, "ownernopin")
    _, other_token = await _make_user_with_token(db, "othernopin")
    job_id = await _upload_as(client, owner_token)

    resp = await client.get(
        f"/api/jobs/{job_id}/download", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert resp.status_code == 200


async def test_file_gate_pin_throttle_is_shared_with_release(db, client, tmp_path):
    """Regression (review finding #3): the `?pin=` file gate used to have no
    throttle at all — a second, unthrottled oracle over the same 10,000-
    value PIN space release_job already rate-limits (F68). It must share
    release_job's per-job throttle: 5 wrong `?pin=` downloads lock out the
    6th (even with the correct PIN), and release — which shares the same
    `pin:{job_id}` key — is locked out too."""
    await _seed_upload_dir(db, tmp_path)
    _, owner_token = await _make_user_with_token(db, "ownerthrottle")
    _, other_token = await _make_user_with_token(db, "otherthrottle")

    upload_resp = await client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    job_id = upload_resp.json()["id"]

    for _ in range(5):
        resp = await client.get(
            f"/api/jobs/{job_id}/download?pin=0000",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert resp.status_code == 403

    # 6th attempt is locked out, even with the correct PIN.
    locked_resp = await client.get(
        f"/api/jobs/{job_id}/download?pin=1234",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert locked_resp.status_code == 429

    # release shares the same pin:{job_id} key, so it's locked out too.
    release_resp = await client.post(
        f"/api/jobs/{job_id}/release", json={"pin": "1234"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert release_resp.status_code == 429


# --------------------------------------------------------------------------- #
# Network job ingest
# --------------------------------------------------------------------------- #
async def test_ingest_network_job_from_localhost_is_held(db, client, tmp_path):
    # ASGITransport reports the client host as 127.0.0.1 by default, matching
    # the localhost-only guard on this internal endpoint.
    await _seed_upload_dir(db, tmp_path)

    resp = await client.post(
        "/api/jobs/internal/ingest",
        files=_pdf_file("network.pdf"),
        data={"title": "Network Job", "username": "someone"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "held"
    assert body["source_type"] == "network"


# --------------------------------------------------------------------------- #
# print.held webhook dispatch
# --------------------------------------------------------------------------- #
def _capture_held(monkeypatch) -> list:
    """Patch jobs_router.dispatch_webhook to record (event, data) tuples."""
    events: list = []

    async def fake_dispatch(_db, event, data):
        events.append((event, data))

    monkeypatch.setattr(jobs_router, "dispatch_webhook", fake_dispatch)
    return events


async def test_upload_held_job_dispatches_print_held(db, user_client, tmp_path, monkeypatch):
    await _seed_upload_dir(db, tmp_path)
    events = _capture_held(monkeypatch)

    resp = await user_client.post("/api/jobs/upload", files=_pdf_file())
    assert resp.status_code == 201

    held = [d for e, d in events if e == "print.held"]
    assert len(held) == 1
    assert held[0]["source_type"] == "upload"
    assert held[0]["id"] == resp.json()["id"]
    assert "user_id" in held[0]


async def test_upload_not_held_does_not_dispatch_print_held(db, user_client, tmp_path, monkeypatch):
    await _seed_upload_dir(db, tmp_path)
    monkeypatch.setattr(jobs_router, "CupsService", _FakeCupsService)
    events = _capture_held(monkeypatch)

    resp = await user_client.post("/api/jobs/upload", files=_pdf_file(), data={"hold": "false"})
    assert resp.status_code == 201

    assert [e for e, _ in events if e == "print.held"] == []


async def test_ingest_held_network_job_dispatches_print_held(db, client, tmp_path, monkeypatch):
    await _seed_upload_dir(db, tmp_path)
    events = _capture_held(monkeypatch)

    resp = await client.post(
        "/api/jobs/internal/ingest",
        files=_pdf_file("network.pdf"),
        data={"username": "someone"},
    )
    assert resp.status_code == 201

    held = [d for e, d in events if e == "print.held"]
    assert len(held) == 1
    assert held[0]["source_type"] == "network"
    assert held[0]["username"] == "someone"


async def test_ingest_auto_release_does_not_dispatch_print_held(db, client, tmp_path, monkeypatch):
    await _seed_upload_dir(db, tmp_path)
    monkeypatch.setattr(jobs_router, "CupsService", _FakeCupsService)
    events = _capture_held(monkeypatch)

    printer = Printer(
        display_name="Auto", cups_name="auto", uri="",
        is_default=True, is_network_queue=False, auto_release=True,
    )
    db.add(printer)
    await db.commit()

    resp = await client.post("/api/jobs/internal/ingest", files=_pdf_file("auto.pdf"))
    assert resp.status_code == 201
    assert resp.json()["status"] == "completed"
    # Auto-released jobs skip the hold queue -> no print.held.
    assert [e for e, _ in events if e == "print.held"] == []


# Regression test: _process_job used to broadcast serialize_print_job(job)
# right after commit without db.refresh(job); the server-side updated_at was
# expired by the UPDATE flush and the synchronous serialization raised
# MissingGreenlet, 500ing every hold=false upload and auto_release ingest.
async def test_ingest_network_job_with_auto_release_printer_completes(
    db, client, tmp_path, monkeypatch
):
    await _seed_upload_dir(db, tmp_path)
    monkeypatch.setattr(jobs_router, "CupsService", _FakeCupsService)

    printer = Printer(
        display_name="Auto",
        cups_name="auto",
        uri="",
        is_default=True,
        is_network_queue=False,
        auto_release=True,
    )
    db.add(printer)
    await db.commit()

    resp = await client.post(
        "/api/jobs/internal/ingest",
        files=_pdf_file("auto.pdf"),
    )
    assert resp.status_code == 201
    body = resp.json()
    # _process_job runs to completion inline: held -> printing -> completed.
    assert body["status"] == "completed"
    assert body["cups_job_id"] == 777

    fake = _FakeCupsService.last_instance
    assert fake is not None
    assert fake.released == [777]


# --------------------------------------------------------------------------- #
# Thumbnail endpoint (GET /{job_id}/thumbnail)
# --------------------------------------------------------------------------- #
async def test_job_thumbnail_404_when_job_missing(user_client):
    resp = await user_client.get("/api/jobs/999999/thumbnail")
    assert resp.status_code == 404


@pytest.mark.skipif(shutil.which("gs") is None, reason="ghostscript not installed")
async def test_pdf_job_thumbnail_returns_jpeg_and_is_cached_on_repeat(
    db, user_client, tmp_path
):
    await _seed_upload_dir(db, tmp_path)
    upload_resp = await user_client.post(
        "/api/jobs/upload", files=_pdf_file(data=_real_pdf_bytes())
    )
    job_id = upload_resp.json()["id"]

    resp = await user_client.get(f"/api/jobs/{job_id}/thumbnail")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == "private, max-age=86400"

    thumb_file = next(f for f in tmp_path.iterdir() if f.name.endswith(".thumb.jpg"))
    first_mtime = thumb_file.stat().st_mtime

    # Repeat request must reuse the cached .thumb.jpg, not regenerate it —
    # get_or_create_thumbnail's mtime check short-circuits regeneration.
    resp2 = await user_client.get(f"/api/jobs/{job_id}/thumbnail")
    assert resp2.status_code == 200
    assert thumb_file.stat().st_mtime == first_mtime


@pytest.mark.skipif(shutil.which("gs") is None, reason="ghostscript not installed")
async def test_office_job_thumbnail_converts_and_caches_preview_pdf(
    db, user_client, tmp_path, monkeypatch
):
    """The thumbnail endpoint shares `_ensure_preview_pdf` with `/preview`: an
    office-doc job gets converted to PDF (cached as `.preview.pdf`) before
    being thumbnailed. `convert_to_pdf` itself is faked here — exercising real
    LibreOffice is `test_convert_service.py`'s job — but it writes a real
    single-page PDF so the ghostscript thumbnail render underneath is real.
    """
    doc_path = tmp_path / "report.docx"
    doc_path.write_bytes(b"not a real docx; convert_to_pdf is faked below")

    job = PrintJob(
        title="report.docx",
        filename="report.docx",
        filepath=str(doc_path),
        file_size=doc_path.stat().st_size,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        status="held",
        source_type="upload",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # convert_to_pdf now writes into its own unique temp subdir (F30), never
    # directly into output_dir — mirror that here so _ensure_preview_pdf's
    # post-rename cleanup rmtree()s only that subdir, not tmp_path itself.
    convert_tmpdir = tmp_path / "convert_fake"
    convert_tmpdir.mkdir()
    converted_path = convert_tmpdir / "converted_output.pdf"
    converted_path.write_bytes(_real_pdf_bytes())

    calls = []

    async def _fake_convert_to_pdf(input_path, output_dir):
        calls.append((input_path, output_dir))
        return str(converted_path)

    monkeypatch.setattr(jobs_router, "convert_to_pdf", _fake_convert_to_pdf)

    resp = await user_client.get(f"/api/jobs/{job.id}/thumbnail")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == "private, max-age=86400"
    assert calls == [(str(doc_path), str(tmp_path))]

    preview_path = str(doc_path) + ".preview.pdf"
    assert os.path.exists(preview_path)  # cached for reuse by /preview too
    assert os.path.exists(preview_path + ".thumb.jpg")
    assert not os.path.exists(converted_path)  # renamed into the cache, not copied
    assert not convert_tmpdir.exists()  # F31: convert_to_pdf's temp dir is cleaned up


# --------------------------------------------------------------------------- #
# _ensure_preview_pdf unit tests — plain job-like objects, no DB/HTTP needed
# --------------------------------------------------------------------------- #
class _JobStub:
    def __init__(self, mime_type: str, filepath: str):
        self.mime_type = mime_type
        self.filepath = filepath


async def _unreachable_convert_to_pdf(*args, **kwargs):
    raise AssertionError("convert_to_pdf must not be called for this branch")


async def test_ensure_preview_pdf_passes_through_pdf_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_router, "convert_to_pdf", _unreachable_convert_to_pdf)
    job = _JobStub(mime_type="application/pdf", filepath=str(tmp_path / "a.pdf"))

    result = await jobs_router._ensure_preview_pdf(job)

    assert result == job.filepath


async def test_ensure_preview_pdf_passes_through_image_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_router, "convert_to_pdf", _unreachable_convert_to_pdf)
    job = _JobStub(mime_type="image/jpeg", filepath=str(tmp_path / "a.jpg"))

    result = await jobs_router._ensure_preview_pdf(job)

    assert result == job.filepath


async def test_ensure_preview_pdf_converts_office_doc_and_caches_result(tmp_path, monkeypatch):
    src = tmp_path / "doc.docx"
    src.write_bytes(b"fake docx")
    # convert_to_pdf now writes into its own unique temp subdir (F30), never
    # directly into output_dir — mirror that here so the post-rename cleanup
    # rmtree()s only that subdir, not tmp_path itself.
    convert_tmpdir = tmp_path / "convert_fake"
    convert_tmpdir.mkdir()
    converted = convert_tmpdir / "doc.pdf"
    converted.write_bytes(b"%PDF-fake-converted%")

    calls = []

    async def _fake_convert(input_path, output_dir):
        calls.append((input_path, output_dir))
        return str(converted)

    monkeypatch.setattr(jobs_router, "convert_to_pdf", _fake_convert)
    job = _JobStub(
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filepath=str(src),
    )

    result = await jobs_router._ensure_preview_pdf(job)

    expected_preview = str(src) + ".preview.pdf"
    assert result == expected_preview
    assert os.path.exists(expected_preview)
    assert not os.path.exists(converted)  # renamed, not copied
    assert not convert_tmpdir.exists()  # F31: convert_to_pdf's temp dir is cleaned up
    assert calls == [(str(src), str(tmp_path))]


async def test_ensure_preview_pdf_reuses_cached_preview_without_reconverting(
    tmp_path, monkeypatch
):
    src = tmp_path / "doc.docx"
    src.write_bytes(b"fake docx")
    preview_path = str(src) + ".preview.pdf"
    with open(preview_path, "wb") as f:
        f.write(b"already-cached")

    monkeypatch.setattr(jobs_router, "convert_to_pdf", _unreachable_convert_to_pdf)
    job = _JobStub(
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filepath=str(src),
    )

    result = await jobs_router._ensure_preview_pdf(job)

    assert result == preview_path


async def test_ensure_preview_pdf_wraps_conversion_failure(tmp_path, monkeypatch):
    src = tmp_path / "doc.docx"
    src.write_bytes(b"fake docx")

    async def _fail(*args, **kwargs):
        raise RuntimeError("libreoffice exploded")

    monkeypatch.setattr(jobs_router, "convert_to_pdf", _fail)
    job = _JobStub(
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filepath=str(src),
    )

    with pytest.raises(ExternalServiceError):
        await jobs_router._ensure_preview_pdf(job)


# --------------------------------------------------------------------------- #
# Share-target (PWA share_target action, POST /api/share-target)
#
# Mounted outside the /api/jobs prefix (see main.py), so these use `client`
# (unaugmented) directly rather than `user_client`. The route's soft-auth
# path calls `get_current_user` for real instead of depending on it, so the
# `user_client` fixture's dependency_overrides trick doesn't apply here — a
# real Bearer token exercises the exact same auth resolution the browser's
# session cookie would go through, same as test_api_auth.py's token tests.
# --------------------------------------------------------------------------- #
async def _seed_share_user_and_token(db, *, plaintext: str = "pprs_test_share_token") -> str:
    user = User(
        email="share@example.com", display_name="Share", role="user",
        is_local=True, username="share",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = APIToken(
        user_id=user.id,
        name="share-token",
        token_hash=hash_token(plaintext),
        permissions=["print"],
    )
    db.add(token)
    await db.commit()
    return plaintext


async def test_share_target_authenticated_creates_held_job_and_redirects(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    plaintext = await _seed_share_user_and_token(db)

    resp = await client.post(
        "/api/share-target",
        files=_pdf_file(),
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/print"

    list_resp = await client.get("/api/jobs", headers={"Authorization": f"Bearer {plaintext}"})
    assert list_resp.status_code == 200
    jobs = list_resp.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["status"] == "held"
    assert jobs[0]["filename"] == "test.pdf"


async def test_share_target_multiple_files_each_create_a_job(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    plaintext = await _seed_share_user_and_token(db)

    resp = await client.post(
        "/api/share-target",
        files=[
            ("file", ("a.pdf", io.BytesIO(_MINIMAL_PDF), "application/pdf")),
            ("file", ("b.pdf", io.BytesIO(_MINIMAL_PDF), "application/pdf")),
        ],
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/print"

    list_resp = await client.get("/api/jobs", headers={"Authorization": f"Bearer {plaintext}"})
    jobs = list_resp.json()["jobs"]
    assert len(jobs) == 2
    assert {j["filename"] for j in jobs} == {"a.pdf", "b.pdf"}


async def test_share_target_unauthenticated_redirects_to_login(client, tmp_path):
    resp = await client.post("/api/share-target", files=_pdf_file())
    assert resp.status_code == 303
    assert resp.headers["location"] == "/api/auth/login"


async def test_share_target_oversize_file_redirects_with_share_failed_count(db, client, tmp_path):
    """Regression (F133): a per-file failure (413 here) used to abort the
    whole share-target loop with a raw JSON error page instead of the usual
    303 redirect. It must be caught, counted, and surfaced via
    `?share_failed=<n>` instead."""
    await _seed_upload_dir(db, tmp_path)
    await _seed_setting(db, "max_upload_size_mb", "1")
    plaintext = await _seed_share_user_and_token(db)

    oversized = b"0" * (2 * 1024 * 1024)  # 2 MiB > 1 MiB cap
    resp = await client.post(
        "/api/share-target",
        files={"file": ("big.pdf", io.BytesIO(oversized), "application/pdf")},
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/print?share_failed=1"

    list_resp = await client.get("/api/jobs", headers={"Authorization": f"Bearer {plaintext}"})
    assert list_resp.json()["jobs"] == []


async def test_share_target_mixed_valid_and_invalid_files_creates_the_valid_one(
    db, client, tmp_path
):
    """A share with one good file and one bad one must still create the good
    job (F133) — the old code aborted the whole loop on the first failure."""
    await _seed_upload_dir(db, tmp_path)
    plaintext = await _seed_share_user_and_token(db)

    resp = await client.post(
        "/api/share-target",
        files=[
            ("file", ("good.pdf", io.BytesIO(_MINIMAL_PDF), "application/pdf")),
            ("file", ("notes.txt", io.BytesIO(b"plain text"), "text/plain")),
        ],
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/print?share_failed=1"

    list_resp = await client.get("/api/jobs", headers={"Authorization": f"Bearer {plaintext}"})
    jobs = list_resp.json()["jobs"]
    assert [j["filename"] for j in jobs] == ["good.pdf"]


async def test_share_target_skips_auto_pin_under_require_release_pin(db, client, tmp_path):
    """Regression: the share flow redirects immediately and can never display a
    generated PIN, so under require_release_pin the job must be held WITHOUT a
    PIN (an unseen auto-PIN would make it permanently unreleasable)."""
    await _seed_upload_dir(db, tmp_path)
    await _seed_setting(db, "require_release_pin", "true")
    plaintext = await _seed_share_user_and_token(db)

    resp = await client.post(
        "/api/share-target",
        files=_pdf_file(),
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 303

    list_resp = await client.get("/api/jobs", headers={"Authorization": f"Bearer {plaintext}"})
    jobs = list_resp.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["status"] == "held"
    assert jobs[0]["has_pin"] is False


async def test_share_target_token_without_print_permission_redirects_to_login(db, client, tmp_path):
    """A scope-limited API token lacking the "print" permission must not be
    able to enqueue jobs through the share route (parity with /upload)."""
    await _seed_upload_dir(db, tmp_path)
    user = User(
        email="scanonly@example.com", display_name="ScanOnly", role="user",
        is_local=True, username="scanonly",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    plaintext = "pprs_test_scan_only_token"
    db.add(APIToken(
        user_id=user.id, name="scan-token",
        token_hash=hash_token(plaintext), permissions=["scan"],
    ))
    await db.commit()

    resp = await client.post(
        "/api/share-target",
        files=_pdf_file(),
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/api/auth/login"
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# Reprint (F29)
# --------------------------------------------------------------------------- #
async def test_reprint_copies_file_independently_of_original(db, user_client, tmp_path):
    """Regression (F29): reprint_job used to alias original.filepath onto the
    new row instead of copying the file, so deleting either job's row would
    unlink the file out from under the other one."""
    await _seed_upload_dir(db, tmp_path)
    upload_resp = await user_client.post("/api/jobs/upload", files=_pdf_file())
    original_id = upload_resp.json()["id"]

    reprint_resp = await user_client.post(f"/api/jobs/{original_id}/reprint")
    assert reprint_resp.status_code == 201
    reprint_id = reprint_resp.json()["id"]

    # Two distinct files must exist on disk now, not one shared path.
    assert len(list(tmp_path.iterdir())) == 2

    delete_resp = await user_client.delete(f"/api/jobs/{original_id}")
    assert delete_resp.status_code == 204

    # The reprint's own file must survive the original's deletion.
    download_resp = await user_client.get(f"/api/jobs/{reprint_id}/download")
    assert download_resp.status_code == 200


# --------------------------------------------------------------------------- #
# F27 — reprint of a PIN-protected job (review fix)
#
# reprint_job used to have no ownership or PIN check at all, and built the
# new row with no release_pin — so any print user could reprint someone
# else's PIN-protected job and get an unprotected copy of the file.
# --------------------------------------------------------------------------- #
async def test_reprint_pin_protected_job_other_user_is_403_without_pin(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    _, owner_token = await _make_user_with_token(db, "ownerrp")
    _, other_token = await _make_user_with_token(db, "otherrp")

    upload_resp = await client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    job_id = upload_resp.json()["id"]

    resp = await client.post(
        f"/api/jobs/{job_id}/reprint", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert resp.status_code == 403


async def test_reprint_pin_protected_job_other_user_with_correct_pin_ok(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    _, owner_token = await _make_user_with_token(db, "ownerrp2")
    _, other_token = await _make_user_with_token(db, "otherrp2")

    upload_resp = await client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    job_id = upload_resp.json()["id"]

    resp = await client.post(
        f"/api/jobs/{job_id}/reprint", json={"pin": "1234"},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 201


async def test_reprint_pin_protected_job_owner_needs_no_pin(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    _, owner_token = await _make_user_with_token(db, "ownerrp3")

    upload_resp = await client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    job_id = upload_resp.json()["id"]

    resp = await client.post(
        f"/api/jobs/{job_id}/reprint", headers={"Authorization": f"Bearer {owner_token}"}
    )
    assert resp.status_code == 201


async def test_reprint_pin_protected_job_admin_needs_no_pin(db, client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    _, owner_token = await _make_user_with_token(db, "ownerrp4")
    _, admin_token = await _make_user_with_token(db, "adminrp", role="admin")

    upload_resp = await client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    job_id = upload_resp.json()["id"]

    resp = await client.post(
        f"/api/jobs/{job_id}/reprint", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 201


async def test_reprint_carries_the_pin_forward_onto_the_new_job(db, user_client, tmp_path):
    """The reprinted copy must stay PIN-protected — not silently drop
    protection because the new row is unconditionally owned by the
    reprinter."""
    await _seed_upload_dir(db, tmp_path)
    upload_resp = await user_client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"}
    )
    job_id = upload_resp.json()["id"]

    resp = await user_client.post(f"/api/jobs/{job_id}/reprint")
    assert resp.status_code == 201
    assert resp.json()["has_pin"] is True


# --------------------------------------------------------------------------- #
# print.upload webhook dispatch (F137)
# --------------------------------------------------------------------------- #
async def test_upload_dispatches_print_upload_webhook(db, user_client, tmp_path, monkeypatch):
    await _seed_upload_dir(db, tmp_path)
    events = _capture_held(monkeypatch)

    resp = await user_client.post("/api/jobs/upload", files=_pdf_file())
    assert resp.status_code == 201

    uploads = [d for e, d in events if e == "print.upload"]
    assert len(uploads) == 1
    assert uploads[0]["id"] == resp.json()["id"]
    assert uploads[0]["title"] == "test.pdf"
    assert uploads[0]["source_type"] == "upload"
    assert "user_id" in uploads[0]

    # A held upload dispatches both print.upload and print.held.
    assert [e for e, _ in events if e == "print.held"] == ["print.held"]


async def test_upload_dispatches_print_upload_even_when_not_held(
    db, user_client, tmp_path, monkeypatch
):
    """print.upload fires for every upload regardless of hold/auto-print —
    print.held is the narrower "landed in the hold queue" signal."""
    await _seed_upload_dir(db, tmp_path)
    monkeypatch.setattr(jobs_router, "CupsService", _FakeCupsService)
    events = _capture_held(monkeypatch)

    resp = await user_client.post("/api/jobs/upload", files=_pdf_file(), data={"hold": "false"})
    assert resp.status_code == 201

    assert [e for e, _ in events if e == "print.upload"] != []
    assert [e for e, _ in events if e == "print.held"] == []
