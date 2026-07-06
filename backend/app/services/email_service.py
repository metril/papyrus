from email.message import Message
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ExternalServiceError
from app.models import AppConfig
from app.services.crypto import decrypt_value


class EmailError(ExternalServiceError):
    pass


class EmailService:
    def _get_config(self, db_config: dict | None = None) -> dict:
        """Get SMTP config from database values."""
        config = {
            "host": "",
            "port": 587,
            "user": "",
            "password": "",
            "from_addr": "",
        }
        if db_config:
            if db_config.get("smtp_host"):
                config["host"] = db_config["smtp_host"]
            if db_config.get("smtp_port"):
                config["port"] = int(db_config["smtp_port"])
            if db_config.get("smtp_user"):
                config["user"] = db_config["smtp_user"]
            if db_config.get("smtp_password_encrypted"):
                config["password"] = decrypt_value(db_config["smtp_password_encrypted"])
            if db_config.get("smtp_from"):
                config["from_addr"] = db_config["smtp_from"]
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
        try:
            await aiosmtplib.send(
                msg,
                hostname=config["host"],
                port=config["port"],
                username=config["user"] or None,
                password=config["password"] or None,
                use_tls=config["port"] == 465,
                start_tls=config["port"] == 587,
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

        # Attach the scan file
        with open(filepath, "rb") as f:
            attachment = MIMEApplication(f.read())
            attachment.add_header(
                "Content-Disposition", "attachment", filename=filename
            )
            msg.attach(attachment)

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
        """Test SMTP connection."""
        config = self._get_config(db_config)
        try:
            smtp = aiosmtplib.SMTP(
                hostname=config["host"],
                port=config["port"],
                use_tls=config["port"] == 465,
                start_tls=config["port"] == 587,
            )
            await smtp.connect()
            if config["user"] and config["password"]:
                await smtp.login(config["user"], config["password"])
            await smtp.quit()
            return True
        except Exception:
            return False


email_service = EmailService()
