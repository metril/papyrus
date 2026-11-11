"""Scan/copy cancel + pollable progress."""
import asyncio

import pytest
from sqlalchemy import select

from app.models import AuditEntry, PrintJob, ScanJob, User
from app.routers import copy as copy_router
from app.routers import scanner as scanner_router
from app.services import copy_service as copy_service_module
from app.services import scan_tasks

CID = "aaaaaaaa-1111-2222-3333-444444444444"


class _HangingScan:
    """Fake scan_service: reports progress, then hangs until cancelled or released."""

    def __init__(self, path):
        self.path = path
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def scan(self, **kw):
        await kw["progress_callback"]("x", 40.0)
        if kw.get("page_count_callback"):
            kw["page_count_callback"](3)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return "internal", self.path

    scan_batch = scan


async def _dev(_db):
    return "test:device0"


@pytest.fixture
def hang(monkeypatch, tmp_path):
    f = tmp_path / "out.pdf"
    f.write_bytes(b"%PDF-1.4 fake\n")
    fake = _HangingScan(str(f))
    monkeypatch.setattr(scanner_router, "scan_service", fake)
    monkeypatch.setattr(scanner_router, "get_default_scanner_device", _dev)
    monkeypatch.setattr(copy_service_module, "scan_service", fake)
    monkeypatch.setattr(copy_router, "get_default_scanner_device", _dev)

    async def queue(_db):
        return "q_release"

    monkeypatch.setattr(copy_router, "get_default_release_queue_name", queue)
    monkeypatch.setattr(
        copy_router, "get_upload_path", lambda name, upload_dir=None: str(tmp_path / name)
    )
    return fake


class _FakeCups:
    cancelled: list = []

    def __init__(self, printer_name=""):
        pass

    async def create_held_job(self, **kw):
        return 77

    async def release_job(self, job_id):
        await _FakeCups.gate.wait()

    async def cancel_job(self, job_id):
        _FakeCups.cancelled.append(job_id)


@pytest.fixture
def cups(monkeypatch):
    _FakeCups.cancelled = []
    _FakeCups.gate = asyncio.Event()
    monkeypatch.setattr(copy_router, "CupsService", _FakeCups)
    return _FakeCups


async def _wait(cond, tries=200):
    for _ in range(tries):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timeout")


async def _start_scan(client, hang, sid, path="/api/v1/scans"):
    t = asyncio.create_task(client.post(path, json={"scan_id": sid}))
    await asyncio.wait_for(hang.started.wait(), 5)
    return t


SID = "11111111-2222-3333-4444-555555555555"


async def test_cancel_during_scan(db, user_client, hang):
    t = await _start_scan(user_client, hang, SID)
    got = await user_client.get(f"/api/v1/scans/{SID}")
    assert got.json()["progress_pct"] == 40 and got.json()["page_count"] == 3
    lst = (await user_client.get("/api/v1/scans")).json()["items"][0]
    assert lst["progress_pct"] == 40 and lst["page_count"] == 3

    r = await user_client.post(f"/api/v1/scans/{SID}/cancel")
    assert r.status_code == 202
    resp = await t
    assert resp.status_code == 409 and resp.json()["code"] == "scan_cancelled"
    assert hang.cancelled
    got = (await user_client.get(f"/api/v1/scans/{SID}")).json()
    assert got["status"] == "cancelled" and got["progress_pct"] is None
    assert not scan_tasks.is_registered(SID)
    db.expire_all()
    ev = (await db.execute(select(AuditEntry).where(AuditEntry.action == "scan.cancel"))).all()
    assert len(ev) == 1


async def test_cancel_batch(db, user_client, hang):
    t = await _start_scan(user_client, hang, SID, "/api/v1/scans/batch")
    assert (await user_client.post(f"/api/v1/scans/{SID}/cancel")).status_code == 202
    assert (await t).json()["code"] == "scan_cancelled"


async def test_cancel_not_running_409(db, user_client, regular_user):
    db.add(ScanJob(user_id=regular_user.id, scan_id=SID, status="completed"))
    await db.commit()
    r = await user_client.post(f"/api/v1/scans/{SID}/cancel")
    assert r.status_code == 409 and r.json()["code"] == "scan_not_running"


async def test_cancel_foreign_404(db, user_client, regular_user):
    other = User(email="o@example.com", display_name="o", role="user", is_local=True, username="o")
    db.add(other)
    await db.commit()
    db.add(ScanJob(user_id=other.id, scan_id=SID, status="scanning"))
    await db.commit()
    assert (await user_client.post(f"/api/v1/scans/{SID}/cancel")).status_code == 404
    assert (await user_client.get(f"/api/v1/scans/{SID}")).status_code == 404


