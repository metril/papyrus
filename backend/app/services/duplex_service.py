"""Interleave a front-side feeder PDF with a separately scanned back-side PDF.

The feeder is single-sided: fronts are scanned first, the stack is flipped,
and the backs are scanned second, so they arrive in reverse order. The merged
document is f1, b_n, f2, b_(n-1), ...
"""

import io
import os
import stat
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
from PIL import Image
from pypdf import PageObject, PdfReader, PdfWriter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ScanJob

# A back page is dropped only if it is bright, flat paper with almost no
# darker marks. Losing real content is far worse than keeping a blank page,
# so both limits err on the side of keeping.
BLANK_ANALYSIS_MAX_DIM = 400
BLANK_MARGIN_FRACTION = 0.03  # headers, footers and page numbers sit just inside this
BLANK_MIN_PAPER_LEVEL = 150  # median brightness (0-255) below this is never blank
BLANK_INK_DELTA = 25  # a pixel this much darker than the paper counts as ink
# A lone "Page 2" at 150 dpi covers ~0.04% of the page and noise covers 0%,
# so the cutoff sits at a few stray pixels.
BLANK_MAX_INK_FRACTION = 0.00002


class PageCountMismatchError(Exception):
    def __init__(self, fronts: int, backs: int):
        self.fronts = fronts
        self.backs = backs
        super().__init__(f"{fronts} front pages but {backs} back pages")


@dataclass
class MergeResult:
    page_count: int
    blanks_dropped: int


def _page_image(page: PageObject) -> Image.Image | None:
    try:
        images = page.images
        if len(images) == 0:
            return None
        return Image.open(io.BytesIO(images[0].data))
    except Exception:
        return None


def is_blank_image(img: Image.Image) -> bool:
    gray = img.convert("L")
    gray.thumbnail((BLANK_ANALYSIS_MAX_DIM, BLANK_ANALYSIS_MAX_DIM), Image.BOX)
    pixels = np.asarray(gray, dtype=np.int16)
    h, w = pixels.shape
    my, mx = int(h * BLANK_MARGIN_FRACTION), int(w * BLANK_MARGIN_FRACTION)
    inner = pixels[my:h - my, mx:w - mx]
    if inner.size == 0:
        return False
    paper = float(np.median(inner))
    if paper < BLANK_MIN_PAPER_LEVEL:
        return False
    ink = np.count_nonzero(inner < paper - BLANK_INK_DELTA)
    return ink / inner.size <= BLANK_MAX_INK_FRACTION


def is_blank_page(page: PageObject) -> bool:
    """True only when the page's embedded image is confidently blank; a page
    with no readable image is never treated as blank."""
    img = _page_image(page)
    if img is None:
        return False
    try:
        return is_blank_image(img)
    finally:
        img.close()


def merge_backs(
    front_path: str,
    back_path: str,
    out_path: str,
    *,
    rotate_backs: bool = False,
    skip_blank_backs: bool = False,
    reverse_backs: bool = True,
) -> MergeResult:
    """Write the interleaved document to ``out_path``.

    ``reverse_backs`` is true when the stack was flipped, so backs arrive
    last-sheet-first.

    Raises ``PageCountMismatchError`` before writing anything if the counts differ.
    """
    fronts = PdfReader(front_path)
    backs = PdfReader(back_path)
    n = len(fronts.pages)
    if n != len(backs.pages):
        raise PageCountMismatchError(n, len(backs.pages))

    writer = PdfWriter()
    dropped = 0
    try:
        for i in range(n):
            writer.add_page(fronts.pages[i])
            back = backs.pages[n - 1 - i if reverse_backs else i]
            if skip_blank_backs and is_blank_page(back):
                dropped += 1
                continue
            added = writer.add_page(back)
            if rotate_backs:
                added.rotate(180)
        with open(out_path, "wb") as f:
            writer.write(f)
    finally:
        writer.close()
    return MergeResult(page_count=2 * n - dropped, blanks_dropped=dropped)


def _fsync_path(path: str, mode_from: str | None = None) -> None:
    fd = os.open(path, os.O_RDWR)
    try:
        if mode_from:
            os.fchmod(fd, stat.S_IMODE(os.stat(mode_from).st_mode))
        os.fsync(fd)
    finally:
        os.close(fd)


def merge_backs_atomic(
    front_path: str,
    back_path: str,
    out_path: str,
    *,
    rotate_backs: bool = False,
    skip_blank_backs: bool = False,
    reverse_backs: bool = True,
) -> MergeResult:
    """Merge into a temp file beside ``out_path``, give it the fronts' file
    mode, sync it to disk, then swap it in, so a crash or failure never leaves
    a partial ``out_path``."""
    directory = os.path.dirname(out_path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".duplex_", suffix=".pdf")
    os.close(fd)
    try:
        result = merge_backs(
            front_path, back_path, tmp_path,
            rotate_backs=rotate_backs, skip_blank_backs=skip_blank_backs,
            reverse_backs=reverse_backs,
        )
        _fsync_path(tmp_path, mode_from=front_path)
        os.replace(tmp_path, out_path)
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return result
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


async def release_fronts(db: AsyncSession, deleting: Iterable[ScanJob]) -> list[ScanJob]:
    """Clear ``duplex_merged`` on the fronts of any merge result being deleted,
    so they can be merged again. Call before the commit that deletes the rows;
    returns the fronts rows that changed (for broadcasting)."""
    rows = list(deleting)
    gone = {r.scan_id for r in rows}
    sources = {
        r.duplex_source_scan_id for r in rows if r.duplex_merged and r.duplex_source_scan_id
    } - gone
    freed: list[ScanJob] = []
    for source in sources:
        other = await db.execute(
            select(ScanJob.id).where(
                ScanJob.duplex_merged.is_(True),
                ScanJob.duplex_source_scan_id == source,
                ScanJob.scan_id.notin_(gone),
            ).limit(1)
        )
        if other.first() is not None:
            continue
        result = await db.execute(
            select(ScanJob).where(ScanJob.scan_id == source, ScanJob.duplex_merged.is_(True))
        )
        fronts = result.scalar_one_or_none()
        if fronts is not None:
            fronts.duplex_merged = False
            freed.append(fronts)
    return freed
