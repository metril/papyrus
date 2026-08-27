"""FTP/SFTP upload service."""

import asyncio
import base64
import ftplib
import hashlib
import hmac
import logging
import os

from app.exceptions import ExternalServiceError
from app.services.crypto import decrypt_value

logger = logging.getLogger(__name__)


class FTPError(ExternalServiceError):
    pass


def _normalize_fingerprint(value: str) -> str:
    """Strip whitespace, an optional "SHA256:" prefix, and base64 padding so
    a fingerprint pasted from tooling compares equal to the raw digest
    stored/computed here.

    `ssh-keygen -lf ...` (and `ssh -o FingerprintHash=sha256`) print the
    *unpadded* base64 form -- `SHA256:AbC...xyz` with no trailing `=` -- so
    without stripping padding here too, a fingerprint pinned by copying
    straight from that standard tooling would never match the padded form
    `base64.b64encode` produces, and every SFTP connection would be refused
    as a "mismatch" even against the correct server.
    """
    value = value.strip()
    if value.upper().startswith("SHA256:"):
        value = value[len("SHA256:"):]
    return value.rstrip("=")


def _sftp_key_fingerprint(key) -> str:
    """SHA256/base64 fingerprint of a paramiko host key's public blob, in
    the same unpadded form `ssh-keygen -lf` prints -- see
    `_normalize_fingerprint` for why the padding is stripped."""
    return base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def _connect_sftp_transport(host, port, username, password, host_key_fingerprint):
    """Open a paramiko SFTP `Transport`, verifying (or pinning) the server's
    host key before sending credentials (F58).

    `Transport.connect()` performs key exchange and authentication in one
    call with no way to verify the host key first, so this splits it:
    `start_client()` completes the key exchange alone, `get_remote_server_key()`
    reads the (now-known) host key, and only once it's been checked --
    matched against `host_key_fingerprint` if one is pinned, otherwise
    accepted-on-first-use with a warning -- does `auth_password()` send the
    username/password. Without this, `Transport.connect()` accepts any
    server key silently, so a MITM on the configured host gets the stored
    password and every auto-delivered scan.
    """
    import paramiko

    transport = paramiko.Transport((host, port))
    try:
        transport.start_client(timeout=30)
        server_key = transport.get_remote_server_key()
        actual_fingerprint = _sftp_key_fingerprint(server_key)

        pinned = _normalize_fingerprint(host_key_fingerprint) if host_key_fingerprint else ""
        if pinned:
            if not hmac.compare_digest(actual_fingerprint, pinned):
                raise FTPError(
                    "SFTP server host key does not match the pinned fingerprint "
                    "(possible MITM, or the server key changed)"
                )
        else:
            logger.warning(
                "SFTP host key for %s:%s is not pinned -- accepting it this time. "
                "Set sftp_host_key_fingerprint to SHA256:%s to pin it.",
                host, port, actual_fingerprint,
            )

        transport.auth_password(username, password)
        return transport
    except Exception:
        transport.close()
        raise


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
        host_key_fingerprint: str | None = None,
    ) -> None:
        """Upload a file via SFTP (SSH)."""
        password = decrypt_value(password_encrypted)

        def _upload():
            import paramiko
            transport = _connect_sftp_transport(
                host, port, username, password, host_key_fingerprint
            )
            try:
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
                return True
            except Exception as exc:
                logger.warning("FTP connection test to %s:%s failed: %s", host, port, exc)
                return False
            finally:
                try:
                    ftp.quit()
                except Exception:
                    try:
                        ftp.close()
                    except Exception:
                        pass

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
                return True
            except Exception as exc:
                logger.warning("SFTP connection test to %s:%s failed: %s", host, port, exc)
                return False
            finally:
                transport.close()

        return await asyncio.to_thread(_test)


ftp_service = FTPService()
