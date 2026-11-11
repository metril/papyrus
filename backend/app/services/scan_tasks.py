"""In-process registries for cancellable scans and copies.

Single-process only (like the other in-memory caches here): a restart drops
every entry, which is why the cancel route also handles a "scanning" row with
no registered task (a stale row).
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

COPY_TTL_SECONDS = 600
TERMINAL_PHASES = frozenset({"done", "failed", "cancelled"})

# Rows still `scanning` that were created before this are leftovers from a previous process.
PROCESS_START = datetime.now(timezone.utc)

_scans: dict[str, asyncio.Task] = {}
_progress: dict[str, float] = {}
_pages: dict[str, int] = {}
# Reserved ids (value = cancel requested before the task started); held until the request ends.
_reserved: dict[str, bool] = {}


def reserve(scan_id: str) -> None:
    """Mark `scan_id` as live from before its row is committed until `unregister`."""
    _reserved[scan_id] = False


def is_reserved(scan_id: str) -> bool:
    return scan_id in _reserved or scan_id in _scans


def cancel_requested(scan_id: str) -> bool:
    return _reserved.get(scan_id, False)


def task_done(scan_id: str) -> None:
    _scans.pop(scan_id, None)


def register(scan_id: str, task: asyncio.Task) -> None:
    _scans[scan_id] = task


def unregister(scan_id: str) -> None:
    _scans.pop(scan_id, None)
    _reserved.pop(scan_id, None)
    _progress.pop(scan_id, None)
    _pages.pop(scan_id, None)


def is_registered(scan_id: str) -> bool:
    return scan_id in _scans


def cancel(scan_id: str) -> bool:
    """Cancel a live scan; True when `scan_id` is running or reserved (flagged if not started)."""
    task = _scans.get(scan_id)
    if scan_id in _reserved:
        _reserved[scan_id] = True
    if task is not None and not task.done():
        task.cancel()
        return True
    return scan_id in _reserved


def set_progress(scan_id: str, pct: float) -> None:
    if is_reserved(scan_id):
        _progress[scan_id] = pct


def set_pages(scan_id: str, n: int) -> None:
    if is_reserved(scan_id):
        _pages[scan_id] = n


def snapshot(scan_id: str) -> tuple[int | None, int | None]:
    """(progress_pct, page_count) for a running scan; (None, None) otherwise."""
    if not is_reserved(scan_id):
        return None, None
    pct = _progress.get(scan_id)
    return (None if pct is None else int(pct)), _pages.get(scan_id)


@dataclass
class CopyEntry:
    copy_id: str
    owner_id: int | None
    phase: str = "scanning"
    progress_pct: int | None = None
    scan_id: str | None = None
    job_id: int | None = None
    cups_job_id: int | None = None
    cancel_requested: bool = False
    task: asyncio.Task | None = None
    finished_at: float | None = None
    extra: dict = field(default_factory=dict)


_copies: dict[str, CopyEntry] = {}


def _prune() -> None:
    now = time.monotonic()
    for cid in [
        c for c, e in _copies.items()
        if e.finished_at is not None and now - e.finished_at > COPY_TTL_SECONDS
    ]:
        del _copies[cid]


def register_copy(copy_id: str | None, owner_id: int | None) -> CopyEntry | None:
    """Create an entry; None when `copy_id` is already registered."""
    _prune()
    cid = copy_id or str(uuid.uuid4())
    if cid in _copies:
        return None
    entry = CopyEntry(copy_id=cid, owner_id=owner_id)
    _copies[cid] = entry
    return entry


def get_copy(copy_id: str) -> CopyEntry | None:
    _prune()
    return _copies.get(copy_id)


def finish_copy(entry: CopyEntry, phase: str) -> None:
    if entry.phase in TERMINAL_PHASES:
        return
    entry.phase = phase
    entry.finished_at = time.monotonic()
    entry.task = None
    if phase == "done":
        entry.progress_pct = 100
