import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from app.exceptions import ExternalServiceError
from app.services.crypto import decrypt_value
from app.services.http_client import get_http_client

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import CloudProvider

logger = logging.getLogger(__name__)


class CloudError(ExternalServiceError):
    pass


class CloudService:
    # --- Google Drive ---

    async def refresh_gdrive_token(
        self,
        refresh_token_encrypted: str,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> tuple[str, datetime | None]:
        """Refresh a Google Drive access token using the refresh token.

        Returns (new_access_token, expiry_datetime).
        """
        if not client_id or not client_secret:
            raise CloudError("Google Drive OAuth credentials not configured")

        refresh_token = decrypt_value(refresh_token_encrypted)

        client = get_http_client()
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            logger.warning("Google token refresh failed (%d): %s", resp.status_code, resp.text)
            raise CloudError("Failed to refresh Google Drive access token")

        data = resp.json()
        new_access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        expiry = datetime.now(timezone.utc).replace(
            microsecond=0
        ) + timedelta(seconds=expires_in)
        return new_access_token, expiry

    async def list_gdrive_files(
        self,
        access_token: str,
        folder_id: str | None = None,
    ) -> list[dict]:
        """List files in a Google Drive folder."""
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError:
            raise CloudError(
                "Google Drive SDK not installed. Install with: pip install papyrus[cloud]"
            )

        credentials = Credentials(token=access_token)
        service = build("drive", "v3", credentials=credentials)

        parent = folder_id or "root"
        query = f"'{parent}' in parents and trashed = false"

        def _list():
            return service.files().list(
                q=query,
                fields="files(id, name, mimeType, size, modifiedTime)",
                orderBy="folder,name",
                pageSize=100,
            ).execute()

        result = await asyncio.to_thread(_list)

        files = []
        for f in result.get("files", []):
            is_dir = f["mimeType"] == "application/vnd.google-apps.folder"
            files.append({
                "name": f["name"],
                "id": f["id"],
                "is_directory": is_dir,
                "size": int(f["size"]) if "size" in f else None,
                "modified_at": f.get("modifiedTime"),
                "mime_type": f["mimeType"],
            })
        return files

    async def download_gdrive_file(
        self,
        access_token: str,
        file_id: str,
        local_path: str,
    ) -> str:
        """Download a file from Google Drive. Exports Google Docs as PDF."""
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaIoBaseDownload
        except ImportError:
            raise CloudError(
                "Google Drive SDK not installed. Install with: pip install papyrus[cloud]"
            )

        credentials = Credentials(token=access_token)
        service = build("drive", "v3", credentials=credentials)

        # Get file metadata to check type
        def _get_meta():
            return service.files().get(fileId=file_id, fields="mimeType,name").execute()

        meta = await asyncio.to_thread(_get_meta)
        mime = meta["mimeType"]

        # Google Workspace docs need export
        export_mimes = {
            "application/vnd.google-apps.document": "application/pdf",
            "application/vnd.google-apps.spreadsheet": "application/pdf",
            "application/vnd.google-apps.presentation": "application/pdf",
        }


        def _download():
            if mime in export_mimes:
                request = service.files().export_media(
                    fileId=file_id, mimeType=export_mimes[mime]
                )
            else:
                request = service.files().get_media(fileId=file_id)

            with open(local_path, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()

        await asyncio.to_thread(_download)
        return local_path

    async def upload_to_gdrive(
        self,
        filepath: str,
        filename: str,
        access_token: str,
        folder_id: str | None = None,
    ) -> str:
        """Upload a file to Google Drive. Returns the file ID.

        `access_token` must already be a valid, decrypted token -- callers
        get one from `get_valid_access_token` (F14), which refreshes it
        first if it's close to/past expiry rather than handing this a
        possibly-stale token that just 401s against the Drive API.
        """
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
        except ImportError:
            raise CloudError(
                "Google Drive SDK not installed. Install with: pip install papyrus[cloud]"
            )

        credentials = Credentials(token=access_token)
        service = build("drive", "v3", credentials=credentials)

        file_metadata: dict = {"name": filename}
        if folder_id:
            file_metadata["parents"] = [folder_id]

        media = MediaFileUpload(filepath)

        def _upload():
            return service.files().create(
                body=file_metadata, media_body=media, fields="id"
            ).execute()

        result = await asyncio.to_thread(_upload)
        return result["id"]

    # --- Dropbox ---

    async def refresh_dropbox_token(
        self,
        refresh_token_encrypted: str,
        app_key: str | None = None,
        app_secret: str | None = None,
    ) -> tuple[str, datetime | None]:
        """Refresh a Dropbox access token. Returns (new_access_token, expiry)."""
        if not app_key or not app_secret:
            raise CloudError("Dropbox OAuth credentials not configured")

        refresh_token = decrypt_value(refresh_token_encrypted)

        client = get_http_client()
        resp = await client.post(
            "https://api.dropboxapi.com/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": app_key,
                "client_secret": app_secret,
            },
        )
        if resp.status_code != 200:
            logger.warning("Dropbox token refresh failed (%d): %s", resp.status_code, resp.text)
            raise CloudError("Failed to refresh Dropbox access token")

        data = resp.json()
        new_access_token = data["access_token"]
        expires_in = data.get("expires_in", 14400)
        expiry = datetime.now(timezone.utc).replace(
            microsecond=0
        ) + timedelta(seconds=expires_in)
        return new_access_token, expiry

    async def list_dropbox_files(
        self,
        access_token: str,
        path: str = "",
    ) -> list[dict]:
        """List files in a Dropbox folder."""
        try:
            import dropbox
        except ImportError:
            raise CloudError("Dropbox SDK not installed. Install with: pip install papyrus[cloud]")

        dbx = dropbox.Dropbox(access_token)

        def _list():
            return dbx.files_list_folder(path)

        result = await asyncio.to_thread(_list)

        files = []
        import dropbox as dbx_module

        for entry in result.entries:
            is_dir = isinstance(entry, dbx_module.files.FolderMetadata)
            files.append({
                "name": entry.name,
                "id": entry.path_lower if hasattr(entry, "path_lower") else entry.name,
                "is_directory": is_dir,
                "size": getattr(entry, "size", None),
                "modified_at": getattr(entry, "server_modified", None),
                "mime_type": None,
            })
        return files

    async def download_dropbox_file(
        self,
        access_token: str,
        remote_path: str,
        local_path: str,
    ) -> str:
        """Download a file from Dropbox."""
        try:
            import dropbox
        except ImportError:
            raise CloudError("Dropbox SDK not installed. Install with: pip install papyrus[cloud]")

        dbx = dropbox.Dropbox(access_token)

        def _download():
            dbx.files_download_to_file(local_path, remote_path)

        await asyncio.to_thread(_download)
        return local_path

    async def upload_to_dropbox(
        self,
        filepath: str,
        filename: str,
        access_token: str,
        remote_path: str = "/Papyrus Scans",
    ) -> str:
        """Upload a file to Dropbox. Returns the path.

        `access_token` must already be a valid, decrypted token -- see
        `upload_to_gdrive`'s docstring (F14).
        """
        try:
            import dropbox
        except ImportError:
            raise CloudError("Dropbox SDK not installed. Install with: pip install papyrus[cloud]")

        dbx = dropbox.Dropbox(access_token)

        dest_path = f"{remote_path}/{filename}"

        def _upload():
            with open(filepath, "rb") as f:
                dbx.files_upload(f.read(), dest_path)

        await asyncio.to_thread(_upload)
        return dest_path


    # --- OneDrive (Microsoft Graph) ---

    GRAPH_BASE = "https://graph.microsoft.com/v1.0"

    async def refresh_onedrive_token(
        self,
        refresh_token_encrypted: str,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> tuple[str, datetime | None]:
        """Refresh a OneDrive access token. Returns (new_access_token, expiry)."""
        if not client_id or not client_secret:
            raise CloudError("OneDrive OAuth credentials not configured")

        refresh_token = decrypt_value(refresh_token_encrypted)

        client = get_http_client()
        resp = await client.post(
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "Files.ReadWrite.All offline_access",
            },
        )
        if resp.status_code != 200:
            logger.warning("OneDrive token refresh failed (%d): %s", resp.status_code, resp.text)
            raise CloudError("Failed to refresh OneDrive access token")

        data = resp.json()
        new_access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        expiry = datetime.now(timezone.utc).replace(
            microsecond=0
        ) + timedelta(seconds=expires_in)
        return new_access_token, expiry

    async def list_onedrive_files(
        self,
        access_token: str,
        folder_id: str | None = None,
    ) -> list[dict]:
        """List files in a OneDrive folder via Microsoft Graph API."""
        if folder_id:
            url = f"{self.GRAPH_BASE}/me/drive/items/{folder_id}/children"
        else:
            url = f"{self.GRAPH_BASE}/me/drive/root/children"

        client = get_http_client()
        resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"$top": "100", "$orderby": "name"},
        )
        if resp.status_code != 200:
            logger.warning("OneDrive list-files API error (%d): %s", resp.status_code, resp.text)
            raise CloudError("Failed to list OneDrive files")

        data = resp.json()

        files = []
        for item in data.get("value", []):
            is_dir = "folder" in item
            files.append({
                "name": item["name"],
                "id": item["id"],
                "is_directory": is_dir,
                "size": item.get("size"),
                "modified_at": item.get("lastModifiedDateTime"),
                "mime_type": item.get("file", {}).get("mimeType") if not is_dir else None,
            })
        return files

    async def download_onedrive_file(
        self,
        access_token: str,
        file_id: str,
        local_path: str,
    ) -> str:
        """Download a file from OneDrive."""
        url = f"{self.GRAPH_BASE}/me/drive/items/{file_id}/content"

        client = get_http_client()
        resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            follow_redirects=True,
        )
        if resp.status_code != 200:
            logger.warning("OneDrive download error (%d)", resp.status_code)
            raise CloudError("Failed to download file from OneDrive")

        def _write():
            with open(local_path, "wb") as f:
                f.write(resp.content)

        await asyncio.to_thread(_write)
        return local_path

    async def upload_to_onedrive(
        self,
        filepath: str,
        filename: str,
        access_token: str,
        folder_path: str = "/Papyrus Scans",
    ) -> str:
        """Upload a file to OneDrive. Returns the item ID.

        `access_token` must already be a valid, decrypted token -- see
        `upload_to_gdrive`'s docstring (F14).
        """
        # Simple upload (< 4MB) via PUT to path
        upload_path = f"{folder_path}/{filename}".replace("//", "/")
        url = f"{self.GRAPH_BASE}/me/drive/root:{upload_path}:/content"

        def _read():
            with open(filepath, "rb") as f:
                return f.read()

        content = await asyncio.to_thread(_read)

        client = get_http_client()
        resp = await client.put(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/octet-stream",
            },
            content=content,
        )
        if resp.status_code not in (200, 201):
            logger.warning("OneDrive upload error (%d): %s", resp.status_code, resp.text)
            raise CloudError("Failed to upload file to OneDrive")

        return resp.json()["id"]

    # --- Shared expiry-aware access token (F14) ---

    async def get_valid_access_token(
        self, db: "AsyncSession", provider: "CloudProvider"
    ) -> str:
        """Return a valid, decrypted access token for `provider`, refreshing
        it first if it has expired.

        Every upload path must go through this rather than a raw
        `decrypt_value(provider.access_token_encrypted)`: Google/OneDrive
        tokens live ~1 hour, and a stale token 401s against the provider's
        API (or, on the scan auto-deliver path, is silently swallowed by its
        best-effort error handling). This is the same refresh-if-expiring
        logic the browse/download routes already used
        (routers/cloud.py's `_get_access_token`), now shared by both.
        """
        # Local import: cloud_service is a low-level service and
        # app.routers.settings imports from several services itself, so a
        # module-level import here would risk an import cycle.
        from app.routers.settings import get_setting
        from app.services.crypto import encrypt_value

        now = datetime.now(timezone.utc)
        if provider.token_expiry and provider.token_expiry.replace(tzinfo=timezone.utc) < now:
            if not provider.refresh_token_encrypted:
                raise CloudError("Cloud storage session expired. Please reconnect.")

            if provider.provider == "gdrive":
                client_id = await get_setting(db, "gdrive_client_id")
                client_secret = await get_setting(db, "gdrive_client_secret")
                new_token, expiry = await self.refresh_gdrive_token(
                    provider.refresh_token_encrypted, client_id, client_secret
                )
            elif provider.provider == "dropbox":
                app_key = await get_setting(db, "dropbox_app_key")
                app_secret = await get_setting(db, "dropbox_app_secret")
                new_token, expiry = await self.refresh_dropbox_token(
                    provider.refresh_token_encrypted, app_key, app_secret
                )
            elif provider.provider == "onedrive":
                client_id = await get_setting(db, "onedrive_client_id")
                client_secret = await get_setting(db, "onedrive_client_secret")
                new_token, expiry = await self.refresh_onedrive_token(
                    provider.refresh_token_encrypted, client_id, client_secret
                )
            else:
                raise CloudError("Unknown cloud provider")

            provider.access_token_encrypted = encrypt_value(new_token)
            provider.token_expiry = expiry
            await db.commit()
            return new_token

        return decrypt_value(provider.access_token_encrypted)


cloud_service = CloudService()
