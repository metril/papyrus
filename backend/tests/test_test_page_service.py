"""Tests for `app.services.test_page_service.print_test_page`.

No real database or CUPS connection: a hand-rolled fake AsyncSession mimics
just enough of SQLAlchemy's flush behavior (assigning `id`/`created_at`/
`updated_at` on first commit, like a real INSERT would) for
`serialize_print_job` to validate successfully, `CupsService` is
monkeypatched at the `test_page_service` module level (mirroring how
`test_printer_status_watcher.py` patches the module a function actually
calls it from), and `ws_manager.broadcast` is monkeypatched to capture
payloads instead of touching real websockets.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from PIL import ImageFont

from app.models import Printer, PrintJob, User
from app.services import test_page_service


class _FakeDB:
    """Minimal AsyncSession stand-in: `commit()` simulates the id/timestamp
    assignment a real flush would perform on the row passed to `add()`."""

    def __init__(self):
        self.commits = 0
        self._job: PrintJob | None = None
        self._next_id = 100

    def add(self, obj):
        self._job = obj

    async def commit(self):
        self.commits += 1
        job = self._job
        if job is not None:
            now = datetime.now(timezone.utc)
            if job.id is None:
                job.id = self._next_id
            if job.created_at is None:
                job.created_at = now
            if job.updated_at is None:
                job.updated_at = now

    async def refresh(self, _obj):
        pass


def _printer(**overrides) -> Printer:
    defaults = dict(
        id=1,
        display_name="Brother Upstairs",
        cups_name="brother",
        uri="ipp://192.168.1.50/ipp/print",
        make_and_model="Brother DCP-L2540DW",
        location="Upstairs office",
        is_network_queue=False,
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return Printer(**defaults)


def _user() -> User:
    return User(
        id=uuid.uuid4(),
        email="admin@example.com",
        display_name="Admin",
        role="admin",
    )


@pytest.fixture(autouse=True)
def _patch_upload_dir(monkeypatch, tmp_path):
    """Route the service's upload_dir lookup at a tmp dir instead of the
    real DB-backed settings path (which the fake session doesn't back)."""

    async def fake_get_setting(_db, key):
        assert key == "upload_dir"
        return str(tmp_path)

    monkeypatch.setattr("app.routers.settings.get_setting", fake_get_setting)


@pytest.fixture
def broadcast(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr("app.services.ws_manager.ws_manager.broadcast", mock)
    return mock


class _FakeCupsService:
    """Records the queue name(s) it was constructed with; success path."""

    instances: list[str] = []

    def __init__(self, printer_name: str):
        self.printer_name = printer_name
        _FakeCupsService.instances.append(printer_name)

    async def create_held_job(self, filepath, title, copies, duplex, media):
        return 555

    async def release_job(self, job_id):
        pass


class _FakeCupsServiceFailing:
    def __init__(self, printer_name: str):
        self.printer_name = printer_name

    async def create_held_job(self, filepath, title, copies, duplex, media):
        raise RuntimeError("printer offline")

    async def release_job(self, job_id):  # pragma: no cover - never reached
        raise AssertionError("release_job should not be called after create_held_job fails")


@pytest.fixture(autouse=True)
def _reset_fake_cups_instances():
    _FakeCupsService.instances = []
    yield


async def test_print_test_page_success_creates_job_and_prints(monkeypatch, broadcast):
    monkeypatch.setattr(test_page_service, "CupsService", _FakeCupsService)

    printer = _printer()
    user = _user()
    db = _FakeDB()

    job = await test_page_service.print_test_page(db, printer, user)

    assert job.title == "Test page — Brother Upstairs"
    assert job.filename == "test-page.pdf"
    assert job.source_type == "test_page"
    assert job.printer_id == printer.id
    assert job.mime_type == "application/pdf"
    assert job.status == "printing"
    assert job.cups_job_id == 555
    assert job.file_size > 0

    # The physical print queue is the printer's *release* queue, never the
    # raw hold queue (which just re-ingests jobs via the papyrus backend and
    # would never actually print).
    assert _FakeCupsService.instances == ["brother_release"]


async def test_print_test_page_writes_a_real_pdf_to_disk(monkeypatch, broadcast):
    monkeypatch.setattr(test_page_service, "CupsService", _FakeCupsService)

    job = await test_page_service.print_test_page(_FakeDB(), _printer(), _user())

    with open(job.filepath, "rb") as f:
        content = f.read()
    assert content.startswith(b"%PDF")
    assert len(content) == job.file_size


async def test_print_test_page_broadcasts_full_serialized_job_created_and_updated(
    monkeypatch, broadcast
):
    monkeypatch.setattr(test_page_service, "CupsService", _FakeCupsService)

    job = await test_page_service.print_test_page(_FakeDB(), _printer(), _user())

    assert broadcast.await_count == 2
    (channel1, payload1), _ = broadcast.await_args_list[0]
    (channel2, payload2), _ = broadcast.await_args_list[1]

    assert channel1 == "jobs"
    assert channel2 == "jobs"
    assert payload1["type"] == "job_created"
    assert payload2["type"] == "job_updated"

    created_data = payload1["data"]
    updated_data = payload2["data"]

    # Full serialized objects, not partial dicts -- same shape as
    # serialize_print_job(job) at each point in time.
    assert created_data["status"] == "held"
    assert created_data["cups_job_id"] is None
    assert created_data["id"] == job.id
    assert created_data["title"] == job.title
    assert created_data["source_type"] == "test_page"

    assert updated_data["status"] == "printing"
    assert updated_data["cups_job_id"] == 555
    assert updated_data["id"] == job.id
    assert updated_data == test_page_service.serialize_print_job(job)


async def test_print_test_page_cups_failure_marks_job_failed_and_raises(monkeypatch, broadcast):
    monkeypatch.setattr(test_page_service, "CupsService", _FakeCupsServiceFailing)

    printer = _printer()
    user = _user()
    db = _FakeDB()

    with pytest.raises(test_page_service.TestPageError, match="printer offline"):
        await test_page_service.print_test_page(db, printer, user)

    job = db._job
    assert job.status == "failed"
    assert job.error_message == "printer offline"

    assert broadcast.await_count == 2
    (_channel, payload) = broadcast.await_args_list[1][0]
    assert payload["type"] == "job_updated"
    assert payload["data"]["status"] == "failed"


async def test_load_font_falls_back_when_size_kwarg_unsupported(monkeypatch):
    def fake_load_default(*args, **kwargs):
        if "size" in kwargs:
            raise TypeError("load_default() got an unexpected keyword argument 'size'")
        return "fallback-font"

    monkeypatch.setattr(ImageFont, "load_default", fake_load_default)

    assert test_page_service._load_font(40) == "fallback-font"
