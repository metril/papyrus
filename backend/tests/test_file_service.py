"""Tests for file service utilities."""
import io
import os

import pytest
from starlette.datastructures import UploadFile

from app.services.file_service import (
    UploadTooLargeError,
    detect_mime_type,
    sanitize_filename,
    save_upload_streaming,
)


def test_sanitize_filename_basic():
    assert sanitize_filename("test.pdf") == "test.pdf"


def test_sanitize_filename_path_traversal():
    result = sanitize_filename("../../etc/passwd")
    assert "/" not in result
    assert ".." not in result


def test_sanitize_filename_special_chars():
    result = sanitize_filename("file (1) [test].pdf")
    assert result == "file__1___test_.pdf"


def test_sanitize_filename_long_name():
    long_name = "a" * 300 + ".pdf"
    result = sanitize_filename(long_name)
    assert len(result) <= 200


def test_detect_mime_type_pdf():
    assert detect_mime_type("document.pdf") == "application/pdf"


def test_detect_mime_type_jpeg():
    assert detect_mime_type("photo.jpg") == "image/jpeg"


def test_detect_mime_type_docx():
    result = detect_mime_type("file.docx")
    assert "word" in result or "document" in result


def test_detect_mime_type_unknown():
    assert detect_mime_type("file.xyz123") == "application/octet-stream"


def _upload_file(data: bytes, filename: str = "test.pdf") -> UploadFile:
    """Build a real starlette UploadFile wrapping in-memory bytes (no temp file)."""
    return UploadFile(io.BytesIO(data), filename=filename)


async def test_save_upload_streaming_under_limit_writes_fully(tmp_path):
    data = b"a" * 1000
    dest = tmp_path / "under.bin"
    written = await save_upload_streaming(_upload_file(data), str(dest), max_bytes=2000)

    assert written == len(data)
    assert dest.read_bytes() == data


async def test_save_upload_streaming_over_limit_raises_and_removes_partial(tmp_path):
    data = b"b" * 2000
    dest = tmp_path / "over.bin"

    with pytest.raises(UploadTooLargeError):
        await save_upload_streaming(_upload_file(data), str(dest), max_bytes=1000)

    assert not os.path.exists(dest)


async def test_save_upload_streaming_exact_limit_succeeds(tmp_path):
    data = b"c" * 1500
    dest = tmp_path / "exact.bin"
    written = await save_upload_streaming(_upload_file(data), str(dest), max_bytes=1500)

    assert written == 1500
    assert dest.read_bytes() == data


async def test_save_upload_streaming_empty_file(tmp_path):
    dest = tmp_path / "empty.bin"
    written = await save_upload_streaming(_upload_file(b""), str(dest), max_bytes=1000)

    assert written == 0
    assert dest.read_bytes() == b""
