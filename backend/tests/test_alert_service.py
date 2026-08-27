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
      - ``status_by_queue``: '<cups_name>_release' -> status dict returned by
        fake CupsService (F34: alert_service reads the printer's real device
        status from its release queue, not its cups_name hold queue)
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

    # _cups_reachable() calls the real cups.Connection().getPrinters() (F126's
    # health probe). Locally that's a no-op MagicMock (tests/conftest.py
    # stubs the `cups` module when pycups isn't installed), so it always
    # "succeeds" -- but CI installs real pycups with no cupsd listening, so
    # the real call would raise and make every test below with a configured
    # printer see a false CUPS outage and return before evaluating anything.
    # Default it to reachable; the one test that exercises the outage path
    # overrides this back to False itself.
    async def fake_cups_reachable() -> bool:
        return True

    monkeypatch.setattr(alert_service, "_cups_reachable", fake_cups_reachable)

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
    harness.status_by_queue["brother_release"] = _status(markers=[{"name": "Black", "level": 5}])
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
    harness.status_by_queue["brother_release"] = _status(markers=[{"name": "Black", "level": 5}])
    db = _FakeDB([_printer()])

    await alert_service.check_alerts(db)
    await alert_service.check_alerts(db)  # unchanged -> no new fire

    assert len(harness.webhooks) == 1
    assert len(harness.emails) == 1


async def test_recovery_resets_and_next_crossing_refires(harness):
    printers = [_printer()]
    db = _FakeDB(printers)

    # Onset
    harness.status_by_queue["brother_release"] = _status(markers=[{"name": "Black", "level": 5}])
    await alert_service.check_alerts(db)

    # Recovery: level back up -> resolved webhook, NO email
    harness.status_by_queue["brother_release"] = _status(markers=[{"name": "Black", "level": 80}])
    await alert_service.check_alerts(db)

    # Cross again -> fires onset again
    harness.status_by_queue["brother_release"] = _status(markers=[{"name": "Black", "level": 5}])
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
    harness.status_by_queue["brother_release"] = _status(markers=[{"name": "Black", "level": 1}])
    db = _FakeDB([_printer()])

    await alert_service.check_alerts(db)

    assert harness.webhooks == []
    assert harness.emails == []
    assert db.saved_state() == {}  # no state written when disabled


