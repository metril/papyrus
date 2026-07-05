"""Tests for the dashboard usage-stats GROUP BY -> legacy-shape mapping.

`get_usage_stats` used to run one `COUNT` query per known status. It now runs
a single `GROUP BY status` query per table and maps the rows onto the same
fixed status list, zero-filling any status that had no rows. These tests
exercise the mapping function directly with a fake result set, so they don't
need a database.
"""
from app.routers.admin import (
    PRINT_JOB_STATUSES,
    SCAN_JOB_STATUSES,
    _zero_filled_status_counts,
)


def test_print_statuses_zero_filled_when_no_rows():
    counts = _zero_filled_status_counts([], PRINT_JOB_STATUSES)
    assert counts == {
        "held": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "printing": 0,
    }
    # Same keys, same order as the legacy per-status loop produced.
    assert list(counts.keys()) == PRINT_JOB_STATUSES


def test_print_statuses_mapped_from_group_by_rows():
    rows = [("held", 3), ("completed", 10), ("failed", 1)]
    counts = _zero_filled_status_counts(rows, PRINT_JOB_STATUSES)
    assert counts == {
        "held": 3,
        "completed": 10,
        "failed": 1,
        "cancelled": 0,
        "printing": 0,
    }


def test_scan_statuses_mapped_from_group_by_rows():
    rows = [("scanning", 2), ("completed", 7)]
    counts = _zero_filled_status_counts(rows, SCAN_JOB_STATUSES)
    assert counts == {"completed": 7, "failed": 0, "scanning": 2}


def test_unknown_status_in_rows_is_dropped():
    """A status outside the fixed list is ignored, matching legacy behavior:
    the old code only ever queried the fixed list, so any other status value
    present in the table was never counted."""
    rows = [("held", 3), ("some_future_status", 99)]
    counts = _zero_filled_status_counts(rows, PRINT_JOB_STATUSES)
    assert counts["held"] == 3
    assert "some_future_status" not in counts
    assert sum(counts.values()) == 3
