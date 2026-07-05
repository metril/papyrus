"""Tests for the mDNS printer discovery service.

No real mDNS/network I/O runs here: every test monkeypatches
``app.services.discovery_service``'s ``AsyncZeroconf`` / ``AsyncServiceBrowser``
/ ``AsyncServiceInfo`` with in-process fakes. The fake browser fires its
Added/Updated events synchronously at construction time (rather than waiting
out the real browse window), so every test passes ``timeout=0`` and runs
instantly regardless of the machine's actual network.
"""
from zeroconf import ServiceStateChange

from app.services import discovery_service
from app.services.discovery_service import discover_printers

_IPP = "_ipp._tcp.local."
_IPPS = "_ipps._tcp.local."
_PRINTER = "_printer._tcp.local."


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
def _make_fake_zeroconf():
    """Returns a fake AsyncZeroconf class; ``.state["closed"]`` records
    whether async_close() ran."""
    state = {"closed": False}

    class FakeAsyncZeroconf:
        state_ref = state

        def __init__(self):
            self.zeroconf = object()

        async def async_close(self):
            state["closed"] = True

    return FakeAsyncZeroconf


def _make_fake_browser(events):
    """``events`` is a list of (service_type, name, state_change) triples,
    fired synchronously against the registered handler on construction —
    simulating responses that already arrived before the browse window
    ends. ``.state["cancelled"]`` records whether async_cancel() ran."""
    state = {"cancelled": False}

    class FakeAsyncServiceBrowser:
        state_ref = state

        def __init__(self, zeroconf, service_types, handlers):
            self.zeroconf = zeroconf
            self.service_types = service_types
            handler = handlers[0]
            for service_type, name, state_change in events:
                handler(zeroconf, service_type, name, state_change)

        async def async_cancel(self):
            state["cancelled"] = True

    return FakeAsyncServiceBrowser


def _make_fake_service_info(fixtures):
    """``fixtures`` maps (service_type, name) -> {"resolved": bool,
    "addresses": [...], "port": int, "properties": {bytes: bytes|None}}.
    A key missing from ``fixtures`` resolves to "not found" (resolved=False)."""

    class FakeAsyncServiceInfo:
        def __init__(self, service_type, name):
            self._fixture = fixtures.get((service_type, name), {})

        async def async_request(self, zc, timeout_ms):
            return self._fixture.get("resolved", False)

        def parsed_addresses(self, version):
            return self._fixture.get("addresses", [])

        @property
        def port(self):
            return self._fixture.get("port")

        @property
        def properties(self):
            return self._fixture.get("properties", {})

    return FakeAsyncServiceInfo


def _patch(monkeypatch, events, fixtures):
    zc_cls = _make_fake_zeroconf()
    browser_cls = _make_fake_browser(events)
    info_cls = _make_fake_service_info(fixtures)
    monkeypatch.setattr(discovery_service, "AsyncZeroconf", zc_cls)
    monkeypatch.setattr(discovery_service, "AsyncServiceBrowser", browser_cls)
    monkeypatch.setattr(discovery_service, "AsyncServiceInfo", info_cls)
    return zc_cls, browser_cls, info_cls


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
async def test_single_ipp_printer_full_txt(monkeypatch):
    name = "Brother HL-L2340DW._ipp._tcp.local."
    events = [(_IPP, name, ServiceStateChange.Added)]
    fixtures = {
        (_IPP, name): {
            "resolved": True,
            "addresses": ["192.0.2.5"],
            "port": 631,
            "properties": {
                b"ty": b"Brother HL-L2340DW series",
                b"note": b"Front Office",
                b"rp": b"ipp/print",
                b"UUID": b"e3248000-80ce-11db-8000-abcdef123456",
                b"pdl": b"application/pdf,image/urf",
            },
        }
    }
    _patch(monkeypatch, events, fixtures)

    result = await discover_printers(timeout=0)

    assert result == [
        {
            "name": "Brother HL-L2340DW",
            "ip": "192.0.2.5",
            "port": 631,
            "make_model": "Brother HL-L2340DW series",
            "location": "Front Office",
            "uri": "ipp://192.0.2.5:631/ipp/print",
            "uuid": "e3248000-80ce-11db-8000-abcdef123456",
            "protocols": ["ipp"],
        }
    ]


