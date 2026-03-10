"""Tests for Pydantic schemas validation."""
import pytest
from pydantic import ValidationError

from app.schemas import (
    APITokenCreate,
    CopyRequest,
    EmailSendRequest,
    PrintJobUpload,
    ScanRequest,
    SMBShareCreate,
)


class TestPrintJobUpload:
    def test_defaults(self):
        job = PrintJobUpload()
        assert job.copies == 1
        assert job.duplex is False
        assert job.media == "A4"
        assert job.hold is True

    def test_valid_copies(self):
        job = PrintJobUpload(copies=5)
        assert job.copies == 5

    def test_invalid_copies_zero(self):
        with pytest.raises(ValidationError):
            PrintJobUpload(copies=0)

    def test_invalid_copies_over_max(self):
        with pytest.raises(ValidationError):
            PrintJobUpload(copies=100)


class TestScanRequest:
    def test_defaults(self):
        req = ScanRequest()
        assert req.resolution == 300
        assert req.mode == "Color"
        assert req.format == "pdf"
        assert req.source == "Flatbed"

    def test_valid_modes(self):
        for mode in ["Color", "Gray", "Lineart"]:
            req = ScanRequest(mode=mode)
            assert req.mode == mode

    def test_invalid_mode(self):
        with pytest.raises(ValidationError):
            ScanRequest(mode="InvalidMode")

    def test_invalid_resolution_too_low(self):
        with pytest.raises(ValidationError):
            ScanRequest(resolution=10)

    def test_invalid_format(self):
        with pytest.raises(ValidationError):
            ScanRequest(format="bmp")


class TestCopyRequest:
    def test_defaults(self):
        req = CopyRequest()
        assert req.resolution == 300
        assert req.copies == 1
        assert req.duplex is False


class TestAPITokenCreate:
    def test_valid(self):
        token = APITokenCreate(name="test-token")
        assert token.name == "test-token"
        assert token.permissions == ["print", "scan"]

    def test_empty_name(self):
        with pytest.raises(ValidationError):
            APITokenCreate(name="")


class TestSMBShareCreate:
    def test_valid(self):
        share = SMBShareCreate(name="NAS", server="192.168.1.50", share_name="docs")
        assert share.domain == "WORKGROUP"

    def test_empty_name(self):
        with pytest.raises(ValidationError):
            SMBShareCreate(name="", server="1.2.3.4", share_name="share")


class TestEmailSendRequest:
    def test_defaults(self):
        req = EmailSendRequest(to="test@example.com")
        assert req.subject == "Scanned Document"
        assert req.body == ""
