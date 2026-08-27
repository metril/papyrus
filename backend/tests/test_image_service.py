"""Behavior tests for image_service auto-crop and deskew.

These encode the CURRENT (pre-rewrite) semantics as observable behavior:
margins (asymmetric 10px top/left, effectively 9px bottom/right), the
threshold boundary (<240 is content), the strict right>left / bottom>top
bail-out, the deskew near-straight bail-out (|angle| must exceed 0.1 to
rotate), the fill color / expand behavior, and the non-image skip path.
"""
import numpy as np
import pytest
from PIL import Image

from app.services.image_service import (
    ImageService,
    _auto_crop,
    _fill_color,
    detect_skew_angle,
)


def _striped(w: int = 400, h: int = 300, period: int = 20, thickness: int = 8) -> Image.Image:
    """White page with evenly spaced horizontal black stripes (text-like)."""
    arr = np.full((h, w), 255, np.uint8)
    for y in range(0, h, period):
        arr[y : y + thickness, :] = 0
    return Image.fromarray(arr, "L")


# --------------------------------------------------------------------------- #
# auto_crop
# --------------------------------------------------------------------------- #
def test_auto_crop_offset_rectangle_bounds_and_margins():
    # Dark rectangle: rows 40..120 inclusive, cols 50..150 inclusive.
    arr = np.full((300, 400), 255, np.uint8)
    arr[40:121, 50:151] = 0
    out = _auto_crop(Image.fromarray(arr, "L"))

    # bbox = (left-10, top-10, right+10, bottom+10) with right/bottom exclusive:
    # (40, 30, 160, 130) -> size (120, 100)
    assert out.size == (120, 100)

    oarr = np.asarray(out.convert("L"))
    dark_rows = np.where((oarr < 240).any(axis=1))[0]
    dark_cols = np.where((oarr < 240).any(axis=0))[0]
    # 10px top/left margin (index 10), 9px bottom/right margin (last dark at 90/110).
    assert (int(dark_rows[0]), int(dark_rows[-1])) == (10, 90)
    assert (int(dark_cols[0]), int(dark_cols[-1])) == (10, 110)


def test_auto_crop_white_image_unchanged():
    img = Image.fromarray(np.full((120, 160), 255, np.uint8), "L")
    out = _auto_crop(img)
    assert out.size == img.size
    assert np.array_equal(np.asarray(out), np.asarray(img))


def test_auto_crop_near_black_page_unchanged_size():
    # Whole page dark: bbox clamps to full image -> effectively no crop.
    img = Image.fromarray(np.full((300, 400), 5, np.uint8), "L")
    out = _auto_crop(img)
    assert out.size == img.size


def test_auto_crop_single_column_not_cropped():
    # Only one dark column -> right == left -> strict right>left fails -> unchanged.
    arr = np.full((300, 400), 255, np.uint8)
    arr[40:121, 50:51] = 0
    img = Image.fromarray(arr, "L")
    out = _auto_crop(img)
    assert out.size == img.size


def test_auto_crop_threshold_boundary():
    # Value exactly 240 is NOT content (< 240 is strict); 239 is content.
    bg = np.full((300, 400), 255, np.uint8)

    at_240 = bg.copy()
    at_240[40:121, 50:151] = 240
    assert _auto_crop(Image.fromarray(at_240, "L")).size == (400, 300)

    at_239 = bg.copy()
    at_239[40:121, 50:151] = 239
    assert _auto_crop(Image.fromarray(at_239, "L")).size == (120, 100)


# --------------------------------------------------------------------------- #
# detect_skew_angle
# --------------------------------------------------------------------------- #
def test_detect_skew_straight_is_near_zero():
    angle = detect_skew_angle(_striped())
    assert abs(angle) <= 0.1


@pytest.mark.parametrize("applied,expected", [(2.0, -2.0), (-2.0, 2.0), (3.0, -3.0)])
def test_detect_skew_known_angle(applied, expected):
    tilted = _striped().rotate(applied, expand=False, fillcolor=255)
    angle = detect_skew_angle(tilted)
    assert abs(angle - expected) <= 0.2


def test_detect_skew_uniform_image_is_zero():
    uniform = Image.fromarray(np.full((300, 400), 5, np.uint8), "L")
    assert detect_skew_angle(uniform) == 0.0


# --------------------------------------------------------------------------- #
# deskew (end-to-end, file in place)
# --------------------------------------------------------------------------- #
async def test_deskew_straight_leaves_file_unchanged(tmp_path):
    p = tmp_path / "straight.png"
    _striped().save(p)
    before = np.asarray(Image.open(p).convert("L")).copy()

    await ImageService().deskew(str(p))

    after = np.asarray(Image.open(p).convert("L"))
    assert after.shape == before.shape
    assert np.array_equal(after, before)


async def test_deskew_tilted_rotates_and_expands_file(tmp_path):
    p = tmp_path / "tilted.png"
    tilted = _striped().rotate(3.0, expand=False, fillcolor=255)
    tilted.save(p)
    before_size = Image.open(p).size

    await ImageService().deskew(str(p))

    out = Image.open(p)
    # expand=True on the correcting rotation grows the canvas.
    assert out.size != before_size
    assert out.size[0] >= before_size[0] and out.size[1] >= before_size[1]
    # residual skew of the corrected page is within the bail-out band.
    assert abs(detect_skew_angle(out)) <= 0.2


