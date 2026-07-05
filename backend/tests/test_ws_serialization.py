"""Tests that WebSocket broadcast payloads match the REST list-endpoint shape.

The incremental-WS feature reuses the same Pydantic response models for realtime
broadcasts as the list endpoints, so the shapes must stay in lockstep. These
tests also guard the security-critical invariant that a print job's release PIN
is never serialized into a broadcast (which fans out to every connected client).
"""
from datetime import datetime, timezone

from app.models import PrintJob, ScanJob
from app.schemas import (
    PrintJobResponse,
    ScanResponse,
    serialize_print_job,
    serialize_scan_job,
)

_NOW = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)


def _make_print_job() -> PrintJob:
    return PrintJob(
        id=42,
        cups_job_id=7,
        title="Report.pdf",
        filename="Report.pdf",
        filepath="/app/data/uploads/report.pdf",
        file_size=1234,
        mime_type="application/pdf",
        status="held",
        copies=2,
        duplex=True,
        media="A4",
        source_type="upload",
        printer_id=3,
        release_pin="4821",
        error_message=None,
        created_at=_NOW,
        updated_at=_NOW,
        completed_at=None,
    )


def _make_scan_job() -> ScanJob:
    return ScanJob(
        id=9,
        scan_id="abc-123",
        status="completed",
        resolution=300,
        mode="Color",
        format="pdf",
        source="Flatbed",
        page_count=3,
        filepath="/app/data/scans/abc.pdf",
        file_size=5678,
        error_message=None,
        created_at=_NOW,
        completed_at=_NOW,
    )


class TestPrintJobWSSerialization:
    def test_field_parity_with_list_response_model(self):
        # The /jobs list endpoint returns PrintJobList(jobs=list[PrintJobResponse]),
        # so the WS payload must have exactly the PrintJobResponse field set.
        payload = serialize_print_job(_make_print_job())
        assert set(payload.keys()) == set(PrintJobResponse.model_fields.keys())

    def test_has_pin_computed_true_when_pin_set(self):
        payload = serialize_print_job(_make_print_job())
        assert payload["has_pin"] is True

    def test_release_pin_never_in_payload(self):
        payload = serialize_print_job(_make_print_job())
        assert "release_pin" not in payload
        # The raw PIN value must not appear anywhere in the serialized payload.
        assert "4821" not in str(payload)


class TestScanJobWSSerialization:
    def test_field_parity_with_list_response_model(self):
        # The /scanner/scans list endpoint returns ScanList(scans=list[ScanResponse]).
        payload = serialize_scan_job(_make_scan_job())
        assert set(payload.keys()) == set(ScanResponse.model_fields.keys())

    def test_internal_filepath_not_leaked(self):
        # filepath is an internal server path that ScanResponse deliberately omits.
        payload = serialize_scan_job(_make_scan_job())
        assert "filepath" not in payload
