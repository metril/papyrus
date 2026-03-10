import os
import tempfile
from datetime import datetime, timezone

from smb.SMBConnection import SMBConnection
from smb.smb_structs import OperationFailure

from app.services.crypto import decrypt_value


class SMBError(Exception):
    pass


class SMBService:
    def _connect(
        self, server: str, share_name: str, username: str | None,
        password_encrypted: str | None, domain: str,
    ) -> tuple[SMBConnection, str]:
        """Create and return an SMB connection.

        Returns:
            Tuple of (connection, service_name)
        """
        user = username or "guest"
        password = ""
        if password_encrypted:
            password = decrypt_value(password_encrypted)

        conn = SMBConnection(user, password, "papyrus", server, domain=domain, use_ntlm_v2=True)
        if not conn.connect(server, 445, timeout=10):
            raise SMBError(f"Failed to connect to {server}")

        return conn, share_name

    def browse(
        self, server: str, share_name: str, path: str,
        username: str | None, password_encrypted: str | None, domain: str,
    ) -> list[dict]:
        """Browse files on an SMB share.

        Returns list of file entries with name, is_directory, size, modified_at.
        """
        conn, service = self._connect(server, share_name, username, password_encrypted, domain)
        try:
            entries = conn.listPath(service, path)
            result = []
            for entry in entries:
                # Skip . and ..
                if entry.filename in (".", ".."):
                    continue
                result.append({
                    "name": entry.filename,
                    "is_directory": entry.isDirectory,
                    "size": entry.file_size,
                    "modified_at": datetime.fromtimestamp(
                        entry.last_write_time, tz=timezone.utc
                    ).isoformat() if entry.last_write_time else None,
                })
            return sorted(result, key=lambda x: (not x["is_directory"], x["name"].lower()))
        except OperationFailure as e:
            raise SMBError(f"Failed to browse {path}: {e}")
        finally:
            conn.close()

    def download(
        self, server: str, share_name: str, remote_path: str,
        local_path: str, username: str | None,
        password_encrypted: str | None, domain: str,
    ) -> str:
        """Download a file from an SMB share to a local path.

        Returns the local file path.
        """
        conn, service = self._connect(server, share_name, username, password_encrypted, domain)
        try:
            with open(local_path, "wb") as f:
                conn.retrieveFile(service, remote_path, f)
            return local_path
        except OperationFailure as e:
            raise SMBError(f"Failed to download {remote_path}: {e}")
        finally:
            conn.close()

    def upload(
        self, server: str, share_name: str, remote_path: str,
        local_path: str, username: str | None,
        password_encrypted: str | None, domain: str,
    ) -> None:
        """Upload a local file to an SMB share."""
        conn, service = self._connect(server, share_name, username, password_encrypted, domain)
        try:
            with open(local_path, "rb") as f:
                conn.storeFile(service, remote_path, f)
        except OperationFailure as e:
            raise SMBError(f"Failed to upload to {remote_path}: {e}")
        finally:
            conn.close()


smb_service = SMBService()
