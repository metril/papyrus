"""F26 (client-supplied scan_id) and F76 (audit entries actually persist).

`scan_service`/`get_default_scanner_device` are faked at scanner.py's own
import sites (mirrors test_api_escl.py's convention) so no real `scanimage`
subprocess ever runs.
"""
import pytest
from sqlalchemy import select

from app.models import AuditEntry, ScanJob
from app.routers import scanner as scanner_router


class _FakeScanService:
    def __init__(self, filepath: str):
        self._filepath = filepath
        self.calls: list[dict] = []

    async def scan(self, **kwargs):
        self.calls.append(kwargs)
        # The internal scan_id scan_service generates for file-naming
        # purposes is deliberately different from the client-supplied /
        # DB-row scan_id -- F26 requires the two to never be conflated.
        return "internal-file-naming-id", self._filepath


async def _fake_get_default_scanner_device(_db) -> str:
    return "test:device0"


def _patch_scan(monkeypatch, filepath: str) -> _FakeScanService:
    fake = _FakeScanService(filepath)
    monkeypatch.setattr(scanner_router, "scan_service", fake)
    monkeypatch.setattr(
        scanner_router, "get_default_scanner_device", _fake_get_default_scanner_device
    )
    return fake


# --------------------------------------------------------------------------- #
# F37 — list_scans limit/offset are bounded
# --------------------------------------------------------------------------- #
async def test_list_scans_rejects_negative_limit(user_client):
    resp = await user_client.get("/api/scanner/scans", params={"limit": -1})
    assert resp.status_code == 422


async def test_list_scans_rejects_negative_offset(user_client):
    resp = await user_client.get("/api/scanner/scans", params={"offset": -1})
    assert resp.status_code == 422


async def test_list_scans_rejects_limit_above_200(user_client):
    resp = await user_client.get("/api/scanner/scans", params={"limit": 1_000_000})
    assert resp.status_code == 422


async def test_list_scans_accepts_max_limit_of_200(user_client):
    resp = await user_client.get("/api/scanner/scans", params={"limit": 200})
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# F26: client-supplied scan_id is used for the row (and progress channel)
# --------------------------------------------------------------------------- #
async def test_initiate_scan_uses_client_supplied_scan_id(db, user_client, monkeypatch, tmp_path):
    scan_file = tmp_path / "out.pdf"
    scan_file.write_bytes(b"%PDF-1.4 fake\n")
    _patch_scan(monkeypatch, str(scan_file))

    client_id = "11111111-2222-3333-4444-555555555555"
    resp = await user_client.post("/api/scanner/scan", json={"scan_id": client_id})

    assert resp.status_code == 201
    assert resp.json()["scan_id"] == client_id

    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == client_id))
    job = result.scalar_one()
    assert job.status == "completed"
    # The internal scan_service-generated id must never leak onto the row --
    # it would desync the id the client subscribed to from the row's own id.
    assert job.scan_id != "internal-file-naming-id"


async def test_initiate_scan_generates_scan_id_when_omitted(db, user_client, monkeypatch, tmp_path):
    scan_file = tmp_path / "out.pdf"
    scan_file.write_bytes(b"%PDF-1.4 fake\n")
    _patch_scan(monkeypatch, str(scan_file))

    resp = await user_client.post("/api/scanner/scan", json={})

    assert resp.status_code == 201
    generated_id = resp.json()["scan_id"]
    assert generated_id  # non-empty
    assert generated_id != "internal-file-naming-id"

    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == generated_id))
    assert result.scalar_one_or_none() is not None


async def test_initiate_batch_scan_uses_client_supplied_scan_id(
    db, user_client, monkeypatch, tmp_path
):
    scan_file = tmp_path / "batch.pdf"
    scan_file.write_bytes(b"%PDF-1.4 fake\n")

    class _FakeBatchScanService:
        async def scan_batch(self, **kwargs):
            return "internal-batch-id", str(scan_file), 3

    monkeypatch.setattr(scanner_router, "scan_service", _FakeBatchScanService())
    monkeypatch.setattr(
        scanner_router, "get_default_scanner_device", _fake_get_default_scanner_device
    )

    client_id = "22222222-3333-4444-5555-666666666666"
    resp = await user_client.post("/api/scanner/scan/batch", json={"scan_id": client_id})

    assert resp.status_code == 201
    assert resp.json()["scan_id"] == client_id

    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == client_id))
    job = result.scalar_one()
    assert job.page_count == 3


# --------------------------------------------------------------------------- #
# F76: scan.complete / scan.delete audit entries are actually committed
# --------------------------------------------------------------------------- #
async def test_scan_complete_audit_entry_is_persisted(db, user_client, monkeypatch, tmp_path):
    scan_file = tmp_path / "out.pdf"
    scan_file.write_bytes(b"%PDF-1.4 fake\n")
    _patch_scan(monkeypatch, str(scan_file))

    resp = await user_client.post("/api/scanner/scan", json={})
    assert resp.status_code == 201
    scan_id = resp.json()["scan_id"]

    result = await db.execute(
        select(AuditEntry).where(
            AuditEntry.action == "scan.complete", AuditEntry.entity_id == scan_id
        )
    )
    entries = result.scalars().all()
    assert len(entries) == 1


async def test_scan_delete_audit_entry_is_persisted(db, user_client, tmp_path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")
    job = ScanJob(scan_id="scan-del-audit-1", status="completed", filepath=str(f), format="pdf")
    db.add(job)
    await db.commit()

    resp = await user_client.delete("/api/scanner/scans/scan-del-audit-1")
    assert resp.status_code == 204

    result = await db.execute(
        select(AuditEntry).where(
            AuditEntry.action == "scan.delete", AuditEntry.entity_id == "scan-del-audit-1"
        )
    )
    entries = result.scalars().all()
    assert len(entries) == 1


async def test_delete_scan_row_is_gone_even_if_file_cleanup_blows_up(
    db, user_client, tmp_path, monkeypatch
):
    """Regression (F44): the row must be deleted and committed *before* the
    file is unlinked. Forcing cleanup_file to raise proves the delete already
    committed -- the row is gone regardless of what happens to the file."""
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")
    job = ScanJob(scan_id="scan-del-order-1", status="completed", filepath=str(f), format="pdf")
    db.add(job)
    await db.commit()

    def boom(_filepath):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(scanner_router, "cleanup_file", boom)

    # ASGITransport re-raises unhandled exceptions rather than surfacing them
    # as the 500 a real deployment would return -- the row-committed-first
    # behavior is what's under test here, not the response shape.
    with pytest.raises(RuntimeError):
        await user_client.delete("/api/scanner/scans/scan-del-order-1")

    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == "scan-del-order-1"))
    assert result.scalar_one_or_none() is None
