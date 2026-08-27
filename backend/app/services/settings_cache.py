"""In-process TTL cache for AppConfig-backed settings.

``app.routers.settings.get_setting`` is called several times per request on hot
paths (job submission, scanner status, eSCL) and each call was previously a DB
round-trip. This module provides a tiny cache in front of it: values are keyed
by the *logical* setting name (the argument passed to ``get_setting``, not the
underlying AppConfig row key) and store the value as ``get_setting`` returns it
— i.e. already decrypted for encrypted settings.

Invalidation completeness is the whole point of this cache: every code path
that inserts, updates, or deletes an AppConfig row MUST call ``invalidate()``
for the affected key(s) (or ``invalidate_all()`` when the write touches an
unpredictable or bulk set of keys, or when staleness would be unsafe — e.g.
secrets used for auth) right after the commit. A missed writer means callers
can observe a stale value for up to ``TTL_SECONDS``.

Single-process cache only: this is a bare module-level dict with no locking
or cross-process coordination. It is correct only for the current
single-worker uvicorn deployment. If Papyrus is ever run with multiple
workers/processes, this cache must be replaced with a shared store (e.g.
Redis), since a write in one process would not invalidate the others.
"""

import time

TTL_SECONDS = 30.0

_cache: dict[str, tuple[str | None, float]] = {}

# F130: bumped by every invalidate()/invalidate_all() call. get_setting reads
# this *before* its DB query and skips repopulating the cache if the
# generation moved on while it was awaiting -- otherwise a reader that
# resolved its SELECT just before a concurrent writer's commit could still
# write the pre-commit value into the cache just after that writer's
# invalidate(), serving it stale for up to TTL_SECONDS. See get_generation().
_generation = 0


def get(key: str) -> tuple[bool, str | None]:
    """Look up ``key``. Returns ``(hit, value)``; ``hit`` is False on miss or expiry."""
    entry = _cache.get(key)
    if entry is None:
        return False, None
    value, expires_at = entry
    if time.monotonic() >= expires_at:
        _cache.pop(key, None)
        return False, None
    return True, value


def put(key: str, value: str | None) -> None:
    """Store ``value`` for ``key``, valid for ``TTL_SECONDS`` from now."""
    _cache[key] = (value, time.monotonic() + TTL_SECONDS)


def get_generation() -> int:
    """Current invalidation generation. Capture this before a DB read and
    compare it after: if it changed, an invalidate() ran concurrently and the
    value just read may already be stale (F130) -- skip caching it."""
    return _generation


def invalidate(key: str) -> None:
    """Evict a single key. Call after writing that key's AppConfig row."""
    global _generation
    _generation += 1
    _cache.pop(key, None)


def invalidate_all() -> None:
    """Evict every cached key.

    Use after a write whose affected keys aren't precisely known (e.g. backup
    restore) or whenever serving a stale value for up to ``TTL_SECONDS`` would
    be unsafe.
    """
    global _generation
    _generation += 1
    _cache.clear()
