"""On a `brothernet:` device, paper in the feeder takes over a glass request:
every sheet is kept and the result is one PDF whatever format was asked for.
Service and route behaviour against a port-0 fake scanner."""
import asyncio
import io
import os
import re

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select

from app.models import AppConfig, ScanJob
from app.routers import escl
from app.services import copy_service as copy_service_module
from app.services import scan_service as ss
from app.services import settings_cache
from app.services.copy_service import CopyService
from app.services.scan_service import ScanService
from tests.brother_helpers import NG, OK, FakeScanner, lease_reply, preflight
from tests.test_brother_feeder_drain import FEEDER_AREA, page, session
from tests.test_brothernet_backend import DEVICE, FLATBED, native, real_img2pdf  # noqa: F401
from tests.test_copy_service import _FakeCups
from tests.test_escl_feeder import _reset, tasks, use_printer  # noqa: F401
from tests.test_escl_held_pages import XML
from tests.test_scanners_native_api import _add

TWO_SHEETS = ("S>C", page(1) + page(2) + b"\x80")


def pdf_pages(path):
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", open(path, "rb").read()))


def fed_session(*body):
    return session(True, True, FEEDER_AREA, *(body or (TWO_SHEETS,)))


@pytest.mark.parametrize("signals", [(True, True), (True, False), (False, True)])
async def test_non_pdf_glass_request_with_paper_is_one_pdf_of_every_sheet(
    signals, tmp_path, native, real_img2pdf  # noqa: F811
):
    counts, fed = [], []
    scripts = [preflight(*signals), session(*signals, FEEDER_AREA, TWO_SHEETS)]
    async with FakeScanner(scripts) as srv:
        native(srv)
        scan_id, path = await ScanService().scan(
            resolution=300, mode="Color", fmt="jpeg", source="Flatbed", device=DEVICE,
            scan_dir=str(tmp_path), page_count_callback=counts.append,
            feeder_callback=lambda: fed.append(True), **FLATBED,
        )
    assert srv.errors == [] and srv.accepted == 2  # the START bytes carry no partial area
    assert path == str(tmp_path / f"{scan_id}.pdf") and pdf_pages(path) == 2
    assert counts == [2] and fed == [True]
    assert os.listdir(tmp_path) == [f"{scan_id}.pdf"]


@pytest.mark.parametrize("first", ["busy", "empty"])
async def test_paper_found_at_scan_time_is_fed_without_a_partial_area(
    first, tmp_path, native, real_img2pdf  # noqa: F811
):
    """The pre-check could not read the feeder, or saw it empty; paper is there
    when the scan connects."""
    counts, fed = [], []
    before = [NG] if first == "busy" else preflight(False, False)
    async with FakeScanner([before, fed_session()]) as srv:
        native(srv)
        scan_id, path = await ScanService().scan(
            resolution=300, mode="Color", fmt="tiff", source="Flatbed", device=DEVICE,
            scan_dir=str(tmp_path), page_count_callback=counts.append,
            feeder_callback=lambda: fed.append(True), **FLATBED,
        )
    assert srv.errors == []
    assert path.endswith(".pdf") and pdf_pages(path) == 2 and counts == [2] and fed == [True]
    assert os.listdir(tmp_path) == [os.path.basename(path)]


async def test_feeder_emptied_after_the_pre_check_falls_back_to_the_glass(
    tmp_path, native, real_img2pdf  # noqa: F811
):
    counts, fed = [], []
    glass = [
        OK,
        ("C>S", ss.bp.encode_lease_request(300, "CGRAY")),
        ("S>C", lease_reply(adf=2, w_px=64, h_mm=0, h_px=800)),
        ("C>S", ss.bp.CMD_FEEDER_QUERY),
        ("S>C", bytes([ss.bp.FEEDER_EMPTY])),
        ("C>S", ss.bp.encode_start(300, 300, "CGRAY", (0, 0, 64, 800))),
        ("S>C", page(1) + b"\x80"),
    ]
    async with FakeScanner([preflight(True, True), glass]) as srv:
        native(srv)
        _, path = await ScanService().scan(
            resolution=300, mode="Color", fmt="jpeg", source="Flatbed", device=DEVICE,
            scan_dir=str(tmp_path), page_count_callback=counts.append,
            feeder_callback=lambda: fed.append(True),
        )
    assert srv.errors == [] and counts == [1] and fed == []
    assert pdf_pages(path) == 1


