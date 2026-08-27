"""Unit tests for ``app.services.ocr_service`` (F12).

``ocrmypdf`` is never actually invoked: ``asyncio.create_subprocess_exec`` is
faked per test (mirrors the convention in test_system_health.py /
test_convert_service.py / test_scan_service.py), so these exercise
``apply_ocr``'s own bookkeeping -- the uuid-suffixed temp output path, the
atomic replace-over-original, cleanup on failure/timeout, and per-path
serialization via ``file_locks.lock_for`` -- against real files on disk.
"""
import asyncio

import pytest

from app.exceptions import ExternalServiceError
from app.services.ocr_service import OCRError, OCRService


def _fake_exec_writing_output(content: bytes = b"%PDF-1.4 ocred\n", returncode: int = 0):
    """Build a fake `create_subprocess_exec` that writes `content` to the
    ocrmypdf output path (the command's last argument) and reports
    `returncode`."""

    class _FakeProcess:
        def __init__(self):
            self.returncode = returncode

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        out_path = args[-1]
        if returncode == 0:
            with open(out_path, "wb") as f:
                f.write(content)
        return _FakeProcess()

    return fake_exec


async def test_apply_ocr_replaces_original_with_ocrd_output(tmp_path, monkeypatch):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 original\n")

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", _fake_exec_writing_output(b"%PDF-1.4 searchable\n")
    )

    result = await OCRService().apply_ocr(str(pdf))

    assert result == str(pdf)
    assert pdf.read_bytes() == b"%PDF-1.4 searchable\n"
    # No stray `.ocr.pdf`-suffixed temp files left behind.
    assert list(tmp_path.iterdir()) == [pdf]


async def test_apply_ocr_output_path_is_uuid_suffixed_not_fixed(tmp_path, monkeypatch):
    """F12: the old code always wrote `filepath + ".ocr.pdf"` -- a fixed,
    predictable name two concurrent OCR runs on the same file would collide
    on. Assert the actual output path handed to ocrmypdf varies per call."""
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 original\n")

    seen_out_paths = []

    class _FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        out_path = args[-1]
        seen_out_paths.append(out_path)
        with open(out_path, "wb") as f:
            f.write(b"%PDF-1.4 ocred\n")
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    await OCRService().apply_ocr(str(pdf))
    await OCRService().apply_ocr(str(pdf))

    assert len(seen_out_paths) == 2
    assert seen_out_paths[0] != seen_out_paths[1]
    assert seen_out_paths[0] != f"{pdf}.ocr.pdf"


async def test_apply_ocr_failure_cleans_up_partial_output_and_leaves_original(
    tmp_path, monkeypatch
):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 original\n")

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", _fake_exec_writing_output(returncode=1)
    )

    with pytest.raises(OCRError):
        await OCRService().apply_ocr(str(pdf))

    # Original untouched, and nothing but the original left in the directory.
    assert pdf.read_bytes() == b"%PDF-1.4 original\n"
    assert list(tmp_path.iterdir()) == [pdf]


async def test_apply_ocr_timeout_kills_process_and_cleans_up(tmp_path, monkeypatch):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 original\n")

    class _TimingOutProcess:
        def __init__(self):
            self.killed = False
            self.waited = False

        async def communicate(self):
            raise asyncio.TimeoutError()

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True

    proc = _TimingOutProcess()

    async def fake_exec(*args, **kwargs):
        # Simulate ocrmypdf having started writing before hanging.
        out_path = args[-1]
        with open(out_path, "wb") as f:
            f.write(b"partial")
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(OCRError):
        await OCRService().apply_ocr(str(pdf))

    assert proc.killed is True
    assert proc.waited is True
    assert pdf.read_bytes() == b"%PDF-1.4 original\n"
    assert list(tmp_path.iterdir()) == [pdf]  # partial output was removed


async def test_apply_ocr_rejects_non_pdf_extension(tmp_path):
    png = tmp_path / "scan.png"
    png.write_bytes(b"not a pdf")

    with pytest.raises(OCRError):
        await OCRService().apply_ocr(str(png))


async def test_apply_ocr_missing_file_raises(tmp_path):
    with pytest.raises(OCRError):
        await OCRService().apply_ocr(str(tmp_path / "does-not-exist.pdf"))


def test_ocr_error_is_a_papyrus_external_service_error():
    assert issubclass(OCRError, ExternalServiceError)


# --------------------------------------------------------------------------- #
# F12: concurrent OCR runs on the same file are serialized, not raced.
# --------------------------------------------------------------------------- #
async def test_apply_ocr_serializes_concurrent_calls_on_same_path(tmp_path, monkeypatch):
    """Two overlapping apply_ocr() calls for the same file (e.g. manual
    "Apply OCR" racing auto-deliver OCR) must never run their ocrmypdf
    invocations concurrently -- lock_for(filepath) should force the second
    call to wait for the first to fully finish (including the replace)
    before it even spawns its own subprocess."""
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 original\n")

    in_flight = 0
    max_in_flight = 0

    class _FakeProcess:
        returncode = 0

        async def communicate(self):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return b"", b""

    async def fake_exec(*args, **kwargs):
        out_path = args[-1]
        with open(out_path, "wb") as f:
            f.write(b"%PDF-1.4 ocred\n")
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    svc = OCRService()
    await asyncio.gather(svc.apply_ocr(str(pdf)), svc.apply_ocr(str(pdf)))

    assert max_in_flight == 1
