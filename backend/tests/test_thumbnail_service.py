"""Tests for the scan thumbnail generation/caching service."""
import os
import shutil

import pytest
from PIL import Image

from app.services.thumbnail_service import (
    THUMBNAIL_MAX_DIM,
    ThumbnailError,
    get_or_create_thumbnail,
    invalidate_thumbnail,
)


def _make_image(path: str, size: tuple[int, int] = (1000, 1500), color=(200, 100, 50)) -> None:
    Image.new("RGB", size, color=color).save(path)


async def test_creates_thumbnail_for_image(tmp_path):
    src = tmp_path / "scan.png"
    _make_image(str(src))

    thumb_path = await get_or_create_thumbnail(str(src))

    assert thumb_path == str(src) + ".thumb.jpg"
    assert os.path.exists(thumb_path)


async def test_thumbnail_long_edge_capped_at_max_dimension(tmp_path):
    src = tmp_path / "scan.png"
    _make_image(str(src), size=(2000, 1000))

    thumb_path = await get_or_create_thumbnail(str(src))

    with Image.open(thumb_path) as thumb:
        assert thumb.size[0] == THUMBNAIL_MAX_DIM  # long edge (width) hits the cap exactly
        assert thumb.size[1] < THUMBNAIL_MAX_DIM  # aspect ratio preserved
        assert max(thumb.size) <= THUMBNAIL_MAX_DIM


async def test_second_call_is_cached_not_regenerated(tmp_path):
    src = tmp_path / "scan.jpg"
    _make_image(str(src))

    first_path = await get_or_create_thumbnail(str(src))
    first_mtime = os.path.getmtime(first_path)

    second_path = await get_or_create_thumbnail(str(src))
    second_mtime = os.path.getmtime(second_path)

    assert second_path == first_path
    assert second_mtime == first_mtime  # not rewritten on the cached call


async def test_stale_thumbnail_regenerated_when_source_is_newer(tmp_path):
    src = tmp_path / "scan.jpg"
    _make_image(str(src), size=(400, 400), color=(10, 10, 10))

    thumb_path = await get_or_create_thumbnail(str(src))
    stale_bytes = open(thumb_path, "rb").read()

    # Simulate an in-place rewrite (e.g. enhance/deskew) that changes both the
    # pixel content and bumps the source file's mtime forward.
    _make_image(str(src), size=(1600, 1600), color=(250, 250, 250))
    newer = os.path.getmtime(src) + 5
    os.utime(str(src), (newer, newer))

    regenerated_path = await get_or_create_thumbnail(str(src))
    with Image.open(regenerated_path) as thumb:
        assert max(thumb.size) == THUMBNAIL_MAX_DIM
    assert open(regenerated_path, "rb").read() != stale_bytes


async def test_invalidate_thumbnail_forces_regeneration_even_within_same_mtime(tmp_path):
    """Belt-and-suspenders: enhance/OCR endpoints call invalidate_thumbnail
    explicitly rather than relying solely on mtime, since a rewrite can land
    within the same filesystem mtime tick as the original thumbnail."""
    src = tmp_path / "scan.jpg"
    _make_image(str(src), size=(400, 400), color=(10, 10, 10))

    thumb_path = await get_or_create_thumbnail(str(src))
    assert os.path.exists(thumb_path)

    invalidate_thumbnail(str(src))
    assert not os.path.exists(thumb_path)

    # Regenerates cleanly after invalidation.
    regenerated_path = await get_or_create_thumbnail(str(src))
    assert os.path.exists(regenerated_path)


def test_invalidate_thumbnail_is_a_no_op_when_missing(tmp_path):
    src = tmp_path / "scan.jpg"
    invalidate_thumbnail(str(src))  # no thumbnail, no source file — must not raise
    invalidate_thumbnail(None)


async def test_missing_source_file_raises_file_not_found(tmp_path):
    missing = tmp_path / "nope.png"
    with pytest.raises(FileNotFoundError):
        await get_or_create_thumbnail(str(missing))


async def test_unsupported_extension_raises_thumbnail_error(tmp_path):
    src = tmp_path / "scan.txt"
    src.write_text("hello world")
    with pytest.raises(ThumbnailError):
        await get_or_create_thumbnail(str(src))


@pytest.mark.skipif(shutil.which("gs") is None, reason="ghostscript not installed")
async def test_pdf_thumbnail_rendered_via_ghostscript(tmp_path):
    src = tmp_path / "scan.pdf"
    img = Image.new("RGB", (850, 1100), color=(20, 120, 200))
    img.save(str(src), "PDF")

    thumb_path = await get_or_create_thumbnail(str(src))

    assert thumb_path == str(src) + ".thumb.jpg"
    assert os.path.exists(thumb_path)
    with Image.open(thumb_path) as thumb:
        assert thumb.format == "JPEG"
        assert max(thumb.size) <= THUMBNAIL_MAX_DIM

    # No leftover intermediate PNG render.
    leftovers = [f for f in os.listdir(tmp_path) if "thumbsrc" in f]
    assert leftovers == []
