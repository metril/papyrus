"""Per-path ``asyncio.Lock`` registry for serializing in-place file rewrites.

``ocr_service.apply_ocr`` and ``image_service.enhance``/``deskew`` both
rewrite a scan file in place. Nothing previously stopped two of those
rewrites (manual OCR vs. auto-deliver OCR, or two overlapping enhance
requests) from racing on the same path — see F12. ``lock_for(path)`` hands
back a single shared ``asyncio.Lock`` per path so callers can serialize with
``async with lock_for(path):``.

Single-process only, like ``settings_cache`` — a bare module-level dict with
no cross-process coordination, correct only for the current single-worker
deployment. Locks are never removed once created (paths are finite in
practice — one per scan file — and the alternative, refcounting them away,
isn't worth the complexity for this workload).
"""

import asyncio

_locks: dict[str, asyncio.Lock] = {}


def lock_for(path: str) -> asyncio.Lock:
    """Return the shared lock for ``path``, creating it on first use."""
    lock = _locks.get(path)
    if lock is None:
        lock = asyncio.Lock()
        _locks[path] = lock
    return lock
