"""In-memory brute-force throttle.

Process-local and intentionally simple: a plain dict keyed by an arbitrary
string (e.g. ``f"{ip}:{username}"`` for login, ``f"pin:{job_id}"`` for a
release PIN), tracking a failure count per key within a fixed lockout
window. Correct only for a single-process deployment — that matches how
Papyrus is deployed, so no cross-process/shared-cache backing is needed.
"""
import time
from dataclasses import dataclass

from app.exceptions import TooManyAttemptsError


@dataclass
class _Entry:
    count: int
    window_start: float


class Throttle:
    """Tracks failed attempts per key and locks the key out once
    ``max_failures`` failures have been recorded within ``lockout_seconds``.

    ``clock`` defaults to ``time.monotonic`` but can be injected (e.g. a fake
    callable) so tests can advance time without sleeping.
    """

    # record_failure calls between periodic full sweeps — see _sweep_expired.
    _SWEEP_EVERY = 1000

    def __init__(self, max_failures: int, lockout_seconds: float, clock=time.monotonic):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._failures_since_sweep = 0

    def _current_count(self, key: str) -> int:
        """Return the live failure count for `key`, evicting it first if its
        window has expired."""
        entry = self._entries.get(key)
        if entry is None:
            return 0
        if self._clock() - entry.window_start > self.lockout_seconds:
            del self._entries[key]
            return 0
        return entry.count

    def _sweep_expired(self) -> None:
        """Drop every entry whose window has expired.

        `_current_count` only evicts a key's *own* stale entry, and only
        when that same key is checked/recorded again. A caller that never
        revisits a key — e.g. local-login's key embeds the attempted
        username, so a script rotating through distinct usernames leaves one
        `_Entry` behind per username — would otherwise accumulate entries
        forever. Called periodically from `record_failure` (the only method
        that grows the dict) rather than on every call, so the common case
        stays O(1).
        """
        now = self._clock()
        expired = [
            key for key, entry in self._entries.items()
            if now - entry.window_start > self.lockout_seconds
        ]
        for key in expired:
            del self._entries[key]

    def check(self, key: str) -> None:
        """Raise TooManyAttemptsError if `key` is currently locked out."""
        if self._current_count(key) >= self.max_failures:
            raise TooManyAttemptsError("Too many attempts. Try again later.")

    def record_failure(self, key: str) -> None:
        """Record one more failure for `key`, starting a fresh window if the
        previous one had expired."""
        now = self._clock()
        entry = self._entries.get(key)
        if entry is None or now - entry.window_start > self.lockout_seconds:
            self._entries[key] = _Entry(count=1, window_start=now)
        else:
            entry.count += 1

        self._failures_since_sweep += 1
        if self._failures_since_sweep >= self._SWEEP_EVERY:
            self._failures_since_sweep = 0
            self._sweep_expired()

    def reset(self, key: str) -> None:
        """Clear any recorded failures for `key` (e.g. on a successful attempt)."""
        self._entries.pop(key, None)
