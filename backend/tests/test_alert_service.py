"""Unit tests for ``app.services.alert_service.check_alerts``.

No real DB or CUPS/IPP: a tiny fake AsyncSession serves the ``Printer`` query
and round-trips the ``alert_state`` AppConfig row in memory (so hysteresis is
exercised across successive polls), ``CupsService``/``probe_ipp`` are
monkeypatched at the ``alert_service`` module level, ``get_setting`` is
replaced with a dict-backed fake, and ``dispatch_webhook`` /
``email_service.send_alert`` are captured to count dispatches.
"""
import json
from types import SimpleNamespace

import pytest

from app.models import AppConfig
from app.services import alert_service


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return list(self._rows)


class _FakeDB:
    """Serves the Printer select and stores AppConfig rows (alert_state) in a
    dict so ``_load_alert_state``/``_save_alert_state`` round-trip in memory."""

    def __init__(self, printers):
        self._printers = printers
        self.store: dict[str, AppConfig] = {}
        self.commits = 0

    async def execute(self, _stmt):
        return _FakeResult(self._printers)

    async def get(self, _model, key):
        return self.store.get(key)

    def add(self, obj):
        self.store[obj.key] = obj

    async def commit(self):
        self.commits += 1

    # convenience for assertions
    def saved_state(self) -> dict:
        row = self.store.get("alert_state")
        return json.loads(row.value) if row else {}


def _printer(pid=1, cups_name="brother", uri="", display_name="Brother"):
    return SimpleNamespace(
        id=pid, cups_name=cups_name, uri=uri, display_name=display_name,
        is_network_queue=False,
    )


def _status(state=3, markers=None, state_reasons=None):
    return {
        "state": state,
        "state_message": "",
        "accepting_jobs": True,
        "markers": markers or [],
        "state_reasons": state_reasons or [],
    }


@pytest.fixture
def harness(monkeypatch):
    """Wire alert_service's collaborators to controllable fakes.

    Returns an object exposing:
      - ``settings``: dict backing get_setting (defaults: enabled, threshold 20)
      - ``status_by_queue``: cups_name -> status dict returned by fake CupsService
      - ``ipp_by_host``: host -> normalized probe dict (or None)
      - ``webhooks``: list of (event, data) dispatched
      - ``emails``: list of (to, subject, body)
    """
    settings = {
        "alerts_enabled": "true",
        "alert_toner_threshold": "20",
        "alert_email": "ops@example.com",
    }
    status_by_queue: dict[str, dict] = {}
    ipp_by_host: dict[str, dict] = {}
    webhooks: list[tuple[str, dict]] = []
    emails: list[tuple[str, str, str]] = []

    async def fake_get_setting(_db, key):
        return settings.get(key)

    monkeypatch.setattr("app.routers.settings.get_setting", fake_get_setting)

    class _FakeCups:
        def __init__(self, printer_name):
            self.printer_name = printer_name

        async def get_printer_status(self):
            return status_by_queue.get(self.printer_name, _status())

    monkeypatch.setattr(alert_service, "CupsService", _FakeCups)

    async def fake_probe(host, *args, **kwargs):
        return ipp_by_host.get(host)

    monkeypatch.setattr(alert_service, "probe_ipp", fake_probe)

    async def fake_dispatch(_db, event, data):
        webhooks.append((event, data))

    monkeypatch.setattr(alert_service, "dispatch_webhook", fake_dispatch)

    async def fake_send_alert(_db, to, subject, body):
        emails.append((to, subject, body))

    monkeypatch.setattr(alert_service.email_service, "send_alert", fake_send_alert)

    return SimpleNamespace(
        settings=settings,
        status_by_queue=status_by_queue,
        ipp_by_host=ipp_by_host,
        webhooks=webhooks,
        emails=emails,
    )


# --------------------------------------------------------------------------- #
# Onset / no-repeat / recovery-rearm
# --------------------------------------------------------------------------- #
async def test_toner_crossing_fires_exactly_one_webhook_and_one_email(harness):
    harness.status_by_queue["brother"] = _status(markers=[{"name": "Black", "level": 5}])
    db = _FakeDB([_printer()])

    await alert_service.check_alerts(db)

    assert len(harness.webhooks) == 1
    event, data = harness.webhooks[0]
    assert event == "printer.supply_low"
    assert data["resolved"] is False
    assert data["printer_id"] == 1
    assert len(harness.emails) == 1
    assert harness.emails[0][0] == "ops@example.com"
    # persisted so a repeat poll won't re-fire
    assert db.saved_state()["1"]["supply_low"] is True


async def test_second_poll_same_state_fires_nothing(harness):
    harness.status_by_queue["brother"] = _status(markers=[{"name": "Black", "level": 5}])
    db = _FakeDB([_printer()])

    await alert_service.check_alerts(db)
    await alert_service.check_alerts(db)  # unchanged -> no new fire

    assert len(harness.webhooks) == 1
    assert len(harness.emails) == 1