async def test_glass_request_with_empty_feeder_is_unchanged(tmp_path, native):  # noqa: F811
    from tests.brother_helpers import load

    counts, fed = [], []
    async with FakeScanner([preflight(), load("20-fixture-mode-04")]) as srv:
        native(srv)
        scan_id, path = await ScanService().scan(
            resolution=300, mode="Color", fmt="tiff", source="Flatbed", device=DEVICE,
            scan_dir=str(tmp_path), page_count_callback=counts.append,
            feeder_callback=lambda: fed.append(True), **FLATBED,
        )
    assert srv.errors == [] and fed == []  # the recorded partial area was sent as is
    assert path == str(tmp_path / f"{scan_id}.tiff")


async def test_copy_prints_the_whole_stack_as_one_pdf(tmp_path, native, real_img2pdf, monkeypatch):  # noqa: F811
    cups = _FakeCups()
    monkeypatch.setattr(copy_service_module, "scan_service", ScanService())
    async with FakeScanner([preflight(True, True), fed_session()]) as srv:
        native(srv)
        result = await CopyService().copy(
            cups=cups, device=DEVICE, source="Flatbed", scan_dir=str(tmp_path)
        )
    assert srv.errors == []
    assert result["format"] == "pdf" and result["page_count"] == 2 and result["source"] == "ADF"
    assert cups.created[0][0].endswith(".pdf") and pdf_pages(cups.created[0][0]) == 2


@pytest_asyncio.fixture(loop_scope="function")
async def configured(db, tmp_path):
    db.add(AppConfig(key="escl_enabled", value="true"))
    db.add(AppConfig(key="scan_dir", value=str(tmp_path)))
    await db.commit()
    settings_cache.invalidate_all()
    return tmp_path


async def test_web_scan_asking_for_jpeg_is_delivered_as_the_pdf_it_became(
    db, admin_client, configured, native, real_img2pdf  # noqa: F811
):
    await _add(db)
    async with FakeScanner([preflight(True, True), fed_session()]) as srv:
        native(srv)
        resp = await admin_client.post(
            "/api/scanner/scan", json={"source": "Flatbed", "format": "jpeg"}
        )
    assert srv.errors == []
    body = resp.json()
    assert resp.status_code == 201
    assert (body["format"], body["source"], body["page_count"]) == ("pdf", "ADF", 2)
    row = (await db.execute(select(ScanJob))).scalars().first()
    await db.refresh(row)
    assert (row.format, row.source, row.page_count, row.status) == ("pdf", "ADF", 2, "completed")
    assert row.filepath.endswith(".pdf") and pdf_pages(row.filepath) == 2


async def _escl_job(client, monkeypatch, tasks, srv, fmt):  # noqa: F811
    use_printer(monkeypatch, srv.port)
    resp = await client.post(
        "/eSCL/ScanJobs", content=XML.decode().format(fmt=fmt, src="Platen").encode()
    )
    assert resp.status_code == 201
    job_id = resp.headers["Location"].rsplit("/", 1)[1]
    await asyncio.gather(*tasks)
    return job_id


async def _only_row(db):
    row = (await db.execute(select(ScanJob))).scalars().first()
    await db.refresh(row)
    return row


async def test_escl_jpeg_platen_job_with_paper_holds_every_sheet_as_jpeg(
    client, db, configured, native, real_img2pdf, monkeypatch, tasks  # noqa: F811
):
    async with FakeScanner([preflight(True, True), fed_session()]) as srv:
        job_id = await _escl_job(client, monkeypatch, tasks, srv, "image/jpeg")
    assert srv.errors == []
    for _ in range(2):
        r = await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert Image.open(io.BytesIO(r.content)).format == "JPEG"
    assert (await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")).status_code == 404
    row = await _only_row(db)
    assert (row.format, row.source, row.page_count) == ("pdf", "ADF", 2)


async def test_escl_pdf_platen_job_with_paper_is_one_document(
    client, db, configured, native, real_img2pdf, monkeypatch, tasks  # noqa: F811
):
    async with FakeScanner([preflight(True, True), fed_session()]) as srv:
        job_id = await _escl_job(client, monkeypatch, tasks, srv, "application/pdf")
    assert srv.errors == []
    r = await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert (await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")).status_code == 404
    row = await _only_row(db)
    assert (row.format, row.source, row.page_count) == ("pdf", "ADF", 2)


async def test_escl_jpeg_job_with_unreadable_feeder_and_late_paper_is_served_as_pdf(
    client, db, configured, native, real_img2pdf, monkeypatch, tasks  # noqa: F811
):
    async with FakeScanner([[NG], [NG], fed_session()]) as srv:
        job_id = await _escl_job(client, monkeypatch, tasks, srv, "image/jpeg")
    assert srv.errors == []
    r = await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    row = await _only_row(db)
    assert (row.format, row.source, row.page_count) == ("pdf", "ADF", 2)
    escl._scan_jobs.clear()