async def test_ipp_and_ipps_same_uuid_merge_into_one_entry(monkeypatch):
    name_ipp = "Office Printer._ipp._tcp.local."
    name_ipps = "Office Printer._ipps._tcp.local."
    events = [
        (_IPP, name_ipp, ServiceStateChange.Added),
        (_IPPS, name_ipps, ServiceStateChange.Added),
    ]
    fixtures = {
        (_IPP, name_ipp): {
            "resolved": True,
            "addresses": ["192.0.2.9"],
            "port": 631,
            "properties": {
                b"ty": b"Office Printer Model X",
                b"rp": b"ipp/print",
                b"UUID": b"same-uuid-1234",
            },
        },
        (_IPPS, name_ipps): {
            "resolved": True,
            "addresses": ["192.0.2.9"],
            "port": 443,
            "properties": {
                b"rp": b"ipp/print",
                b"UUID": b"same-uuid-1234",
            },
        },
    }
    _patch(monkeypatch, events, fixtures)

    result = await discover_printers(timeout=0)

    assert len(result) == 1
    device = result[0]
    assert device["uuid"] == "same-uuid-1234"
    assert set(device["protocols"]) == {"ipp", "ipps"}
    assert device["uri"] == "ipp://192.0.2.9:631/ipp/print"  # ipp preferred over ipps


async def test_printer_tcp_only_uses_lpd_uri(monkeypatch):
    name = "Legacy LPD Printer._printer._tcp.local."
    events = [(_PRINTER, name, ServiceStateChange.Added)]
    fixtures = {
        (_PRINTER, name): {
            "resolved": True,
            "addresses": ["192.0.2.20"],
            "port": 515,
            "properties": {},
        }
    }
    _patch(monkeypatch, events, fixtures)

    result = await discover_printers(timeout=0)

    assert len(result) == 1
    assert result[0]["protocols"] == ["lpd"]
    assert result[0]["uri"] == "lpd://192.0.2.20"


async def test_missing_txt_keys_yield_none_fields(monkeypatch):
    name = "Bare Printer._ipp._tcp.local."
    events = [(_IPP, name, ServiceStateChange.Added)]
    fixtures = {
        (_IPP, name): {
            "resolved": True,
            "addresses": ["192.0.2.30"],
            "port": 631,
            "properties": {},  # no ty / note / rp / UUID at all
        }
    }
    _patch(monkeypatch, events, fixtures)

    result = await discover_printers(timeout=0)

    assert len(result) == 1
    device = result[0]
    assert device["make_model"] is None
    assert device["location"] is None
    assert device["uuid"] is None
    assert device["uri"] == "ipp://192.0.2.30:631/ipp/print"  # default resource path


async def test_unresolvable_service_is_dropped(monkeypatch):
    resolvable = "Good Printer._ipp._tcp.local."
    unresolvable = "Ghost Printer._ipp._tcp.local."
    events = [
        (_IPP, resolvable, ServiceStateChange.Added),
        (_IPP, unresolvable, ServiceStateChange.Added),
    ]
    fixtures = {
        (_IPP, resolvable): {
            "resolved": True,
            "addresses": ["192.0.2.40"],
            "port": 631,
            "properties": {b"ty": b"Good Printer Model"},
        },
        # unresolvable entry: async_request() returns False (not in fixtures ->
        # defaults to resolved=False)
    }
    _patch(monkeypatch, events, fixtures)

    result = await discover_printers(timeout=0)

    assert len(result) == 1
    assert result[0]["name"] == "Good Printer"


async def test_resolved_with_no_ipv4_address_is_dropped(monkeypatch):
    name = "IPv6 Only Printer._ipp._tcp.local."
    events = [(_IPP, name, ServiceStateChange.Added)]
    fixtures = {
        (_IPP, name): {
            "resolved": True,
            "addresses": [],  # resolved, but no IPv4 address available
            "port": 631,
            "properties": {},
        }
    }
    _patch(monkeypatch, events, fixtures)

    result = await discover_printers(timeout=0)

    assert result == []


async def test_no_services_found_returns_empty_list(monkeypatch):
    zc_cls, browser_cls, _ = _patch(monkeypatch, events=[], fixtures={})

    result = await discover_printers(timeout=0)

    assert result == []
    assert browser_cls.state_ref["cancelled"] is True
    assert zc_cls.state_ref["closed"] is True


async def test_updated_state_change_is_also_collected(monkeypatch):
    name = "Updated Printer._ipp._tcp.local."
    events = [(_IPP, name, ServiceStateChange.Updated)]
    fixtures = {
        (_IPP, name): {
            "resolved": True,
            "addresses": ["192.0.2.50"],
            "port": 631,
            "properties": {},
        }
    }
    _patch(monkeypatch, events, fixtures)

    result = await discover_printers(timeout=0)

    assert len(result) == 1
    assert result[0]["ip"] == "192.0.2.50"


async def test_removed_state_change_is_ignored(monkeypatch):
    name = "Departed Printer._ipp._tcp.local."
    events = [(_IPP, name, ServiceStateChange.Removed)]
    _patch(monkeypatch, events, fixtures={})

    result = await discover_printers(timeout=0)

    assert result == []