async def test_recovery_resets_and_next_crossing_refires(harness):
    printers = [_printer()]
    db = _FakeDB(printers)

    # Onset
    harness.status_by_queue["brother"] = _status(markers=[{"name": "Black", "level": 5}])
    await alert_service.check_alerts(db)

    # Recovery: level back up -> resolved webhook, NO email
    harness.status_by_queue["brother"] = _status(markers=[{"name": "Black", "level": 80}])
    await alert_service.check_alerts(db)

    # Cross again -> fires onset again
    harness.status_by_queue["brother"] = _status(markers=[{"name": "Black", "level": 5}])
    await alert_service.check_alerts(db)

    events = [e for e, _ in harness.webhooks]
    assert events == ["printer.supply_low", "printer.supply_low", "printer.supply_low"]
    resolved_flags = [d["resolved"] for _, d in harness.webhooks]
    assert resolved_flags == [False, True, False]
    # Recovery must not email; only the two onsets do.
    assert len(harness.emails) == 2


# --------------------------------------------------------------------------- #
# Disabled / unknown levels
# --------------------------------------------------------------------------- #
async def test_disabled_alerts_do_nothing(harness):
    harness.settings["alerts_enabled"] = "false"
    harness.status_by_queue["brother"] = _status(markers=[{"name": "Black", "level": 1}])
    db = _FakeDB([_printer()])

    await alert_service.check_alerts(db)

    assert harness.webhooks == []
    assert harness.emails == []
    assert db.saved_state() == {}  # no state written when disabled


async def test_unknown_marker_level_never_alerts(harness):
    # -1 == unknown; absent level also unknown. Neither should alert.
    harness.status_by_queue["brother"] = _status(
        markers=[{"name": "Black", "level": -1}, {"name": "Cyan", "level": -3}]
    )
    db = _FakeDB([_printer()])

    await alert_service.check_alerts(db)

    assert harness.webhooks == []
    assert harness.emails == []
    assert db.saved_state()["1"]["supply_low"] is False


# --------------------------------------------------------------------------- #
# Error reasons / offline
# --------------------------------------------------------------------------- #
async def test_jam_state_reason_fires_printer_error(harness):
    harness.status_by_queue["brother"] = _status(
        state=3, state_reasons=["media-jam-warning"]
    )
    db = _FakeDB([_printer()])

    await alert_service.check_alerts(db)

    events = [e for e, _ in harness.webhooks]
    assert events == ["printer.error"]
    assert harness.webhooks[0][1]["state_reasons"] == ["media-jam-warning"]


async def test_stopped_printer_fires_offline_printer_error(harness):
    harness.status_by_queue["brother"] = _status(state=5)  # stopped/unreachable
    db = _FakeDB([_printer()])

    await alert_service.check_alerts(db)

    events = [e for e, _ in harness.webhooks]
    assert events == ["printer.error"]
    assert harness.webhooks[0][1]["reason"] == "offline"


# --------------------------------------------------------------------------- #
# Email-absent still fires webhook; IPP enrichment; stale-id pruning
# --------------------------------------------------------------------------- #
async def test_webhook_fires_even_when_no_alert_email_configured(harness):
    harness.settings["alert_email"] = ""
    harness.status_by_queue["brother"] = _status(markers=[{"name": "Black", "level": 5}])
    db = _FakeDB([_printer()])

    await alert_service.check_alerts(db)

    assert len(harness.webhooks) == 1
    assert harness.emails == []  # no email configured, but webhook still went


async def test_ipp_markers_enrich_when_uri_is_ip_based(harness):
    # CUPS reports clean; the low level comes only from the IPP probe.
    harness.status_by_queue["brother"] = _status(markers=[])
    harness.ipp_by_host["192.168.1.50"] = {
        "state_reasons": [],
        "markers": {"names": ["Toner"], "levels": [3]},
    }
    db = _FakeDB([_printer(uri="ipp://192.168.1.50/ipp/print")])

    await alert_service.check_alerts(db)

    events = [e for e, _ in harness.webhooks]
    assert events == ["printer.supply_low"]


async def test_stale_printer_ids_are_pruned_from_state(harness):
    # First poll: printer 1 is low -> state {"1": {...}}
    harness.status_by_queue["brother"] = _status(markers=[{"name": "Black", "level": 5}])
    db = _FakeDB([_printer(pid=1, cups_name="brother")])
    await alert_service.check_alerts(db)
    assert "1" in db.saved_state()

    # Printer 1 is deleted and replaced by printer 2 in a later poll; the
    # persisted state must not keep a stale row for the gone printer.
    db._printers = [_printer(pid=2, cups_name="epson")]
    harness.status_by_queue["epson"] = _status(markers=[])
    await alert_service.check_alerts(db)

    saved = db.saved_state()
    assert "1" not in saved
    assert "2" in saved
