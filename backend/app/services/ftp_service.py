"""FTP/SFTP upload service."""

import asyncio
import ftplib
import os
import logging

from app.services.crypto import decrypt_value

logger = logging.getLogger(__name__)


class FTPError(Exception):
    pass


class FTPService:
    """Upload files to FTP or SFTP servers."""

    async def upload_ftp(
        self,
        host: str,
        port: int,
        username: str,
        password_encrypted: str,
        filepath: str,
        filename: str,
        remote_dir: str = "/",
        use_tls: bool = False,
    ) -> None:
        """Upload a file via FTP (optionally with TLS)."""
        password = decrypt_value(password_encrypted)

        def _upload():
            if use_tls:
                ftp = ftplib.FTP_TLS()
            else:
                ftp = ftplib.FTP()
            try:
                ftp.connect(host, port, timeout=30)
                ftp.login(username, password)
                if use_tls:
                    ftp.prot_p()
                if remote_dir and remote_dir != "/":
                    ftp.cwd(remote_dir)
                with open(filepath, "rb") as f:
                    ftp.storbinary(f"STOR {filename}", f)
            finally:
                try:
                    ftp.quit()
                except Exception:
                    ftp.close()

        try:
            await asyncio.to_thread(_upload)
        except Exception as exc:
            raise FTPError(f"FTP upload failed: {exc}") from exc

    async def upload_sftp(
        self,
        host: str,
        port: int,
        username: str,
        password_encrypted: str,
        filepath: str,
        filename: str,
        remote_dir: str = "/",
    ) -> None:
        """Upload a file via SFTP (SSH)."""
        password = decrypt_value(password_encrypted)

        def _upload():
            import paramiko
            transport = paramiko.Transport((host, port))
            try:
                transport.connect(username=username, password=password)
                sftp = paramiko.SFTPClient.from_transport(transport)
                if sftp is None:
                    raise FTPError("Could not open SFTP session")
                remote_path = os.path.join(remote_dir, filename)
                sftp.put(filepath, remote_path)
                sftp.close()
            finally:
                transport.close()

        try:
            await asyncio.to_thread(_upload)
        except FTPError:
            raise
        except Exception as exc:
            raise FTPError(f"SFTP upload failed: {exc}") from exc

    async def test_ftp(
        self,
        host: str,
        port: int,
        username: str,
        password_encrypted: str,
        use_tls: bool = False,
    ) -> bool:
        """Test FTP connectivity."""
        password = decrypt_value(password_encrypted)

        def _test():
            if use_tls:
                ftp = ftplib.FTP_TLS()
            else:
                ftp = ftplib.FTP()
            try:
                ftp.connect(host, port, timeout=10)
                ftp.login(username, password)
                ftp.quit()
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_test)

    async def test_sftp(
        self,
        host: str,
        port: int,
        username: str,
        password_encrypted: str,
    ) -> bool:
        """Test SFTP connectivity."""
        password = decrypt_value(password_encrypted)

        def _test():
            import paramiko
            transport = paramiko.Transport((host, port))
            try:
                transport.connect(username=username, password=password)
                transport.close()
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_test)


ftp_service = FTPService()
