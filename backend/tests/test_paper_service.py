"""Loaded-paper lookup over IPP (get_printer_attributes mocked, no network)."""
import pytest

from app.services import paper_service
from app.services.ipp_client import IppError


@pytest.fixture(autouse=True)
def _clear_cache():
    paper_service._cache.clear()
    yield
    paper_service._cache.clear()


def _patch(monkeypatch, result):
    calls = []

    async def fake(host, port, resource, timeout):
        calls.append((host, port, resource, timeout))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(paper_service, "get_printer_attributes", fake)
    return calls


@pytest.mark.parametrize("keyword,label", [
    ("na_letter_8.5x11in", "Letter"),
    ("iso_a4_210x297mm", "A4"),
    ("na_legal_8.5x14in", "Legal"),
    ("iso_a5_148x210mm", None),
    ("custom_foo", None),
    (None, None),
])
def test_media_label(keyword, label):
    assert paper_service.media_label(keyword) == label


async def test_prefers_media_ready_over_default(monkeypatch):
    _patch(monkeypatch, {"media-ready": "na_letter_8.5x11in", "media-default": "iso_a4_210x297mm"})
    assert await paper_service.get_loaded_paper("ipp://10.0.0.5:631/ipp/print") == "Letter"


async def test_falls_back_to_media_default(monkeypatch):
    _patch(monkeypatch, {"media-ready": ["iso_a5_148x210mm"], "media-default": "iso_a4_210x297mm"})
    assert await paper_service.get_loaded_paper("ipp://10.0.0.5/ipp/print") == "A4"


async def test_unknown_media_is_none(monkeypatch):
    _patch(monkeypatch, {"media-ready": "om_small-photo_100x150mm"})
    assert await paper_service.get_loaded_paper("ipp://10.0.0.5/ipp/print") is None


async def test_ipp_failure_is_none_and_uses_short_timeout(monkeypatch):
    calls = _patch(monkeypatch, IppError("down"))
    assert await paper_service.get_loaded_paper("ipp://10.0.0.5/ipp/print") is None
    assert all(c[3] <= 3 for c in calls)


async def test_non_ipp_uri_is_none_without_network(monkeypatch):
    calls = _patch(monkeypatch, {"media-ready": "na_letter_8.5x11in"})
    assert await paper_service.get_loaded_paper("papyrus:/") is None
    assert await paper_service.get_loaded_paper(None) is None
    assert calls == []


async def test_result_is_cached(monkeypatch):
    calls = _patch(monkeypatch, {"media-ready": "na_letter_8.5x11in"})
    uri = "ipp://10.0.0.5/ipp/print"
    await paper_service.get_loaded_paper(uri)
    await paper_service.get_loaded_paper(uri)
    assert len(calls) == 1
