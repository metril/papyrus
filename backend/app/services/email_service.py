import os
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.config import settings
from app.services.crypto import decrypt_value


class EmailError(Exception):
    pass


class EmailService:
    def _get_config(self, db_config: dict | None = None) -> dict:
        """Get SMTP config from settings or database override."""
        config = {
            "host": settings.smtp_host,
            "port": settings.smtp_port,
            "user": settings.smtp_user,
            "password": settings.smtp_password,
            "from_addr": settings.smtp_from,
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
