"""POST /api/scanner/scans/{id}/backs through the real route (DB harness).

scan_service is faked at scanner.py's import site so no scanimage runs; the
fake writes a real back-side PDF.
"""
import os

import pytest
from sqlalchemy import select

from app.auth.tokens import hash_token
from app.exceptions import ScannerBusyError
from app.models import APIToken, ScanJob, User
from app.routers import scanner as scanner_router
from app.services import duplex_service
from app.services.scan_service import ScanError
from tests.duplex_helpers import make_pdf, tags


class _FakeScanService:
    def __init__(self, tmp_path, specs, error=None):
        self.tmp_path, self.specs, self.error = tmp_path, specs, error
        self.calls: list[dict] = []

    async def scan_batch(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        path = str(self.tmp_path / "backs.pdf")
        make_pdf(path, self.specs)
        return "backs-scan-id", path, len(self.specs)


async def _fake_device(_db) -> str:
    return "test:device0"


def _patch(monkeypatch, tmp_path, specs, error=None):
    fake = _FakeScanService(tmp_path, specs, error)
    monkeypatch.setattr(scanner_router, "scan_service", fake)
    monkeypatch.setattr(scanner_router, "get_default_scanner_device", _fake_device)
    return fake


async def _seed(db, tmp_path, fronts, **overrides) -> ScanJob:
    path = str(tmp_path / "fronts.pdf")
    make_pdf(path, fronts)
    fields = dict(
        scan_id="front-scan-id", status="completed", source="ADF", format="pdf",
        resolution=200, mode="Gray", page_count=len(fronts), filepath=path,
        file_size=os.path.getsize(path),
    )
    fields.update(overrides)
    job = ScanJob(**fields)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def _kept(db) -> ScanJob:
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == "backs-scan-id"))
    return result.scalar_one()


URL = "/api/scanner/scans/front-scan-id/backs"
FRONTS = [(1, "text"), (2, "text"), (3, "text")]


async def test_merges_backs_into_original(db, user_client, monkeypatch, tmp_path):
    job = await _seed(db, tmp_path, FRONTS)
    fake = _patch(monkeypatch, tmp_path, [(11, "text"), (12, "text"), (13, "text")])
    resp = await user_client.post(URL, json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["scan_id"] == "front-scan-id" and body["page_count"] == 6
    assert tags(job.filepath) == [1, 13, 2, 12, 3, 11]
    assert body["file_size"] == os.path.getsize(job.filepath)
    assert not os.path.exists(tmp_path / "backs.pdf")
    # Reuses the original's resolution and mode, and the default device.
    assert fake.calls[0]["resolution"] == 200 and fake.calls[0]["mode"] == "Gray"
    assert fake.calls[0]["device"] == "test:device0"
    count = (await db.execute(select(ScanJob))).scalars().all()
    assert len(count) == 1


async def test_blank_backs_removed_by_default(db, user_client, monkeypatch, tmp_path):
    job = await _seed(db, tmp_path, FRONTS)
    _patch(monkeypatch, tmp_path, [(11, "blank"), (12, "text"), (13, "blank")])
    resp = await user_client.post(URL, json={"rotate_backs": True})
    assert resp.status_code == 200 and resp.json()["page_count"] == 4
    assert tags(job.filepath) == [1, 2, 12, 3]


async def test_blank_backs_kept_when_disabled(db, user_client, monkeypatch, tmp_path):
    job = await _seed(db, tmp_path, [(1, "text")])
    _patch(monkeypatch, tmp_path, [(11, "blank")])
    resp = await user_client.post(URL, json={"skip_blank_backs": False})
    assert resp.status_code == 200
    assert tags(job.filepath) == [1, 11]


async def test_count_mismatch_keeps_both(db, user_client, monkeypatch, tmp_path):
    job = await _seed(db, tmp_path, FRONTS)
    before = open(job.filepath, "rb").read()
    _patch(monkeypatch, tmp_path, [(11, "text"), (12, "text")])
    resp = await user_client.post(URL, json={})
    assert resp.status_code == 409
    assert "history" in resp.json()["detail"]
    assert open(job.filepath, "rb").read() == before
    kept = await _kept(db)
    assert kept.status == "completed" and kept.page_count == 2
    assert tags(kept.filepath) == [11, 12]
    await db.refresh(job)
    assert job.page_count == 3


async def test_merge_failure_keeps_original_and_backs(db, user_client, monkeypatch, tmp_path):
    job = await _seed(db, tmp_path, FRONTS)
    before = open(job.filepath, "rb").read()
    _patch(monkeypatch, tmp_path, [(11, "text")] * 3)

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(duplex_service, "merge_backs_in_place", boom)
    resp = await user_client.post(URL, json={})
    assert resp.status_code == 409
    assert "disk" not in resp.json()["detail"]
    assert open(job.filepath, "rb").read() == before
    kept = await _kept(db)
    assert os.path.exists(kept.filepath)


@pytest.mark.parametrize("overrides", [
    {"source": "Flatbed"}, {"format": "png"}, {"status": "failed"}, {"filepath": None},
])
async def test_ineligible_rows_refused(db, user_client, monkeypatch, tmp_path, overrides):
    await _seed(db, tmp_path, FRONTS, **overrides)
    fake = _patch(monkeypatch, tmp_path, [(11, "text")] * 3)
    resp = await user_client.post(URL, json={})
    assert resp.status_code == 400
    assert resp.json()["detail"]
    assert fake.calls == []


async def test_missing_file_refused(db, user_client, monkeypatch, tmp_path):
    job = await _seed(db, tmp_path, FRONTS)
    os.unlink(job.filepath)
    fake = _patch(monkeypatch, tmp_path, [(11, "text")] * 3)
    assert (await user_client.post(URL, json={})).status_code == 400
    assert fake.calls == []


async def test_unknown_scan_is_404(db, user_client, monkeypatch, tmp_path):
    fake = _patch(monkeypatch, tmp_path, [])
    assert (await user_client.post("/api/scanner/scans/nope/backs", json={})).status_code == 404
    assert fake.calls == []


@pytest.mark.parametrize("error", [
    ScanError("No pages scanned from ADF"), ScanError("Scanner is busy. Try again later."),
    ScannerBusyError("No default scanner configured"),
])
async def test_feeder_refusal_changes_nothing(db, user_client, monkeypatch, tmp_path, error):
    job = await _seed(db, tmp_path, FRONTS)
    before = open(job.filepath, "rb").read()
    _patch(monkeypatch, tmp_path, [], error=error)
    resp = await user_client.post(URL, json={})
    assert resp.status_code == error.status_code
    assert resp.json()["detail"] == error.detail
    assert open(job.filepath, "rb").read() == before
    assert len((await db.execute(select(ScanJob))).scalars().all()) == 1


async def test_token_without_scan_permission_is_403(db, client, monkeypatch, tmp_path):
    await _seed(db, tmp_path, FRONTS)
    fake = _patch(monkeypatch, tmp_path, [(11, "text")] * 3)
    user = User(email="p@example.com", display_name="P", role="user", is_local=True, username="p")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    db.add(APIToken(user_id=user.id, name="t", token_hash=hash_token("pprs_backs_print_only"),
                    permissions=["print"]))
    await db.commit()
    resp = await client.post(
        URL, json={}, headers={"Authorization": "Bearer pprs_backs_print_only"},
    )
    assert resp.status_code == 403
    assert fake.calls == []