async def test_deskew_uniform_page_unchanged(tmp_path):
    p = tmp_path / "black.png"
    Image.fromarray(np.full((300, 400), 5, np.uint8), "L").save(p)
    before = Image.open(p).size
    await ImageService().deskew(str(p))
    assert Image.open(p).size == before


async def test_deskew_non_image_extension_skips(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 not really a pdf")
    result = await ImageService().deskew(str(p))
    assert result == str(p)
    assert p.read_bytes() == b"%PDF-1.4 not really a pdf"


# --------------------------------------------------------------------------- #
# enhance integration
# --------------------------------------------------------------------------- #
async def test_enhance_auto_crop_writes_cropped_file(tmp_path):
    p = tmp_path / "scan.png"
    arr = np.full((300, 400), 255, np.uint8)
    arr[40:121, 50:151] = 0
    Image.fromarray(arr, "L").save(p)

    await ImageService().enhance(str(p), auto_crop=True)

    assert Image.open(p).size == (120, 100)


# --------------------------------------------------------------------------- #
# F15: rotation fill color matches the image's mode (no red wedges on RGB)
# --------------------------------------------------------------------------- #
def test_fill_color_is_plain_255_for_grayscale():
    img = Image.fromarray(np.full((10, 10), 255, np.uint8), "L")
    assert _fill_color(img) == 255


def test_fill_color_is_white_tuple_for_rgb():
    img = Image.new("RGB", (10, 10), (10, 20, 30))
    assert _fill_color(img) == (255, 255, 255)


def test_fill_color_is_white_tuple_for_rgba():
    img = Image.new("RGBA", (10, 10), (10, 20, 30, 40))
    assert _fill_color(img) == (255, 255, 255, 255)


def _striped_rgb(w: int = 400, h: int = 300, period: int = 20, thickness: int = 8) -> Image.Image:
    """Color equivalent of `_striped`: an RGB page with horizontal stripes,
    tinted so it isn't just a grayscale image wearing an RGB label."""
    arr = np.full((h, w, 3), 255, np.uint8)
    for y in range(0, h, period):
        arr[y : y + thickness, :, :] = (20, 60, 120)
    return Image.fromarray(arr, "RGB")


async def test_deskew_rgb_image_corners_are_white_not_red(tmp_path):
    """Reproduces F15: rotating a color scan used to fill the expanded
    corners with fillcolor=255, which PIL packs as raw RGB bytes -- (255, 0,
    0), bright red -- instead of white."""
    p = tmp_path / "tilted.png"
    tilted = _striped_rgb().rotate(3.0, expand=False, fillcolor=(255, 255, 255))
    tilted.save(p)

    await ImageService().deskew(str(p))

    out = np.asarray(Image.open(p).convert("RGB"))
    corners = [
        out[0, 0], out[0, -1], out[-1, 0], out[-1, -1],
    ]
    for corner in corners:
        r, g, b = (int(c) for c in corner)
        assert (r, g, b) == (255, 255, 255), f"corner pixel was {(r, g, b)}, expected white"
        assert not (r > 200 and g < 50 and b < 50), "corner pixel is red"


# --------------------------------------------------------------------------- #
# F52: enhance() applies brightness/contrast/rotation/crop AND deskew,
# instead of silently discarding the former whenever deskew is requested.
#
# `detect_skew_angle` is monkeypatched to a fixed no-op (0.0) angle in both
# tests below, so what's under test is purely whether _process's effects
# survive alongside a deskew request -- not the real skew-detection
# algorithm's behavior on a rotated/cropped image, which is covered
# separately (test_detect_skew_* above) and would make an exact-size
# assertion here fragile.
# --------------------------------------------------------------------------- #
async def test_enhance_with_deskew_also_applies_rotation(tmp_path, monkeypatch):
    """A 90-degree manual rotation must actually take effect even when
    deskew=True is requested alongside it (F52 used to return immediately
    after deskewing, dropping brightness/contrast/rotation/auto_crop)."""
    import app.services.image_service as image_service_module

    monkeypatch.setattr(image_service_module, "detect_skew_angle", lambda img: 0.0)

    p = tmp_path / "scan.png"
    _striped(w=400, h=300).save(p)
    before_size = Image.open(p).size

    await ImageService().enhance(str(p), rotation=90, deskew=True)

    after_size = Image.open(p).size
    assert after_size == (before_size[1], before_size[0])  # width/height swapped


async def test_enhance_with_deskew_also_applies_auto_crop(tmp_path, monkeypatch):
    import app.services.image_service as image_service_module

    monkeypatch.setattr(image_service_module, "detect_skew_angle", lambda img: 0.0)

    p = tmp_path / "scan.png"
    arr = np.full((300, 400), 255, np.uint8)
    arr[40:121, 50:151] = 0
    Image.fromarray(arr, "L").save(p)

    await ImageService().enhance(str(p), auto_crop=True, deskew=True)

    # auto_crop's effect (a much smaller image) must be visible, not
    # silently dropped in favor of only running deskew.
    assert Image.open(p).size == (120, 100)
