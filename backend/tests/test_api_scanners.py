"""Scanners admin API suite (``app.routers.scanners``) — CRUD default
promotion (F35) and probe IP validation (F36).

No existing suite covered add/delete/set-default on this router (only the
singular ``scanner.py`` scan-operations router and its bulk-delete
permission test existed); this mirrors test_api_printers.py's conventions
for the equivalent printers admin surface.
"""
import pytest

from app.models import Scanner
from app.routers import scanners as scanners_router


# --------------------------------------------------------------------------- #
# F35: default scanner promotion
# --------------------------------------------------------------------------- #
async def test_first_scanner_added_becomes_default(db, admin_client):
    resp = await admin_client.post(
        "/api/scanners", json={"name": "First", "device": "test:device:1"}
    )
    assert resp.status_code == 201
    assert resp.json()["is_default"] is True


async def test_second_scanner_added_is_not_default(db, admin_client):
    first = await admin_client.post(
        "/api/scanners", json={"name": "One", "device": "test:device:1"}
    )
    assert first.json()["is_default"] is True

    second = await admin_client.post(
        "/api/scanners", json={"name": "Two", "device": "test:device:2"}
    )
    assert second.json()["is_default"] is False


async def test_deleting_default_scanner_promotes_oldest_remaining(db, admin_client):
    first = await admin_client.post(
        "/api/scanners", json={"name": "One", "device": "test:device:1"}
    )
    await admin_client.post("/api/scanners", json={"name": "Two", "device": "test:device:2"})
    first_id = first.json()["id"]
    assert first.json()["is_default"] is True

    del_resp = await admin_client.delete(f"/api/scanners/{first_id}")
    assert del_resp.status_code == 204

    listing = (await admin_client.get("/api/scanners")).json()
    two = next(s for s in listing if s["name"] == "Two")
    assert two["is_default"] is True


async def test_deleting_non_default_scanner_does_not_touch_default(db, admin_client):
    first = await admin_client.post(
        "/api/scanners", json={"name": "One", "device": "test:device:1"}
    )
    second = await admin_client.post(
        "/api/scanners", json={"name": "Two", "device": "test:device:2"}
    )
    second_id = second.json()["id"]

    del_resp = await admin_client.delete(f"/api/scanners/{second_id}")
    assert del_resp.status_code == 204

    listing = (await admin_client.get("/api/scanners")).json()
    one = next(s for s in listing if s["name"] == "One")
    assert one["id"] == first.json()["id"]
    assert one["is_default"] is True


async def test_set_default_scanner_clears_previous_default(db, admin_client):
    """F11: set_default_scanner clears the previous default in the same
    transaction it sets the new one (two statements -- see the F11
    coordinator ruling in printers.set_default_printer)."""
    p1 = Scanner(name="One", device="test:device:1", is_default=True)
    p2 = Scanner(name="Two", device="test:device:2")
    db.add_all([p1, p2])
    await db.commit()
    await db.refresh(p1)
    await db.refresh(p2)

    resp = await admin_client.post(f"/api/scanners/{p2.id}/default")
    assert resp.status_code == 200
    assert resp.json()["is_default"] is True

    p1_id = p1.id
    await db.rollback()
    refreshed_p1 = await db.get(Scanner, p1_id)
    assert refreshed_p1.is_default is False


async def test_set_default_scanner_swap_back_to_earlier_scanner_does_not_500(db, admin_client):
    """Regression (F11 review finding, CRITICAL): mirrors the printers-side
    regression -- a single-statement `SET is_default = (id = :id)` violates
    the partial unique index (ux_scanners_default) depending on heap-scan
    order. Toggle forward then back against the real test Postgres."""
    p1 = Scanner(name="One", device="test:device:1", is_default=True)
    p2 = Scanner(name="Two", device="test:device:2")
    db.add_all([p1, p2])
    await db.commit()
    await db.refresh(p1)
    await db.refresh(p2)

    forward = await admin_client.post(f"/api/scanners/{p2.id}/default")
    assert forward.status_code == 200
    assert forward.json()["is_default"] is True

    back = await admin_client.post(f"/api/scanners/{p1.id}/default")
    assert back.status_code == 200
    assert back.json()["is_default"] is True

    listing = (await admin_client.get("/api/scanners")).json()
    by_name = {s["name"]: s for s in listing}
    assert by_name["One"]["is_default"] is True
    assert by_name["Two"]["is_default"] is False


# --------------------------------------------------------------------------- #
# F36: probe IP validation (SSRF hardening)
# --------------------------------------------------------------------------- #
async def test_scanner_probe_unparseable_ip_is_400(admin_client):
    resp = await admin_client.get(
        "/api/scanners/probe", params={"ip": "169.254.169.254/latest/meta-data"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid IP address"


async def test_scanner_probe_loopback_ip_is_400(admin_client):
    resp = await admin_client.get("/api/scanners/probe", params={"ip": "127.0.0.1"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid IP address"


async def test_scanner_probe_link_local_ip_is_400(admin_client):
    resp = await admin_client.get("/api/scanners/probe", params={"ip": "169.254.169.254"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid IP address"


async def test_scanner_probe_multicast_ip_is_400(admin_client):
    resp = await admin_client.get("/api/scanners/probe", params={"ip": "224.0.0.1"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid IP address"


# --------------------------------------------------------------------------- #
# F36: _write_airscan_device rejects unsafe characters
# --------------------------------------------------------------------------- #
def test_write_airscan_device_rejects_newline_in_name(tmp_path, monkeypatch):
    conf = tmp_path / "papyrus.conf"
    monkeypatch.setattr(scanners_router, "AIRSCAN_PAPYRUS_CONF", str(conf))

    with pytest.raises(ValueError):
        scanners_router._write_airscan_device("Evil\nName", "http://1.2.3.4/eSCL", "eSCL")

    assert not conf.exists()


def test_write_airscan_device_rejects_quote_in_url(tmp_path, monkeypatch):
    conf = tmp_path / "papyrus.conf"
    monkeypatch.setattr(scanners_router, "AIRSCAN_PAPYRUS_CONF", str(conf))

    with pytest.raises(ValueError):
        scanners_router._write_airscan_device(
            "Scanner", 'http://1.2.3.4/eSCL"injected', "eSCL"
        )

    assert not conf.exists()


def test_write_airscan_device_accepts_normal_values(tmp_path, monkeypatch):
    conf = tmp_path / "papyrus.conf"
    monkeypatch.setattr(scanners_router, "AIRSCAN_PAPYRUS_CONF", str(conf))

    scanners_router._write_airscan_device("Brother", "http://1.2.3.4/eSCL", "eSCL")

    assert conf.exists()
    assert '"Brother" = http://1.2.3.4/eSCL, eSCL' in conf.read_text()
