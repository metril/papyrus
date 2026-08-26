"""Unit tests for the in-memory brute-force throttle.

Time is controlled via an injected `clock` callable (a mutable box the test
advances directly) rather than sleeping — these must run instantly.
"""
import pytest

from app.exceptions import TooManyAttemptsError
from app.services.throttle import Throttle


class _FakeClock:
    """A monotonic-like clock the test can advance on demand."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _throttle(max_failures=5, lockout_seconds=300, clock=None):
    return Throttle(max_failures, lockout_seconds, clock=clock or _FakeClock())


def test_check_does_not_raise_when_no_failures_recorded():
    t = _throttle()
    t.check("k")  # must not raise


def test_check_does_not_raise_below_max_failures():
    t = _throttle(max_failures=5)
    for _ in range(4):
        t.record_failure("k")
    t.check("k")  # 4 < 5, still allowed


def test_check_raises_at_max_failures():
    t = _throttle(max_failures=5)
    for _ in range(5):
        t.record_failure("k")
    with pytest.raises(TooManyAttemptsError) as exc_info:
        t.check("k")
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail  # curated, user-facing text


def test_failures_are_scoped_per_key():
    t = _throttle(max_failures=5)
    for _ in range(5):
        t.record_failure("attacker")
    t.check("someone-else")  # a different key is unaffected


def test_reset_clears_recorded_failures():
    t = _throttle(max_failures=5)
    for _ in range(5):
        t.record_failure("k")
    t.reset("k")
    t.check("k")  # must not raise after reset


def test_expired_failures_are_evicted_after_lockout_window():
    clock = _FakeClock()
    t = _throttle(max_failures=5, lockout_seconds=300, clock=clock)
    for _ in range(5):
        t.record_failure("k")
    with pytest.raises(TooManyAttemptsError):
        t.check("k")

    clock.advance(301)  # past the lockout window
    t.check("k")  # expired failures no longer count


def test_record_failure_after_partial_expiry_does_not_immediately_lock():
    """A single fresh failure after the old ones expired should not trip the
    throttle by itself."""
    clock = _FakeClock()
    t = _throttle(max_failures=5, lockout_seconds=300, clock=clock)
    for _ in range(5):
        t.record_failure("k")

    clock.advance(301)
    t.record_failure("k")
    t.check("k")  # only 1 failure in the current window


def test_stale_entries_across_many_distinct_keys_are_swept_periodically():
    """Regression: `_current_count` only evicts a key's own stale entry when
    that same key is checked/recorded again. An attacker rotating through
    distinct keys (e.g. many different usernames against local-login, since
    the throttle key embeds the username) never revisits a key, so nothing
    ever swept the resulting entries — unbounded growth. A periodic sweep
    (every `_SWEEP_EVERY` record_failure calls) must reclaim them."""
    clock = _FakeClock()
    t = _throttle(max_failures=5, lockout_seconds=300, clock=clock)

    for i in range(Throttle._SWEEP_EVERY - 1):
        t.record_failure(f"key-{i}")
    assert len(t._entries) == Throttle._SWEEP_EVERY - 1  # nothing swept yet

    clock.advance(301)  # every existing entry's window has now expired

    # This call crosses the sweep threshold; it should reclaim every expired
    # entry, leaving only the fresh one just recorded by this same call.
    t.record_failure("trigger-sweep")
    assert len(t._entries) == 1
    assert "trigger-sweep" in t._entries
