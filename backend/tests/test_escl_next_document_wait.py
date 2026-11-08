"""`NextDocument` holds the request while a scan runs, so clients with a limited
503 retry budget (sane-airscan: about 30) survive long feeder stacks."""
import asyncio

import pytest
from fastapi import HTTPException

from app.routers import escl


class _Request:
    def __init__(self, gone=False):
        self.gone = gone

    async def is_disconnected(self):
        return self.gone


@pytest.fixture
def job(monkeypatch):
    jobs: dict = {}
    monkeypatch.setattr(escl, "_scan_jobs", jobs)
    monkeypatch.setattr(escl, "_purge_stale_jobs", lambda: None)
    return jobs


async def test_waits_for_the_scan_then_serves(job, monkeypatch, tmp_path):
    monkeypatch.setattr(escl, "_NEXT_DOCUMENT_WAIT_SECONDS", 5)
    served = []
    monkeypatch.setattr(escl, "_file_response", lambda j, *a: served.append(j) or "file")

    async def scan():
        await asyncio.sleep(0.05)
        job["j"]["state"] = "Completed"

    job["j"] = {"state": "Processing", "served": False, "task": asyncio.create_task(scan())}
    assert await escl.get_next_document("j", _Request()) == "file"
    assert served


async def test_still_scanning_after_the_wait_is_503_and_scan_survives(job, monkeypatch):
    monkeypatch.setattr(escl, "_NEXT_DOCUMENT_WAIT_SECONDS", 0.01)
    task = asyncio.create_task(asyncio.sleep(30))
    job["j"] = {"state": "Processing", "served": False, "task": task}
    with pytest.raises(HTTPException) as exc:
        await escl.get_next_document("j", _Request())
    assert exc.value.status_code == 503
    assert exc.value.headers == {"Retry-After": "1"}
    assert not task.done()
    task.cancel()


async def test_job_deleted_during_the_wait_is_404(job, monkeypatch):
    monkeypatch.setattr(escl, "_NEXT_DOCUMENT_WAIT_SECONDS", 5)

    async def scan():
        await asyncio.sleep(0.01)
        job.clear()

    job["j"] = {"state": "Processing", "served": False, "task": asyncio.create_task(scan())}
    with pytest.raises(HTTPException) as exc:
        await escl.get_next_document("j", _Request())
    assert exc.value.status_code == 404


async def test_client_gone_after_the_wait_leaves_the_document_for_its_retry(job, monkeypatch):
    monkeypatch.setattr(escl, "_NEXT_DOCUMENT_WAIT_SECONDS", 5)
    monkeypatch.setattr(escl, "_file_response", lambda j, *a: "file")

    async def scan():
        await asyncio.sleep(0.01)
        job["j"]["state"] = "Completed"

    job["j"] = {"state": "Processing", "served": False, "task": asyncio.create_task(scan())}
    response = await escl.get_next_document("j", _Request(gone=True))
    assert response.status_code == 499
    assert job["j"]["served"] is False
    assert await escl.get_next_document("j", _Request()) == "file"
