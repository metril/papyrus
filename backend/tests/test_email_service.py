"""EmailService.send_alert tests.

``send_alert`` reads SMTP config straight from the DB (unlike ``send_scan``,
which is handed a pre-loaded ``db_config``), builds a plain-text message, and
shares the same connect/send core (``_deliver``) as ``send_scan``. Here
``aiosmtplib.send`` is monkeypatched at the module import site so no real SMTP
connection is attempted; the SMTP config is seeded as real AppConfig rows
through the integration ``db`` fixture.
"""
import pytest

from app.models import AppConfig
from app.services.email_service import EmailError, email_service


async def _seed_smtp(db, **overrides) -> None:
    rows = {
        "smtp_host": "smtp.example.com",
        "smtp_port": "587",
        "smtp_from": "papyrus@example.com",
    }
    rows.update(overrides)
    for key, value in rows.items():
        db.add(AppConfig(key=key, value=value))
    await db.commit()


async def test_send_alert_builds_plaintext_message_and_sends(db, monkeypatch):
    await _seed_smtp(db)

    captured: dict = {}

    async def fake_send(msg, **kwargs):
        captured["msg"] = msg
        captured["kwargs"] = kwargs

    monkeypatch.setattr("app.services.email_service.aiosmtplib.send", fake_send)

    await email_service.send_alert(db, "ops@example.com", "Toner low", "Black at 5%")

    msg = captured["msg"]
    assert msg["To"] == "ops@example.com"
    assert msg["From"] == "papyrus@example.com"
    assert msg["Subject"] == "Toner low"
    assert msg.get_content_type() == "text/plain"
    assert "Black at 5%" in msg.get_payload()

    kwargs = captured["kwargs"]
    assert kwargs["hostname"] == "smtp.example.com"
    assert kwargs["port"] == 587
    assert kwargs["start_tls"] is True  # port 587
    assert kwargs["use_tls"] is False


async def test_send_alert_uses_implicit_tls_on_port_465(db, monkeypatch):
    await _seed_smtp(db, smtp_port="465")
    captured: dict = {}

    async def fake_send(msg, **kwargs):
        captured["kwargs"] = kwargs

    monkeypatch.setattr("app.services.email_service.aiosmtplib.send", fake_send)

    await email_service.send_alert(db, "ops@example.com", "S", "B")

    assert captured["kwargs"]["use_tls"] is True
    assert captured["kwargs"]["start_tls"] is False


async def test_send_alert_raises_when_smtp_unconfigured(db, monkeypatch):
    # No smtp_host seeded -> unconfigured -> EmailError, and aiosmtplib.send
    # is never reached.
    sent = {"called": False}

    async def fake_send(*args, **kwargs):  # pragma: no cover - must not run
        sent["called"] = True

    monkeypatch.setattr("app.services.email_service.aiosmtplib.send", fake_send)

    with pytest.raises(EmailError):
        await email_service.send_alert(db, "ops@example.com", "S", "B")
    assert sent["called"] is False


async def test_send_alert_wraps_delivery_failure_in_email_error(db, monkeypatch):
    await _seed_smtp(db)

    async def fake_send(*args, **kwargs):
        raise ConnectionRefusedError("no route to smtp host")

    monkeypatch.setattr("app.services.email_service.aiosmtplib.send", fake_send)

    with pytest.raises(EmailError, match="Failed to send email"):
        await email_service.send_alert(db, "ops@example.com", "S", "B")
