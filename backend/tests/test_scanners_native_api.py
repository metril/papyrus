"""Router-level behaviour for native Brother scanners: probe-backed status,
/test, /diagnostics, /discover, health, options and /register-brscan4. The
fake scanner lives on 127.0.0.1:<port 0>, so the SSRF guard is patched out."""
import asyncio

import pytest

from app.models import Scanner
from app.routers import scanners as scanners_router
from app.routers import system as system_router
from app.services import brother_protocol as bp
from app.services import scan_service as ss
from app.services.scan_service import BrotherNetBackend
from tests.brother_helpers import NG, OK, FakeScanner, lease_reply

DEVICE = "brothernet:127.0.0.1"
LEASE = [OK, ("C>S", bp.encode_lease_request(300, "TEXT")), ("S>C", lease_reply())]


@pytest.fixture
def native(monkeypatch):
    def install(srv):
        monkeypatch.setattr(
            ss, "_brothernet_backend", BrotherNetBackend(srv.port, retry_delay=0, idle_timeout=5)
        )

    async def allow(_host):
        return None

    monkeypatch.setattr(ss, "assert_safe_host", allow)
    return install


async def _add(db, device=DEVICE, name="Native", default=True):
    row = Scanner(name=name, device=device, is_default=default)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def test_scanner_test_endpoint_native(db, admin_client, native):
    row = await _add(db)
    async with FakeScanner([LEASE]) as srv:
        native(srv)
        resp = await admin_client.get(f"/api/scanners/{row.id}/test")
    body = resp.json()
    assert resp.status_code == 200 and body["sane_ok"] is True and body["busy"] is False
    assert body["escl_ok"] is False and srv.errors == []


async def test_scanner_test_endpoint_native_busy_is_reachable(db, admin_client, native):
    row = await _add(db)
    async with FakeScanner([[NG]]) as srv:
        native(srv)
        body = (await admin_client.get(f"/api/scanners/{row.id}/test")).json()
    assert body["sane_ok"] is True and body["busy"] is True


async def test_scanner_test_endpoint_native_unreachable(db, admin_client, native):
    row = await _add(db)
    async with FakeScanner([]) as srv:
        native(srv)
    body = (await admin_client.get(f"/api/scanners/{row.id}/test")).json()
    assert body["sane_ok"] is False and body["sane_error"]


async def test_status_endpoint_uses_probe(db, admin_client, native):
    await _add(db)
    async with FakeScanner([LEASE]) as srv:
        native(srv)
        body = (await admin_client.get("/api/scanner/status")).json()
    assert body["available"] is True and body["device"] == DEVICE


async def test_malformed_native_device_status_is_422(db, admin_client):
    await _add(db, device="brothernet:bad host")
    resp = await admin_client.get("/api/scanner/status")
    assert resp.status_code == 422


async def test_options_follow_default_device(db, admin_client):
    await _add(db, device="brothernet:10.0.0.5")
    native_opts = (await admin_client.get("/api/scanner/options")).json()
    assert native_opts["resolutions"] == [100, 150, 200, 300, 400, 600]


async def test_options_sane_default_unchanged(db, admin_client):
    await _add(db, device="airscan:e:x")
    opts = (await admin_client.get("/api/scanner/options")).json()
    assert opts["resolutions"][0] == 75


async def test_diagnostics_has_native_section(db, admin_client, native, monkeypatch):
    await _add(db)

    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    async with FakeScanner([LEASE]) as srv:
        native(srv)
        body = (await admin_client.get("/api/scanners/diagnostics")).json()
    assert body["native"] == [
        {"name": "Native", "device": DEVICE, "available": True, "busy": False, "error": None}
    ]
    assert body["scanimage_list"].startswith("Error")


@pytest.fixture
def mdns(monkeypatch):
    def install(records):
        async def fake():
            return records

        monkeypatch.setattr(scanners_router, "discover_scanner_services", fake)

    return install


def _rec(ip, model="DCP-L2540DW series", port=bp.PORT):
    return {"name": f"Brother {ip}", "ip": ip, "port": port, "model": model,
            "flatbed": True, "feeder": True}


async def test_discover_lists_supported_and_untested(admin_client, mdns, monkeypatch):
    probed = []

    async def reachable(device):
        probed.append(device)
        return {"available": True, "busy": False, "device": device, "output": ""}

    monkeypatch.setattr(scanners_router.scan_service, "check_device", reachable)

    async def fake_exec(*args, **kwargs):
        return _SaneList()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    mdns([_rec("127.0.0.1"), _rec("127.0.0.2", model="MFC-L9999CDW"),
          _rec("127.0.0.3", port=8080)])
    body = (await admin_client.get("/api/scanners/discover")).json()
    assert sorted(probed) == ["brothernet:127.0.0.1", "brothernet:127.0.0.2"]
    assert body[0] == {"device": "brother4:net1;dev0", "description": "Brother DCP"}
    by_ip = {d["ip"]: d for d in body if "ip" in d}
    assert set(by_ip) == {"127.0.0.1", "127.0.0.2"}
    assert by_ip["127.0.0.1"]["device"] == DEVICE and by_ip["127.0.0.1"]["supported"] is True
    other = by_ip["127.0.0.2"]
    assert other["device"] is None and other["supported"] is False
    assert "untested" in other["description"]


async def test_discover_drops_unconfirmed_candidates(admin_client, native, mdns, monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _SaneList()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    mdns([_rec("127.0.0.1")])
    async with FakeScanner([]) as srv:
        native(srv)
    body = (await admin_client.get("/api/scanners/discover")).json()
    assert [d["device"] for d in body] == ["brother4:net1;dev0"]


async def test_discover_survives_mdns_failure(admin_client, monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _SaneList()

    async def boom():
        raise RuntimeError("no multicast")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(scanners_router, "discover_scanner_services", boom)
    resp = await admin_client.get("/api/scanners/discover")
    assert resp.status_code == 200 and len(resp.json()) == 1


class _SaneList:
    returncode = 0

    async def communicate(self):
        return b"device `brother4:net1;dev0' is a Brother DCP\n", b""


async def test_register_brscan4_missing_binary_is_domain_error(admin_client, monkeypatch):
    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    resp = await admin_client.post(
        "/api/scanners/register-brscan4", json={"name": "x", "model": "m", "ip": "10.0.0.2"}
    )
    assert resp.status_code == 422
    assert "native" in resp.json()["detail"] and "brsaneconfig4" not in resp.json()["detail"]


async def test_health_probes_native_default(monkeypatch, native):
    system_router._reset_health_cache()

    async def device():
        return DEVICE

    monkeypatch.setattr(system_router, "_default_native_device", device)
    async with FakeScanner([[NG]]) as srv:
        native(srv)
        _, scanner_ok = await system_router._probe_subsystems()
    system_router._reset_health_cache()
    assert scanner_ok is True


async def test_health_native_unreachable(monkeypatch, native):
    system_router._reset_health_cache()

    async def device():
        return DEVICE

    monkeypatch.setattr(system_router, "_default_native_device", device)
    async with FakeScanner([]) as srv:
        native(srv)
    _, scanner_ok = await system_router._probe_subsystems()
    system_router._reset_health_cache()
    assert scanner_ok is False
