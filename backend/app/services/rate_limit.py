"""In-memory token-bucket rate limiter (process-local, like ``throttle.py``).

Each key owns a bucket holding up to ``capacity`` tokens that refills at
``rpm / 60`` tokens per second. Memory is bounded: the dict is an LRU capped at
``max_keys`` and fully swept of idle (refilled) buckets every ``_SWEEP_EVERY``
calls. ``clock`` is injectable so tests advance time without sleeping.
"""
import math
import time
from collections import OrderedDict


class RateLimiter:
    _SWEEP_EVERY = 1000

    def __init__(self, max_keys: int = 10_000, clock=time.monotonic):
        self.max_keys = max_keys
        self._clock = clock
        # key -> (tokens, last_refill, capacity, rate_per_sec)
        self._buckets: OrderedDict[str, tuple[float, float, float, float]] = OrderedDict()
        self._calls = 0

    def _sweep(self, now: float) -> None:
        idle = [
            k for k, (tokens, last, cap, rate) in self._buckets.items()
            if tokens + (now - last) * rate >= cap
        ]
        for k in idle:
            del self._buckets[k]

    def check(self, key: str, rpm: int) -> float:
        """Consume one token. Returns 0.0 if allowed, else seconds until a
        token is available (the Retry-After value)."""
        now = self._clock()
        capacity = float(max(rpm, 1))
        rate = capacity / 60.0
        self._calls += 1
        if self._calls >= self._SWEEP_EVERY:
            self._calls = 0
            self._sweep(now)

        entry = self._buckets.get(key)
        if entry is None:
            tokens = capacity
        else:
            tokens, last, _cap, _rate = entry
            tokens = min(capacity, tokens + (now - last) * rate)
            self._buckets.move_to_end(key)

        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, now, capacity, rate)
            allowed = 0.0
        else:
            self._buckets[key] = (tokens, now, capacity, rate)
            allowed = (1.0 - tokens) / rate

        while len(self._buckets) > self.max_keys:
            self._buckets.popitem(last=False)
        return allowed

    def clear(self) -> None:
        self._buckets.clear()


def retry_after_seconds(wait: float) -> int:
    return max(1, math.ceil(wait))
