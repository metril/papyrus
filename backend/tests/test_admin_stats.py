"""Tests for the dashboard usage-stats GROUP BY -> legacy-shape mapping.

`get_usage_stats` used to run one `COUNT` query per known status. It now runs
a single `GROUP BY status` query per table and maps the rows onto the same
fixed status list, zero-filling any status that had no rows. These tests
exercise the mapping function directly with a fake result set, so they don't
need a database.

The bottom of the file has integration tests (need `db`/`admin_client`) for
the two later additions to `/api/admin/stats`: the zero-filled 30-day trend
and the ranked per-user totals.
"""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from app.models import PrintJob, ScanJob, User
from app.routers.admin import (
    PER_USER_TOP_N,
    PRINT_JOB_STATUSES,
    SCAN_JOB_STATUSES,
    TREND_DAYS,
    _ranked_per_user,
    _user_label,
    _zero_filled_status_counts,
    _zero_filled_trend,
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


# --- _zero_filled_trend -----------------------------------------------------


def test_trend_zero_fills_days_with_no_rows():
    days = ["2026-01-01", "2026-01-02", "2026-01-03"]
    print_rows = [(date(2026, 1, 1), 5)]
    scan_rows = [(date(2026, 1, 3), 2)]

    result = _zero_filled_trend(print_rows, scan_rows, days)

    assert result == [
        {"date": "2026-01-01", "prints": 5, "scans": 0},
        {"date": "2026-01-02", "prints": 0, "scans": 0},
        {"date": "2026-01-03", "prints": 0, "scans": 2},
    ]


def test_trend_empty_rows_zero_fill_every_day():
    days = ["2026-01-01", "2026-01-02"]
    result = _zero_filled_trend([], [], days)
    assert result == [
        {"date": "2026-01-01", "prints": 0, "scans": 0},
        {"date": "2026-01-02", "prints": 0, "scans": 0},
    ]


# --- _user_label -------------------------------------------------------------


def test_user_label_is_network_for_null_user_id():
    assert _user_label(None, "someone", "someone@example.com") == "Network"
    # Even if a username/email were somehow present, NULL user_id always wins.
    assert _user_label(None, None, None) == "Network"


def test_user_label_prefers_username():
    assert _user_label(uuid4(), "jdoe", "jdoe@example.com") == "jdoe"


def test_user_label_falls_back_to_email_when_username_missing():
    """OIDC-only accounts never populate the local-auth `username` column."""
    assert _user_label(uuid4(), None, "oidc.user@example.com") == "oidc.user@example.com"


# --- _ranked_per_user --------------------------------------------------------


def test_ranked_per_user_merges_print_and_scan_rows_and_sorts_desc():
    alice, bob = uuid4(), uuid4()
    print_rows = [(alice, "alice", "alice@x.com", 5), (bob, "bob", "bob@x.com", 1)]
    scan_rows = [(alice, "alice", "alice@x.com", 1), (None, None, None, 3)]

    result = _ranked_per_user(print_rows, scan_rows, top_n=10)

    assert result == [
        {"username": "alice", "prints": 5, "scans": 1},
        {"username": "Network", "prints": 0, "scans": 3},
        {"username": "bob", "prints": 1, "scans": 0},
    ]


def test_ranked_per_user_rolls_remainder_into_other_row():
    # 11 distinct users, strictly decreasing totals so ranking is unambiguous.
    rows = [(uuid4(), f"user{i}", f"user{i}@x.com", 22 - 2 * i) for i in range(11)]

    result = _ranked_per_user(rows, [], top_n=10)

    assert len(result) == 11
    assert [row["username"] for row in result[:10]] == [f"user{i}" for i in range(10)]
    assert result[-1] == {"username": "Other", "prints": 2, "scans": 0}


def test_ranked_per_user_no_other_row_when_at_or_below_top_n():
    rows = [(uuid4(), f"user{i}", f"user{i}@x.com", 1) for i in range(10)]
    result = _ranked_per_user(rows, [], top_n=10)
    assert len(result) == 10
    assert all(row["username"] != "Other" for row in result)


# --- Integration: GET /api/admin/stats --------------------------------------


async def _print_job(db, *, created_at, user_id=None, title="job.pdf") -> PrintJob:
    job = PrintJob(
        title=title,
        filename="job.pdf",
        filepath="/tmp/job.pdf",
        file_size=100,
        mime_type="application/pdf",
        status="completed",
        source_type="upload",
        user_id=user_id,
        created_at=created_at,
    )
    db.add(job)
    return job


async def _scan_job(db, *, created_at, user_id=None) -> ScanJob:
    job = ScanJob(status="completed", user_id=user_id, created_at=created_at)
    db.add(job)
    return job


async def test_stats_trend_30d_zero_fills_and_respects_utc_day_boundary(db, admin_client):
    """Seeds one print+scan today, one print on the oldest in-window day at
    23:59:59 UTC (must land on that day, not roll into the next), and one
    print entirely outside the 30-day window (must be absent from the trend
    but still count toward the all-time `per_user` total)."""
    today = datetime.now(timezone.utc).date()
    oldest_day = today - timedelta(days=TREND_DAYS - 1)
    outside_day = oldest_day - timedelta(days=1)

    today_dt = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    await _print_job(db, created_at=today_dt + timedelta(hours=10))
    await _scan_job(db, created_at=today_dt + timedelta(hours=11))
    oldest_day_dt = datetime(oldest_day.year, oldest_day.month, oldest_day.day, tzinfo=timezone.utc)
    await _print_job(db, created_at=oldest_day_dt + timedelta(hours=23, minutes=59, seconds=59))
    outside_day_dt = datetime(
        outside_day.year, outside_day.month, outside_day.day, tzinfo=timezone.utc
    )
    await _print_job(db, created_at=outside_day_dt + timedelta(hours=12))
    await db.commit()

    resp = await admin_client.get("/api/admin/stats")
    assert resp.status_code == 200
    body = resp.json()

    trend = body["trend_30d"]
    assert len(trend) == TREND_DAYS
    expected_dates = [(oldest_day + timedelta(days=i)).isoformat() for i in range(TREND_DAYS)]
    assert [row["date"] for row in trend] == expected_dates

    by_date = {row["date"]: row for row in trend}
    assert by_date[today.isoformat()] == {"date": today.isoformat(), "prints": 1, "scans": 1}
    assert by_date[oldest_day.isoformat()] == {
        "date": oldest_day.isoformat(),
        "prints": 1,
        "scans": 0,
    }
    # The out-of-window job must not appear anywhere in the trend.
    assert sum(row["prints"] for row in trend) == 2
    assert sum(row["scans"] for row in trend) == 1

    # per_user has no time window: all three prints + one scan are all
    # null-user_id, so they collapse into a single "Network" bucket.
    assert body["per_user"] == [{"username": "Network", "prints": 3, "scans": 1}]

    # Pre-existing fields are unchanged.
    assert set(body["print_counts"].keys()) == set(PRINT_JOB_STATUSES)
    assert set(body["scan_counts"].keys()) == set(SCAN_JOB_STATUSES)
    assert body["print_counts"]["completed"] == 3
    assert body["scan_counts"]["completed"] == 1
    assert "daily_prints" in body
    assert "daily_scans" in body


async def test_stats_per_user_ranks_and_rolls_up_beyond_top_10(db, admin_client):
    now = datetime.now(timezone.utc)
    users = []
    for i in range(11):
        user = User(
            email=f"user{i}@example.com",
            display_name=f"User {i}",
            role="user",
            is_local=True,
            username=f"user{i}",
        )
        db.add(user)
        users.append(user)
    await db.flush()

    # Strictly decreasing, evenly-spaced counts so the top-10/Other boundary
    # is unambiguous (no ties to break).
    for i, user in enumerate(users):
        count = 22 - 2 * i  # 22, 20, ..., 2
        for _ in range(count):
            await _print_job(db, created_at=now, user_id=user.id)
    await db.commit()

    resp = await admin_client.get("/api/admin/stats")
    assert resp.status_code == 200
    per_user = resp.json()["per_user"]

    assert len(per_user) == PER_USER_TOP_N + 1
    assert per_user[:10] == [
        {"username": f"user{i}", "prints": 22 - 2 * i, "scans": 0} for i in range(10)
    ]
    # The 11th user (count=2) rolls up alone into "Other".
    assert per_user[10] == {"username": "Other", "prints": 2, "scans": 0}
