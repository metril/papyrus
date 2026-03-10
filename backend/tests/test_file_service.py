"""Tests for file service utilities."""
from app.services.file_service import detect_mime_type, sanitize_filename


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
