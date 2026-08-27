"""F41: every scanimage/brsaneconfig4/airscan-discover subprocess in
``app.routers.scanners`` is killed and awaited (not just abandoned) on
timeout, and a couple of previously-timeoutless call sites
(``discover_scanners``) get one at all.

Endpoints are called directly (bypassing HTTP/auth, mirroring
test_escl_job_eviction.py's convention for router functions) with
``asyncio.create_subprocess_exec`` faked per test -- no real subprocess ever
runs. This is deliberately not exhaustive over every call site (six in this
file); it covers one representative "raises a domain error" site
(register_brscan4, discover_scanners) and one "degrades into a response
field" site (scanner_diagnostics, probe's airscan-discover fallback) since
those two behaviors are handled differently.
"""
import asyncio

import pytest

from app.exceptions import ScannerBusyError
from app.routers import scanners as scanners_router
from app.routers.scanners import Brscan4Register


class _TimingOutProcess:
    """Mirrors test_system_health.py's ``_TimingOutProcess``: raising
    ``asyncio.TimeoutError`` directly from ``communicate()`` is exactly what
    ``asyncio.wait_for`` raises on a real timeout, without the test actually
    waiting out a real one."""

    def __init__(self):
        self.killed = False
        self.waited = False

    async def communicate(self):
        raise asyncio.TimeoutError()

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True


async def test_register_brscan4_timeout_kills_awaits_and_raises(monkeypatch):
    proc = _TimingOutProcess()

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ScannerBusyError):
        await scanners_router.register_brscan4(
            Brscan4Register(name="Brother", model="DCP-L2540DW", ip="10.0.0.5"),
            _user=None,
        )

    assert proc.killed is True
    assert proc.waited is True


async def test_discover_scanners_timeout_kills_awaits_and_raises(monkeypatch):
    proc = _TimingOutProcess()

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ScannerBusyError):
        await scanners_router.discover_scanners(_user=None)

    assert proc.killed is True
    assert proc.waited is True


async def test_scanner_diagnostics_scanimage_timeout_kills_and_degrades_gracefully(
    monkeypatch,
):
    """Diagnostics is a best-effort admin report -- a timed-out probe must
    still kill the child, but degrades into an error string in the response
    rather than raising and failing the whole diagnostics call."""
    scanimage_proc = _TimingOutProcess()

    class _OkProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    calls = {"n": 0}

    async def fake_exec(*args, **kwargs):
        calls["n"] += 1
        if args[0] == "scanimage":
            return scanimage_proc
        return _OkProcess()  # airscan-discover

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await scanners_router.scanner_diagnostics(_user=None)

    assert scanimage_proc.killed is True
    assert scanimage_proc.waited is True
    assert "timed out" in result["scanimage_list"]


async def test_probe_airscan_discover_timeout_kills_and_falls_through(monkeypatch):
    """probe_scanner_ip's airscan-discover step has a multi-stage fallback
    chain (eSCL probing, then WSD port check) -- a timeout there must kill
    the child and continue to the next stage rather than raising."""
    discover_proc = _TimingOutProcess()

    async def fake_exec(*args, **kwargs):
        if args[0] == "airscan-discover":
            return discover_proc
        raise AssertionError("should not spawn any other subprocess")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    async def fake_fetch_capped(url):
        raise OSError("connection refused")  # eSCL probing also fails

    monkeypatch.setattr(scanners_router, "_fetch_capped", fake_fetch_capped)

    async def fake_run_in_executor(_executor, func):
        return False  # WSD port check: host unreachable

    monkeypatch.setattr(
        asyncio.get_event_loop(), "run_in_executor", fake_run_in_executor
    )

    result = await scanners_router.probe_scanner_ip(ip="10.0.0.9", _user=None)

    assert discover_proc.killed is True
    assert discover_proc.waited is True
    assert result["reachable"] is False


# --------------------------------------------------------------------------- #
# F108: capped response body for the scanner probe's HTTP fetches
# --------------------------------------------------------------------------- #
class _FakeStreamResponse:
    def __init__(self, chunks: list[bytes], content_length: str | None):
        self._chunks = chunks
        self.headers = {"content-length": content_length} if content_length else {}

    def raise_for_status(self):
        pass

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeStreamingClient:
    def __init__(self, response: _FakeStreamResponse):
        self._response = response

    def stream(self, method, url, timeout=None):
        return self._response


async def test_fetch_capped_rejects_declared_content_length_over_cap(monkeypatch):
    oversized = str(scanners_router._MAX_PROBE_RESPONSE_BYTES + 1)
    resp = _FakeStreamResponse(chunks=[b"x" * 100], content_length=oversized)
    monkeypatch.setattr(
        "app.services.http_client.get_http_client", lambda: _FakeStreamingClient(resp)
    )

    with pytest.raises(scanners_router._ProbeResponseTooLargeError):
        await scanners_router._fetch_capped("http://10.0.0.9/eSCL/ScannerCapabilities")


async def test_fetch_capped_rejects_body_exceeding_cap_with_no_content_length(monkeypatch):
    """A response that lies about (or omits) Content-Length but streams an
    oversized body must still be caught -- capped by bytes actually read,
    not just the declared header."""
    big_chunk = b"x" * (scanners_router._MAX_PROBE_RESPONSE_BYTES + 1)
    resp = _FakeStreamResponse(chunks=[big_chunk], content_length=None)
    monkeypatch.setattr(
        "app.services.http_client.get_http_client", lambda: _FakeStreamingClient(resp)
    )

    with pytest.raises(scanners_router._ProbeResponseTooLargeError):
        await scanners_router._fetch_capped("http://10.0.0.9/eSCL/ScannerCapabilities")


async def test_fetch_capped_returns_body_under_cap(monkeypatch):
    resp = _FakeStreamResponse(chunks=[b"<xml/>"], content_length="6")
    monkeypatch.setattr(
        "app.services.http_client.get_http_client", lambda: _FakeStreamingClient(resp)
    )

    result = await scanners_router._fetch_capped("http://10.0.0.9/eSCL/ScannerCapabilities")

    assert result == b"<xml/>"


async def test_probe_scanner_ip_rejects_oversized_escl_response(monkeypatch):
    """End-to-end through probe_scanner_ip: an oversized ScannerCapabilities
    response must not be accepted as a valid device -- it should fall
    through to the next candidate URL/fallback instead of crashing or
    buffering the whole thing."""
    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError("airscan-discover not installed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    async def fake_fetch_capped(url):
        raise scanners_router._ProbeResponseTooLargeError(url)

    monkeypatch.setattr(scanners_router, "_fetch_capped", fake_fetch_capped)

    async def fake_run_in_executor(_executor, func):
        return False

    monkeypatch.setattr(
        asyncio.get_event_loop(), "run_in_executor", fake_run_in_executor
    )

    result = await scanners_router.probe_scanner_ip(ip="10.0.0.9", _user=None)

    assert result["reachable"] is False
    assert "too large" in result["error"]
