import asyncio
import logging
from email.message import Message
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ExternalServiceError
from app.models import AppConfig
from app.services.crypto import decrypt_value

logger = logging.getLogger(__name__)

_VALID_SECURITY_MODES = {"starttls", "tls", "none"}


class EmailError(ExternalServiceError):
    pass


def _tls_flags(security: str) -> tuple[bool, bool]:
    """Map the ``smtp_security`` setting to aiosmtplib's (use_tls, start_tls).

    F56: this replaces the old ``port == 465`` / ``port == 587`` heuristic,
    which passed an explicit ``start_tls=False`` for any other port --
    aiosmtplib treats that as "never attempt STARTTLS" (only ``None`` is
    opportunistic), so a relay on 25/2525 sent AUTH in the clear while the
    Test button still reported success.

    * "tls" — implicit TLS from connect (port 465-style).
    * "starttls" (default) — require a STARTTLS upgrade; fail loudly if the
      server doesn't support it, rather than silently falling back to
      cleartext.
    * "none" — no encryption at all; an explicit, informed opt-in rather
      than an accidental default.
    """
    if security == "tls":
        return True, False
    if security == "none":
        return False, False
    return False, True  # starttls


class EmailService:
    def _get_config(self, db_config: dict | None = None) -> dict:
        """Get SMTP config from database values."""
        config = {
            "host": "",
            "port": 587,
            "user": "",
            "password": "",
            "from_addr": "",
            "security": "starttls",
        }
        if db_config:
            if db_config.get("smtp_host"):
                config["host"] = db_config["smtp_host"]
            if db_config.get("smtp_port"):
                config["port"] = int(db_config["smtp_port"])
            if db_config.get("smtp_user"):
                config["user"] = db_config["smtp_user"]
            if db_config.get("smtp_password_encrypted"):
                # F74: an unguarded decrypt here means a rotated
                # PAPYRUS_ENCRYPTION_KEY (or a restored foreign backup) turns
                # every SMTP send into a generic 500 with no hint the key is
                # the cause. Treat a decrypt failure as "no password
                # configured" and log a warning, matching get_setting.
                try:
                    config["password"] = decrypt_value(db_config["smtp_password_encrypted"])
                except InvalidToken:
                    logger.warning(
                        "Failed to decrypt SMTP password -- encryption key may have changed"
                    )
            if db_config.get("smtp_from"):
                config["from_addr"] = db_config["smtp_from"]
            security = db_config.get("smtp_security")
            if security in _VALID_SECURITY_MODES:
                config["security"] = security
        return config

    def is_configured(self, db_config: dict | None = None) -> bool:
        """Check if SMTP is configured."""
        config = self._get_config(db_config)
        return bool(config["host"] and config["from_addr"])

    async def _load_db_config(self, db: AsyncSession) -> dict:
        """Load the raw SMTP AppConfig rows (same shape ``send_scan`` expects).

        Returns encrypted values verbatim — ``_get_config`` decrypts them.
        """
        result = await db.execute(select(AppConfig).where(AppConfig.key.like("smtp_%")))
        return {row.key: row.value for row in result.scalars().all()}

    async def _deliver(self, msg: Message, config: dict) -> None:
        """Shared SMTP connect/send core for every outbound message.

        Extracted so ``send_scan`` and ``send_alert`` share one implementation
        of the connect/STARTTLS/auth logic and raise the same ``EmailError``.
        """
        use_tls, start_tls = _tls_flags(config["security"])
        try:
            await aiosmtplib.send(
                msg,
                hostname=config["host"],
                port=config["port"],
                username=config["user"] or None,
                password=config["password"] or None,
                use_tls=use_tls,
                start_tls=start_tls,
            )
        except Exception as e:
            raise EmailError(f"Failed to send email: {e}")

    async def send_scan(
        self,
        to: str,
        subject: str,
        body: str,
        filepath: str,
        filename: str,
        db_config: dict | None = None,
    ) -> None:
        """Send a scanned document as an email attachment."""
        config = self._get_config(db_config)

        if not config["host"]:
            raise EmailError("SMTP not configured")

        msg = MIMEMultipart()
        msg["From"] = config["from_addr"]
        msg["To"] = to
        msg["Subject"] = subject

        msg.attach(MIMEText(body or "Scanned document attached.", "plain"))

        # Attach the scan file. F39: read off the event loop -- a large scan
        # would otherwise block every other request/WS broadcast for the
        # whole disk read.
        def _build_attachment() -> MIMEApplication:
            with open(filepath, "rb") as f:
                attachment = MIMEApplication(f.read())
            attachment.add_header("Content-Disposition", "attachment", filename=filename)
            return attachment

        msg.attach(await asyncio.to_thread(_build_attachment))

        await self._deliver(msg, config)

    async def send_alert(self, db: AsyncSession, to: str, subject: str, body: str) -> None:
        """Send a plain-text alert email, reading SMTP config from the DB.

        Reuses the same connect/send core as ``send_scan``. Raises
        ``EmailError`` when SMTP is unconfigured or delivery fails — callers in
        the alert path are expected to catch/log so a mail failure never breaks
        the poller or suppresses the (already-dispatched) webhook.
        """
        config = self._get_config(await self._load_db_config(db))

        if not config["host"]:
            raise EmailError("SMTP not configured")

        msg = MIMEText(body or "", "plain")
        msg["From"] = config["from_addr"]
        msg["To"] = to
        msg["Subject"] = subject

        await self._deliver(msg, config)

    async def test_connection(self, db_config: dict | None = None) -> bool:
        """Test SMTP connection.

        F104: closes the session in a ``finally`` (a failed login used to
        leave the connection open until the server's own idle timeout) and
        logs the cause at warning level instead of swallowing it entirely.
        """
        config = self._get_config(db_config)
        use_tls, start_tls = _tls_flags(config["security"])
        smtp = aiosmtplib.SMTP(
            hostname=config["host"], port=config["port"], use_tls=use_tls, start_tls=start_tls
        )
        try:
            await smtp.connect()
            if config["user"] and config["password"]:
                await smtp.login(config["user"], config["password"])
            return True
        except Exception as exc:
            logger.warning(
                "SMTP connection test to %s:%s failed: %s", config["host"], config["port"], exc
            )
            return False
        finally:
            try:
                await smtp.quit()
            except Exception:
                pass


email_service = EmailService()
