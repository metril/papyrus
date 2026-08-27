"""Tests for document conversion service."""
import asyncio
import os

import pytest

from app.services.convert_service import convert_to_pdf, is_printable, needs_conversion


def test_pdf_is_printable():
    assert is_printable("application/pdf") is True


def test_jpeg_is_printable():
    assert is_printable("image/jpeg") is True


def test_docx_is_printable():
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert is_printable(mime) is True


def test_unknown_not_printable():
    assert is_printable("application/octet-stream") is False


def test_pdf_does_not_need_conversion():
    assert needs_conversion("application/pdf") is False


def test_image_does_not_need_conversion():
    assert needs_conversion("image/png") is False


def test_docx_needs_conversion():
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert needs_conversion(mime) is True


def test_xlsx_needs_conversion():
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert needs_conversion(mime) is True


def test_pptx_needs_conversion():
    mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    assert needs_conversion(mime) is True


# --------------------------------------------------------------------------- #
# convert_to_pdf — F30 (unique output dir per call) / F31 (cleanup on failure)
#
# LibreOffice is faked at the asyncio.create_subprocess_exec layer rather
# than shelled out for real: what's under test is convert_to_pdf's own
# temp-dir bookkeeping, not LibreOffice itself.
# --------------------------------------------------------------------------- #
class _FakeProcess:
    def __init__(self, returncode: int = 0):
        self.returncode = returncode

    async def communicate(self):
        return b"", b""


def _fake_libreoffice_success(monkeypatch):
    """Writes a stub PDF into whatever --outdir it was given, named after the
    input's basename — mirroring LibreOffice's real naming convention."""
    async def _fake(*args, **kwargs):
        outdir = args[args.index("--outdir") + 1]
        input_path = args[-1]
        base = os.path.splitext(os.path.basename(input_path))[0]
        with open(os.path.join(outdir, f"{base}.pdf"), "wb") as f:
            f.write(b"%PDF-fake%")
        return _FakeProcess(returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake)


async def test_convert_to_pdf_writes_outside_the_output_dir_itself(tmp_path, monkeypatch):
    _fake_libreoffice_success(monkeypatch)
    input_path = tmp_path / "report.docx"
    input_path.write_bytes(b"fake docx")

    result = await convert_to_pdf(str(input_path), str(tmp_path))

    assert os.path.exists(result)
    assert os.path.dirname(result) != str(tmp_path)


async def test_convert_to_pdf_two_calls_do_not_share_an_output_path(tmp_path, monkeypatch):
    """Regression (F30): both calls used to write the same deterministic
    `<output_dir>/<base>.pdf`, so a job's release racing its first-time
    preview/thumbnail conversion could collide — one call's rename moving the
    file out from under the other. Each call must get its own unique temp
    directory."""
    _fake_libreoffice_success(monkeypatch)
    input_path = tmp_path / "report.docx"
    input_path.write_bytes(b"fake docx")

    result1 = await convert_to_pdf(str(input_path), str(tmp_path))
    result2 = await convert_to_pdf(str(input_path), str(tmp_path))

    assert result1 != result2
    assert os.path.exists(result1)
    assert os.path.exists(result2)


async def test_convert_to_pdf_cleans_up_temp_dir_on_conversion_failure(tmp_path, monkeypatch):
    """Regression (F31): a failed conversion must not leak its temp dir —
    the caller never learns its path (convert_to_pdf raises instead of
    returning), so it can only be cleaned up here."""
    async def _fake_fail(*args, **kwargs):
        return _FakeProcess(returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_fail)
    input_path = tmp_path / "report.docx"
    input_path.write_bytes(b"fake docx")

    before = set(os.listdir(tmp_path))
    with pytest.raises(RuntimeError):
        await convert_to_pdf(str(input_path), str(tmp_path))
    after = set(os.listdir(tmp_path))

    assert after == before


async def test_convert_to_pdf_cleans_up_temp_dir_when_output_missing(tmp_path, monkeypatch):
    """LibreOffice can exit 0 but still produce no output file — that must
    also clean up the temp dir, not just a nonzero exit code."""
    async def _fake_no_output(*args, **kwargs):
        return _FakeProcess(returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_no_output)
    input_path = tmp_path / "report.docx"
    input_path.write_bytes(b"fake docx")

    before = set(os.listdir(tmp_path))
    with pytest.raises(RuntimeError, match="no output"):
        await convert_to_pdf(str(input_path), str(tmp_path))
    after = set(os.listdir(tmp_path))

    assert after == before
