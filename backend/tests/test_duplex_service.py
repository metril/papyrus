"""Pure merge tests for two-pass duplex (no DB)."""
import os
import stat

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from app.services import duplex_service as ds
from tests.duplex_helpers import make_pdf, page_image, tags


def _pdfs(tmp_path, fronts, backs):
    f, b = tmp_path / "f.pdf", tmp_path / "b.pdf"
    make_pdf(f, fronts)
    make_pdf(b, backs)
    return str(f), str(b), str(tmp_path / "out.pdf")


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5])
def test_interleaves_with_backs_reversed(tmp_path, n):
    fronts = [(i, "text") for i in range(1, n + 1)]
    backs = [(10 + i, "text") for i in range(1, n + 1)]  # scan order b1..bn
    f, b, out = _pdfs(tmp_path, fronts, backs)
    result = ds.merge_backs(f, b, out)
    expected = []
    for i in range(1, n + 1):
        expected += [i, 10 + (n + 1 - i)]
    assert tags(out) == expected
    assert result.page_count == 2 * n and result.blanks_dropped == 0


def test_rotate_backs_only_rotates_backs(tmp_path):
    f, b, out = _pdfs(tmp_path, [(1, "text"), (2, "text")], [(11, "text"), (12, "text")])
    ds.merge_backs(f, b, out, rotate_backs=True)
    rotations = [p.get("/Rotate", 0) % 360 for p in PdfReader(out).pages]
    assert rotations == [0, 180, 0, 180]


def test_no_rotation_by_default(tmp_path):
    f, b, out = _pdfs(tmp_path, [(1, "text")], [(11, "text")])
    ds.merge_backs(f, b, out)
    assert [p.get("/Rotate", 0) for p in PdfReader(out).pages] == [0, 0]


def test_blank_backs_are_dropped_after_pairing(tmp_path):
    # backs in scan order: b1 (back of f3) blank, b2 text, b3 (back of f1) blank
    f, b, out = _pdfs(
        tmp_path,
        [(1, "text"), (2, "text"), (3, "text")],
        [(11, "blank"), (12, "text"), (13, "blank")],
    )
    result = ds.merge_backs(f, b, out, skip_blank_backs=True)
    assert tags(out) == [1, 2, 12, 3]
    assert result.page_count == 4 and result.blanks_dropped == 2


def test_blank_backs_kept_by_default(tmp_path):
    f, b, out = _pdfs(tmp_path, [(1, "text")], [(11, "blank")])
    ds.merge_backs(f, b, out)
    assert tags(out) == [1, 11]


@pytest.mark.parametrize("kind", ["line", "text", "dark"])
def test_content_is_never_called_blank(kind):
    assert not ds.is_blank_image(page_image(1, kind))


@pytest.mark.parametrize("kind", ["blank", "noise", "edge"])
def test_empty_paper_is_blank(kind):
    assert ds.is_blank_image(page_image(1, kind))


def _text_page(text: str, size: int = 18) -> Image.Image:
    img = Image.new("L", (1240, 1754), 255)
    ImageDraw.Draw(img).text((310, 877), text, fill=0, font=ImageFont.load_default(size=size))
    return img


@pytest.mark.parametrize("text", ["Page 2", "2", "The quick brown fox"])
def test_real_scale_text_is_kept(text):
    assert not ds.is_blank_image(_text_page(text))


def test_scanner_noise_and_faint_show_through_are_blank():
    rng = np.random.default_rng(3)
    arr = 245 + rng.normal(0, 10, (1754, 1240))
    faint = np.asarray(_text_page("Show through text here", 40), dtype=float)
    arr = np.clip(arr - (255 - faint) / 255 * 20, 0, 255).astype(np.uint8)
    assert ds.is_blank_image(Image.fromarray(arr, "L"))


def test_single_text_line_back_is_kept(tmp_path):
    f, b, out = _pdfs(tmp_path, [(1, "text")], [(11, "line")])
    ds.merge_backs(f, b, out, skip_blank_backs=True)
    assert tags(out) == [1, 11]


def test_unreadable_page_image_is_not_blank(tmp_path):
    from pypdf import PageObject
    assert ds.is_blank_page(PageObject.create_blank_page(width=100, height=100)) is False


def test_count_mismatch_raises_and_writes_nothing(tmp_path):
    f, b, out = _pdfs(tmp_path, [(1, "text"), (2, "text")], [(11, "text")])
    with pytest.raises(ds.PageCountMismatchError) as e:
        ds.merge_backs(f, b, out)
    assert (e.value.fronts, e.value.backs) == (2, 1)
    assert not os.path.exists(out)


def test_atomic_writes_new_file_and_leaves_inputs(tmp_path):
    f, b, out = _pdfs(tmp_path, [(1, "text"), (2, "text")], [(11, "text"), (12, "text")])
    fb, bb = open(f, "rb").read(), open(b, "rb").read()
    result = ds.merge_backs_atomic(f, b, out)
    assert tags(out) == [1, 12, 2, 11] and result.page_count == 4
    assert open(f, "rb").read() == fb and open(b, "rb").read() == bb
    assert [n for n in os.listdir(tmp_path) if n.startswith(".duplex_")] == []


def test_atomic_copies_the_fronts_file_mode(tmp_path):
    f, b, out = _pdfs(tmp_path, [(1, "text")], [(11, "text")])
    os.chmod(f, 0o640)
    ds.merge_backs_atomic(f, b, out)
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o640


def test_atomic_failure_writes_nothing_and_no_temp(tmp_path):
    f, _, out = _pdfs(tmp_path, [(1, "text")], [(11, "text")])
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(Exception):
        ds.merge_backs_atomic(f, str(bad), out)
    assert not os.path.exists(out)
    assert [n for n in os.listdir(tmp_path) if n.startswith(".duplex_")] == []


def test_atomic_mismatch_writes_nothing(tmp_path):
    f, b, out = _pdfs(tmp_path, [(1, "text"), (2, "text")], [(11, "text")])
    with pytest.raises(ds.PageCountMismatchError):
        ds.merge_backs_atomic(f, b, out)
    assert not os.path.exists(out)


def test_reverse_backs_false_keeps_scan_order(tmp_path):
    f, b, out = _pdfs(tmp_path, [(1, "text"), (2, "text")], [(11, "text"), (12, "text")])
    ds.merge_backs(f, b, out, reverse_backs=False)
    assert tags(out) == [1, 11, 2, 12]


@pytest.mark.parametrize("where", ["footer", "header", "left", "right"])
def test_marks_in_the_outer_margin_zone_are_kept(where):
    w, h = 1240, 1754
    pos = {
        "footer": (w // 2, int(h * 0.95)),
        "header": (w // 2, int(h * 0.04)),
        "left": (int(w * 0.04), h // 2),
        "right": (int(w * 0.93), h // 2),
    }[where]
    img = Image.new("L", (w, h), 255)
    ImageDraw.Draw(img).text(pos, "Page 7", fill=0, font=ImageFont.load_default(size=18))
    assert not ds.is_blank_image(img)


def test_margin_signature_is_kept():
    img = Image.new("L", (1240, 1754), 255)
    ImageDraw.Draw(img).line((1180, 1650, 1225, 1620), fill=0, width=3)
    assert not ds.is_blank_image(img)
