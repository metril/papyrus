"""Interleave a front-side feeder PDF with a separately scanned back-side PDF.

The feeder is single-sided: fronts are scanned first, the stack is flipped,
and the backs are scanned second, so they arrive in reverse order. The merged
document is f1, b_n, f2, b_(n-1), ...
"""

import io
import os
import tempfile
from dataclasses import dataclass

import numpy as np
from PIL import Image
from pypdf import PageObject, PdfReader, PdfWriter

# A back page is dropped only if it is bright, flat paper with almost no
# darker marks. Losing real content is far worse than keeping a blank page,
# so both limits err on the side of keeping.
BLANK_ANALYSIS_MAX_DIM = 400
BLANK_MARGIN_FRACTION = 0.08
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
    skip_blank_backs: bool = True,
) -> MergeResult:
    """Write the interleaved document to ``out_path``.

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
            back = backs.pages[n - 1 - i]
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


def merge_backs_in_place(
    front_path: str,
    back_path: str,
    *,
    rotate_backs: bool = False,
    skip_blank_backs: bool = True,
) -> MergeResult:
    """Merge into a temp file beside ``front_path`` and swap it in atomically,
    so any failure leaves the original untouched."""
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(front_path) or ".", prefix=".duplex_", suffix=".pdf"
    )
    os.close(fd)
    try:
        result = merge_backs(
            front_path, back_path, tmp_path,
            rotate_backs=rotate_backs, skip_blank_backs=skip_blank_backs,
        )
        os.replace(tmp_path, front_path)
        return result
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
