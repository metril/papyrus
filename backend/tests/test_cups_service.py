"""Tests for the async CupsService wrapper and its status cache.

The local dev venv has no pycups; conftest stubs ``sys.modules["cups"]`` with a
MagicMock. These tests therefore never rely on real CUPS behaviour: they
monkeypatch the service's private ``_*_sync`` bodies and, where an IPPError path
is exercised, install a real exception class on the stubbed ``cups`` module.
"""
import cups
import pytest

import app.services.cups_service as cs_module
from app.services.cups_service import CupsService


@pytest.fixture(autouse=True)
def _clear_status_cache():
    """Class-level caches are shared across instances; isolate each test."""
    CupsService._status_cache.clear()
    CupsService._status_locks.clear()
    yield
    CupsService._status_cache.clear()
    CupsService._status_locks.clear()


# ---------------------------------------------------------------------------
# Async wrapper delegation
# ---------------------------------------------------------------------------

async def test_create_held_job_delegates_with_args(monkeypatch):
    svc = CupsService(printer_name="q1")
    captured = {}

    def fake_sync(filepath, title, copies, duplex, media):
        captured.update(
            filepath=filepath, title=title, copies=copies, duplex=duplex, media=media
        )
        return 42

    monkeypatch.setattr(svc, "_create_held_job_sync", fake_sync)

    job_id = await svc.create_held_job(
        filepath="/tmp/a.pdf", title="Doc", copies=3, duplex=True, media="Letter"
    )

    assert job_id == 42
    assert captured == {
        "filepath": "/tmp/a.pdf",
        "title": "Doc",
        "copies": 3,
        "duplex": True,
        "media": "Letter",
    }


async def test_release_job_delegates(monkeypatch):
    svc = CupsService(printer_name="q1")
    seen = {}
    monkeypatch.setattr(svc, "_release_job_sync", lambda job_id: seen.update(id=job_id))
    await svc.release_job(7)
    assert seen == {"id": 7}


async def test_cancel_job_delegates(monkeypatch):
    svc = CupsService(printer_name="q1")
    seen = {}
    monkeypatch.setattr(svc, "_cancel_job_sync", lambda job_id: seen.update(id=job_id))
    await svc.cancel_job(9)
    assert seen == {"id": 9}


async def test_get_printer_options_delegates(monkeypatch):
    svc = CupsService(printer_name="q1")
    monkeypatch.setattr(svc, "_get_printer_options_sync", lambda: {"media_default": "A4"})
    assert await svc.get_printer_options() == {"media_default": "A4"}


async def test_get_job_attributes_delegates(monkeypatch):
    svc = CupsService(printer_name="q1")
    monkeypatch.setattr(svc, "_get_job_attributes_sync", lambda job_id: {"job": job_id})
    assert await svc.get_job_attributes(5) == {"job": 5}


async def test_get_all_jobs_delegates(monkeypatch):
    svc = CupsService(printer_name="q1")
    monkeypatch.setattr(svc, "_get_all_jobs_sync", lambda: {1: {}})
    assert await svc.get_all_jobs() == {1: {}}


# ---------------------------------------------------------------------------
# Status cache behaviour
# ---------------------------------------------------------------------------

def _ok_status():
    return {
        "state": 3,
        "state_message": "idle",
        "accepting_jobs": True,
        "markers": [],
        "state_reasons": [],
    }


async def test_status_cache_returns_cached_within_ttl(monkeypatch):
    svc = CupsService(printer_name="q1")
    clock = {"t": 1000.0}
    monkeypatch.setattr(cs_module.time, "monotonic", lambda: clock["t"])

    calls = {"n": 0}

    def fake_sync():
        calls["n"] += 1
        return _ok_status()

    monkeypatch.setattr(svc, "_get_printer_status_sync", fake_sync)

    first = await svc.get_printer_status()
    assert calls["n"] == 1
    assert first == _ok_status()

    # Within TTL (12s) -> served from cache, no new sync call.
    clock["t"] = 1000.0 + 11.9
    second = await svc.get_printer_status()
    assert calls["n"] == 1
    assert second == first


async def test_status_cache_refreshes_after_ttl(monkeypatch):
    svc = CupsService(printer_name="q1")
    clock = {"t": 500.0}
    monkeypatch.setattr(cs_module.time, "monotonic", lambda: clock["t"])

    calls = {"n": 0}

    def fake_sync():
        calls["n"] += 1
        return _ok_status()

    monkeypatch.setattr(svc, "_get_printer_status_sync", fake_sync)

    await svc.get_printer_status()
    assert calls["n"] == 1

    # Past TTL -> refetch.
    clock["t"] = 500.0 + 12.1
    await svc.get_printer_status()
    assert calls["n"] == 2


async def test_status_cache_keyed_by_queue(monkeypatch):
    monkeypatch.setattr(cs_module.time, "monotonic", lambda: 100.0)
    calls = {"q1": 0, "q2": 0}

    def make_sync(key):
        def _sync():
            calls[key] += 1
            return _ok_status()
        return _sync

    svc1 = CupsService(printer_name="q1")
    svc2 = CupsService(printer_name="q2")
    monkeypatch.setattr(svc1, "_get_printer_status_sync", make_sync("q1"))
    monkeypatch.setattr(svc2, "_get_printer_status_sync", make_sync("q2"))

    await svc1.get_printer_status()
    await svc2.get_printer_status()
    # Distinct keys must not share cache entries.
    assert calls == {"q1": 1, "q2": 1}


async def test_status_error_returns_fallback_and_is_not_cached(monkeypatch):
    # Install a real exception class on the stubbed cups module.
    class FakeIPPError(Exception):
        pass

    monkeypatch.setattr(cups, "IPPError", FakeIPPError, raising=False)

    svc = CupsService(printer_name="q1")
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise cups.IPPError()

    monkeypatch.setattr(svc, "_get_printer_status_sync", boom)

    result = await svc.get_printer_status()
    assert result == {
        "state": 5,
        "state_message": "Printer not found or unreachable",
        "accepting_jobs": False,
        "markers": [],
        "state_reasons": [],
    }
    # Errors are not cached: a second call retries the underlying fetch.
    result2 = await svc.get_printer_status()
    assert calls["n"] == 2
    assert result2["state"] == 5

    # Caller must not be able to mutate the shared fallback constant.
    result["state"] = 999
    assert CupsService._STATUS_FALLBACK["state"] == 5


async def test_concurrent_status_misses_coalesce(monkeypatch):
    """Anti-stampede: concurrent misses for one queue trigger a single fetch."""
    svc = CupsService(printer_name="q1")
    calls = {"n": 0}

    def fake_sync():
        calls["n"] += 1
        return _ok_status()

    monkeypatch.setattr(svc, "_get_printer_status_sync", fake_sync)

    import asyncio

    r1, r2, r3 = await asyncio.gather(
        svc.get_printer_status(),
        svc.get_printer_status(),
        svc.get_printer_status(),
    )
    assert calls["n"] == 1
    assert r1 == r2 == r3 == _ok_status()