async def test_unknown_marker_level_never_alerts(harness):
    # -1 == unknown; absent level also unknown. Neither should alert.
    harness.status_by_queue["brother_release"] = _status(
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
    harness.status_by_queue["brother_release"] = _status(
        state=3, state_reasons=["media-jam-warning"]
    )
    db = _FakeDB([_printer()])

    await alert_service.check_alerts(db)

    events = [e for e, _ in harness.webhooks]
    assert events == ["printer.error"]
    assert harness.webhooks[0][1]["state_reasons"] == ["media-jam-warning"]


async def test_stopped_printer_fires_offline_printer_error(harness):
    harness.status_by_queue["brother_release"] = _status(state=5)  # stopped/unreachable
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
    harness.status_by_queue["brother_release"] = _status(markers=[{"name": "Black", "level": 5}])
    db = _FakeDB([_printer()])

    await alert_service.check_alerts(db)

    assert len(harness.webhooks) == 1
    assert harness.emails == []  # no email configured, but webhook still went


async def test_ipp_markers_enrich_when_uri_is_ip_based(harness):
    # CUPS reports clean; the low level comes only from the IPP probe.
    harness.status_by_queue["brother_release"] = _status(markers=[])
    harness.ipp_by_host["192.168.1.50"] = {
        "state_reasons": [],
        "markers": {"names": ["Toner"], "levels": [3]},
    }
    db = _FakeDB([_printer(uri="ipp://192.168.1.50/ipp/print")])

    await alert_service.check_alerts(db)

    events = [e for e, _ in harness.webhooks]
    assert events == ["printer.supply_low"]


# --------------------------------------------------------------------------- #
# F126 — a total CUPS outage must not fire a false offline onset for every
# printer; state is carried forward untouched.
# --------------------------------------------------------------------------- #
async def test_cups_totally_unreachable_carries_state_forward_and_fires_nothing(
    harness, monkeypatch
):
    db = _FakeDB([_printer(pid=1, cups_name="brother")])
    # Seed a prior sweep's persisted state directly, as if the printer was
    # previously healthy on every condition.
    prior = {"1": {"supply_low": False, "error": False, "offline": False}}
    db.add(AppConfig(key="alert_state", value=json.dumps(prior)))

    async def cups_down() -> bool:
        return False

    monkeypatch.setattr(alert_service, "_cups_reachable", cups_down)

    await alert_service.check_alerts(db)

    assert harness.webhooks == []
    assert harness.emails == []
    # Untouched -- not reset, not re-derived from the (unreachable) status.
    assert db.saved_state() == prior


async def test_empty_printer_list_skips_the_cups_reachability_probe(harness):
    """No printers configured -> nothing to probe CUPS for; must not treat an
    empty printer list as a CUPS outage."""
    db = _FakeDB([])

    await alert_service.check_alerts(db)

    assert harness.webhooks == []
    assert db.saved_state() == {}


# --------------------------------------------------------------------------- #
# F127 — a marker visible to both CUPS and the IPP probe must not be counted
# twice.
# --------------------------------------------------------------------------- #
async def test_marker_seen_in_both_cups_and_ipp_is_not_duplicated(harness):
    harness.status_by_queue["brother_release"] = _status(markers=[{"name": "Black", "level": 5}])
    harness.ipp_by_host["192.168.1.50"] = {
        "state_reasons": [],
        "markers": {"names": ["Black"], "levels": [5]},
    }
    db = _FakeDB([_printer(uri="ipp://192.168.1.50/ipp/print")])

    await alert_service.check_alerts(db)

    assert len(harness.webhooks) == 1
    _, data = harness.webhooks[0]
    assert data["markers"] == [{"name": "Black", "level": 5}]


async def test_ipp_marker_absent_from_cups_still_fills_the_gap(harness):
    """A marker CUPS didn't report but IPP did must still come through --
    the dedupe must not drop genuinely distinct markers."""
    harness.status_by_queue["brother_release"] = _status(markers=[{"name": "Black", "level": 50}])
    harness.ipp_by_host["192.168.1.50"] = {
        "state_reasons": [],
        "markers": {"names": ["Black", "Cyan"], "levels": [50, 3]},
    }
    db = _FakeDB([_printer(uri="ipp://192.168.1.50/ipp/print")])

    await alert_service.check_alerts(db)

    assert len(harness.webhooks) == 1
    _, data = harness.webhooks[0]
    assert data["markers"] == [{"name": "Cyan", "level": 3}]


async def test_ipp_marker_fills_a_missing_level_cups_reported_as_unknown(harness):
    """Regression: the F127 dedupe must not suppress a real low-supply alert.
    CUPS reports the same physical marker but with an unusable level (-1,
    which CupsService emits whenever marker-levels is shorter than
    marker-names or absent), while the IPP probe reports a real level for
    it. The merge must keep the *usable* level, not silently prefer CUPS's
    unknown one just because CUPS was seen first."""
    harness.status_by_queue["brother_release"] = _status(markers=[{"name": "Black", "level": -1}])
    harness.ipp_by_host["192.168.1.50"] = {
        "state_reasons": [],
        "markers": {"names": ["black"], "levels": [5]},
    }
    db = _FakeDB([_printer(uri="ipp://192.168.1.50/ipp/print")])

    await alert_service.check_alerts(db)

    events = [e for e, _ in harness.webhooks]
    assert events == ["printer.supply_low"]


# --------------------------------------------------------------------------- #
# F128 — alert_state is durable after each printer's transitions, not only
# once at the very end of the sweep.
# --------------------------------------------------------------------------- #
async def test_state_saved_after_each_printer_not_only_at_sweep_end(harness):
    harness.status_by_queue["brother_release"] = _status(markers=[{"name": "Black", "level": 5}])
    harness.status_by_queue["epson_release"] = _status(markers=[{"name": "Cyan", "level": 5}])
    printers = [_printer(pid=1, cups_name="brother"), _printer(pid=2, cups_name="epson")]
    db = _FakeDB(printers)

    await alert_service.check_alerts(db)

    # One persist per printer (2), not a single persist at the very end.
    assert db.commits == len(printers)


async def test_printer1_state_survives_a_crash_dispatching_printer2(harness, monkeypatch):
    """Regression (F128): if something blows up processing printer 2 (e.g. a
    webhook POST that doesn't come back before the container restarts),
    printer 1's already-computed, already-dispatched transition must already
    be durably persisted -- not lost, which would otherwise re-fire printer
    1's onset again on the next poll even though it already fired once.

    Also proves the seeding fix: printer 2's *prior* True flag (persisted
    from an earlier, fully-completed sweep, before this sweep even started)
    must not be wiped out by printer 1's incremental save just because
    printer 2 hasn't been re-evaluated yet this cycle. A `new_state` that
    starts empty and is written in full on every save would truncate the
    persisted row to only the printers visited so far, silently discarding
    printer 2's True flags the moment printer 1's save fires.
    """
    harness.status_by_queue["brother_release"] = _status(markers=[{"name": "Black", "level": 5}])
    # Printer 2's error condition is already active from a prior sweep (no
    # new transition -> no dispatch for it); its supply_low condition is
    # what newly transitions this sweep, triggering (and crashing) dispatch.
    harness.status_by_queue["epson_release"] = _status(
        markers=[{"name": "Cyan", "level": 5}], state_reasons=["media-jam-warning"]
    )
    printers = [_printer(pid=1, cups_name="brother"), _printer(pid=2, cups_name="epson")]
    db = _FakeDB(printers)

    prior = {
        "1": {"supply_low": False, "error": False, "offline": False},
        "2": {"supply_low": False, "error": True, "offline": False},
    }
    db.add(AppConfig(key="alert_state", value=json.dumps(prior)))

    async def flaky_dispatch(_db, event, data):
        if data["printer_id"] == 2:
            raise RuntimeError("simulated crash mid-dispatch")
        harness.webhooks.append((event, data))

    monkeypatch.setattr(alert_service, "dispatch_webhook", flaky_dispatch)

    with pytest.raises(RuntimeError):
        await alert_service.check_alerts(db)

    saved = db.saved_state()
    # Printer 1's own onset this sweep is durable.
    assert saved.get("1", {}).get("supply_low") is True
    # Printer 2's prior True flag survives the crash -- not wiped to {} (or
    # dropped from the map entirely) just because printer 2 was never
    # successfully reprocessed this sweep.
    assert saved.get("2", {}).get("error") is True


async def test_stale_printer_ids_are_pruned_from_state(harness):
    # First poll: printer 1 is low -> state {"1": {...}}
    harness.status_by_queue["brother_release"] = _status(markers=[{"name": "Black", "level": 5}])
    db = _FakeDB([_printer(pid=1, cups_name="brother")])
    await alert_service.check_alerts(db)
    assert "1" in db.saved_state()

    # Printer 1 is deleted and replaced by printer 2 in a later poll; the
    # persisted state must not keep a stale row for the gone printer.
    db._printers = [_printer(pid=2, cups_name="epson")]
    harness.status_by_queue["epson_release"] = _status(markers=[])
    await alert_service.check_alerts(db)

    saved = db.saved_state()
    assert "1" not in saved
    assert "2" in saved
