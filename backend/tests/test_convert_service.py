"""Tests for document conversion service."""
from app.services.convert_service import is_printable, needs_conversion


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