async def test_cancel_stale_scanning_row(db, user_client, regular_user):
    db.add(ScanJob(user_id=regular_user.id, scan_id=SID, status="scanning"))
    await db.commit()
    r = await user_client.post(f"/api/v1/scans/{SID}/cancel")
    assert r.status_code == 202
    assert (await user_client.get(f"/api/v1/scans/{SID}")).json()["status"] == "cancelled"


async def test_client_disconnect_marks_cancelled(db, user_client, hang):
    t = await _start_scan(user_client, hang, SID)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    await _wait(lambda: hang.cancelled)
    await _wait(lambda: not scan_tasks.is_registered(SID))
    db.expire_all()
    job = (await db.execute(select(ScanJob).where(ScanJob.scan_id == SID))).scalar_one()
    assert job.status == "cancelled"


async def test_scan_completes_normally(db, user_client, hang):
    hang.release.set()
    r = await user_client.post("/api/v1/scans", json={"scan_id": SID})
    assert r.status_code == 201 and r.json()["status"] == "completed"
    assert r.json()["progress_pct"] is None


# ---- copy ----
async def _start_copy(client, cid=CID):
    return asyncio.create_task(client.post("/api/v1/copy", json={"copy_id": cid}))


async def test_copy_cancel_while_scanning(db, user_client, hang, cups):
    t = await _start_copy(user_client)
    await asyncio.wait_for(hang.started.wait(), 5)
    st = (await user_client.get(f"/api/v1/copy/{CID}")).json()
    assert st["phase"] == "scanning" and st["progress_pct"] == 40 and st["scan_id"]
    # copy_id reuse
    dup = await user_client.post("/api/v1/copy", json={"copy_id": CID})
    assert dup.status_code == 409 and dup.json()["code"] == "copy_id_in_use"

    r = await user_client.post(f"/api/v1/copy/{CID}/cancel")
    assert r.status_code == 202
    resp = await t
    assert resp.status_code == 409 and resp.json()["code"] == "scan_cancelled"
    st = (await user_client.get(f"/api/v1/copy/{CID}")).json()
    assert st["phase"] == "cancelled"
    status = (await db.execute(
        select(ScanJob.status).where(ScanJob.scan_id == st["scan_id"]))).scalar_one()
    assert status == "cancelled"
    again = await user_client.post(f"/api/v1/copy/{CID}/cancel")
    assert again.status_code == 409 and again.json()["code"] == "copy_not_running"


async def test_copy_phases_and_cancel_while_printing(db, user_client, hang, cups):
    hang.release.set()
    t = await _start_copy(user_client)
    await _wait(lambda: getattr(scan_tasks.get_copy(CID), "cups_job_id", None) == 77)
    assert (await user_client.get(f"/api/v1/copy/{CID}")).json()["phase"] == "printing"
    r = await user_client.post(f"/api/v1/copy/{CID}/cancel")
    assert r.status_code == 202 and r.json()["phase"] == "cancelled"
    assert cups.cancelled == [77]
    cups.gate.set()
    resp = await t
    assert resp.status_code == 200 and resp.json()["copy_id"] == CID
    status = (await db.execute(select(PrintJob.status))).scalar_one()
    assert status == "cancelled"
    assert (await user_client.get(f"/api/v1/copy/{CID}")).json()["phase"] == "cancelled"


async def test_copy_done(db, user_client, hang, cups):
    hang.release.set()
    cups.gate.set()
    r = await user_client.post("/api/v1/copy", json={"copy_id": CID})
    assert r.status_code == 200
    st = (await user_client.get(f"/api/v1/copy/{CID}")).json()
    assert st["phase"] == "done" and st["progress_pct"] == 100 and st["job_id"]
    assert (await user_client.post(f"/api/v1/copy/{CID}/cancel")).status_code == 409


async def test_copy_foreign_404(db, user_client, admin_user):
    scan_tasks.register_copy("bbbbbbbb-1111-2222-3333-444444444444", admin_user.id)
    r = await user_client.get("/api/v1/copy/bbbbbbbb-1111-2222-3333-444444444444")
    assert r.status_code == 404
    r = await user_client.post("/api/v1/copy/bbbbbbbb-1111-2222-3333-444444444444/cancel")
    assert r.status_code == 404


@pytest.fixture(autouse=True)
def _clean_registry():
    scan_tasks._copies.clear()
    yield
    scan_tasks._copies.clear()
