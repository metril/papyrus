import asyncio
from datetime import datetime, timezone

from smb.smb_structs import OperationFailure
from smb.SMBConnection import SMBConnection

from app.exceptions import ExternalServiceError
from app.services.crypto import decrypt_value


class SMBError(ExternalServiceError):
    pass


class SMBService:
    """Async wrapper around pysmb.

    Every pysmb call is blocking network I/O, so the public methods dispatch
    the synchronous work to a worker thread via ``asyncio.to_thread`` to avoid
    stalling the event loop.
    """

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

    # ------------------------------------------------------------------
    # Synchronous bodies (run inside a worker thread).
    # ------------------------------------------------------------------

    def _browse_sync(
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

    def _download_sync(
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

    def _upload_sync(
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

    # ------------------------------------------------------------------
    # Async public API.
    # ------------------------------------------------------------------

    async def browse(
        self, server: str, share_name: str, path: str,
        username: str | None, password_encrypted: str | None, domain: str,
    ) -> list[dict]:
        """Browse files on an SMB share.

        Returns list of file entries with name, is_directory, size, modified_at.
        """
        return await asyncio.to_thread(
            self._browse_sync, server, share_name, path, username, password_encrypted, domain
        )

    async def download(
        self, server: str, share_name: str, remote_path: str,
        local_path: str, username: str | None,
        password_encrypted: str | None, domain: str,
    ) -> str:
        """Download a file from an SMB share to a local path.

        Returns the local file path.
        """
        return await asyncio.to_thread(
            self._download_sync, server, share_name, remote_path, local_path,
            username, password_encrypted, domain,
        )

    async def upload(
        self, server: str, share_name: str, remote_path: str,
        local_path: str, username: str | None,
        password_encrypted: str | None, domain: str,
    ) -> None:
        """Upload a local file to an SMB share."""
        await asyncio.to_thread(
            self._upload_sync, server, share_name, remote_path, local_path,
            username, password_encrypted, domain,
        )


smb_service = SMBService()
