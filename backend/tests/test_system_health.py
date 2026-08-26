"""Tests for the F51 fix: `/api/system/health` is unauthenticated, so
without caching a burst of anonymous requests can fork a `scanimage`
subprocess (and open a CUPS connection) per request, exhausting the shared
`asyncio.to_thread` executor and starving every other blocking call routed
through it (CUPS status, release, thumbnails, ...).

`_probe_subsystems()` is the extracted, cached, single-flight probe; these
tests exercise it directly (no ASGI client / DB needed) by monkeypatching
`asyncio.create_subprocess_exec` and counting invocations, monkeypatching
`time.monotonic` to control cache expiry, running probes concurrently via
`asyncio.gather` to prove the lock serializes a burst rather than just a
TTL window closing sequential polling, and simulating a timed-out child to
prove it gets `kill()`ed and `wait()`ed rather than leaked.
"""
import asyncio

from app.routers import system as system_router


class _FakeProcess:
    async def communicate(self):
        return b"device test:libusb:001:002 test\n", b""


def _install_fake_subprocess(monkeypatch):
    calls = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append(args)
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    return calls


async def test_two_probes_within_ttl_spawn_only_one_subprocess(monkeypatch):
    system_router._reset_health_cache()
    calls = _install_fake_subprocess(monkeypatch)

    first = await system_router._probe_subsystems()
    second = await system_router._probe_subsystems()

    assert len(calls) == 1
    assert first == second == (True, True)


async def test_probe_result_is_cached_verbatim_across_calls(monkeypatch):
    system_router._reset_health_cache()
    _install_fake_subprocess(monkeypatch)

    cups_ok, scanner_ok = await system_router._probe_subsystems()

    assert cups_ok is True  # `cups` module is stubbed as a MagicMock in conftest
    assert scanner_ok is True


async def test_probe_re_runs_after_the_cache_ttl_expires(monkeypatch):
    system_router._reset_health_cache()
    calls = _install_fake_subprocess(monkeypatch)

    fake_now = [1_000.0]
    monkeypatch.setattr(system_router.time, "monotonic", lambda: fake_now[0])

    await system_router._probe_subsystems()
    assert len(calls) == 1

    fake_now[0] += system_router._HEALTH_CACHE_TTL_SECONDS - 1
    await system_router._probe_subsystems()
    assert len(calls) == 1  # still within TTL

    fake_now[0] += 2  # now past the TTL from the first probe
    await system_router._probe_subsystems()
    assert len(calls) == 2


async def test_reset_health_cache_forces_a_fresh_probe(monkeypatch):
    system_router._reset_health_cache()
    calls = _install_fake_subprocess(monkeypatch)

    await system_router._probe_subsystems()
    assert len(calls) == 1

    system_router._reset_health_cache()
    await system_router._probe_subsystems()
    assert len(calls) == 2


async def test_scanner_probe_failure_is_cached_as_not_ok(monkeypatch):
    system_router._reset_health_cache()

    async def failing_subprocess_exec(*args, **kwargs):
        raise FileNotFoundError("scanimage not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failing_subprocess_exec)

    cups_ok, scanner_ok = await system_router._probe_subsystems()

    assert scanner_ok is False


async def test_concurrent_probes_within_ttl_spawn_only_one_subprocess(monkeypatch):
    """A TTL cache alone only closes the *sequential* polling loophole — the
    audit's literal failure scenario is a burst of *concurrent* anonymous
    GETs, all arriving before the first probe has finished and written the
    cache. `_health_probe_lock` must single-flight that burst: only the
    caller that wins the lock forks `scanimage`; everyone else re-checks the
    (now warm) cache after acquiring the lock instead of forking their own.

    `create_subprocess_exec` sleeps briefly so the other 24 gathered callers
    genuinely queue up on the lock while the first is mid-probe, rather than
    the test passing by accident because nothing ever yields control.
    """
    system_router._reset_health_cache()
    calls = []

    async def slow_create_subprocess_exec(*args, **kwargs):
        calls.append(args)
        await asyncio.sleep(0.05)
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_create_subprocess_exec)

    results = await asyncio.gather(*(system_router._probe_subsystems() for _ in range(25)))

    assert len(calls) == 1
    assert all(result == (True, True) for result in results)


class _TimingOutProcess:
    """Simulates a `scanimage -L` child whose `communicate()` never returns
    before the 5s deadline. Raising `asyncio.TimeoutError` directly from
    `communicate()` is the exact exception `asyncio.wait_for` raises on a
    real timeout, so the surrounding `except asyncio.TimeoutError:` handling
    is exercised identically — without the test actually waiting 5s.
    """

    def __init__(self):
        self.killed = False
        self.waited = False

    async def communicate(self):
        raise asyncio.TimeoutError()

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True


async def test_scanner_probe_timeout_kills_and_awaits_the_child(monkeypatch):
    system_router._reset_health_cache()
    proc = _TimingOutProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    cups_ok, scanner_ok = await system_router._probe_subsystems()

    assert proc.killed is True
    assert proc.waited is True
    assert scanner_ok is False
