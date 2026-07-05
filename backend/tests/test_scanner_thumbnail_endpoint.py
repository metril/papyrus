"""Tests for the GET /scanner/scans/{scan_id}/thumbnail endpoint's 404 handling.

This codebase's router tests call the endpoint function directly with a
fake AsyncSession stand-in rather than spinning up a TestClient (see
test_settings_cache.py's `_CountingSession` for precedent).
"""
import pytest
from fastapi import HTTPException

from app.routers.scanner import get_scan_thumbnail


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, value):
        self._value = value

    async def execute(self, _query):
        return _FakeResult(self._value)


class _FakeJob:
    def __init__(self, status="completed", filepath=None, format="png"):
        self.status = status
        self.filepath = filepath
        self.format = format


async def test_thumbnail_404_when_scan_not_found():
    db = _FakeSession(None)

    with pytest.raises(HTTPException) as exc_info:
        await get_scan_thumbnail("missing-scan-id", user=None, db=db)

    assert exc_info.value.status_code == 404


async def test_thumbnail_404_when_scan_not_completed():
    job = _FakeJob(status="scanning", filepath=None)
    db = _FakeSession(job)

    with pytest.raises(HTTPException) as exc_info:
        await get_scan_thumbnail("scan-id", user=None, db=db)

    assert exc_info.value.status_code == 404


async def test_thumbnail_404_when_file_missing_on_disk(tmp_path):
    job = _FakeJob(status="completed", filepath=str(tmp_path / "gone.png"))
    db = _FakeSession(job)

    with pytest.raises(HTTPException) as exc_info:
        await get_scan_thumbnail("scan-id", user=None, db=db)

    assert exc_info.value.status_code == 404
