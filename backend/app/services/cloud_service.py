import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from app.services.crypto import decrypt_value, encrypt_value


class CloudError(Exception):
    pass


class CloudService:
    # --- Google Drive ---

    async def refresh_gdrive_token(
        self,
        refresh_token_encrypted: str,
    ) -> tuple[str, datetime | None]:
        """Refresh a Google Drive access token using the refresh token.

        Returns (new_access_token, expiry_datetime).
        """
        from app.config import settings

        if not settings.gdrive_client_id or not settings.gdrive_client_secret:
            raise CloudError("Google Drive OAuth credentials not configured")

        refresh_token = decrypt_value(refresh_token_encrypted)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.gdrive_client_id,
                    "client_secret": settings.gdrive_client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code != 200:
                raise CloudError(f"Failed to refresh Google token: {resp.text}")

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
            from googleapiclient.discovery import build
            from google.oauth2.credentials import Credentials
        except ImportError:
            raise CloudError("Google Drive SDK not installed. Install with: pip install papyrus[cloud]")

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
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaIoBaseDownload
            from google.oauth2.credentials import Credentials
        except ImportError:
            raise CloudError("Google Drive SDK not installed. Install with: pip install papyrus[cloud]")

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

        import io

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
        access_token_encrypted: str,
        folder_id: str | None = None,
    ) -> str:
        """Upload a file to Google Drive. Returns the file ID."""
        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
            from google.oauth2.credentials import Credentials
        except ImportError:
            raise CloudError("Google Drive SDK not installed. Install with: pip install papyrus[cloud]")

        access_token = decrypt_value(access_token_encrypted)
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
    ) -> tuple[str, datetime | None]:
        """Refresh a Dropbox access token. Returns (new_access_token, expiry)."""
        from app.config import settings

        if not settings.dropbox_app_key or not settings.dropbox_app_secret:
            raise CloudError("Dropbox OAuth credentials not configured")

        refresh_token = decrypt_value(refresh_token_encrypted)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.dropboxapi.com/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": settings.dropbox_app_key,
                    "client_secret": settings.dropbox_app_secret,
                },
            )
            if resp.status_code != 200:
                raise CloudError(f"Failed to refresh Dropbox token: {resp.text}")

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
        access_token_encrypted: str,
        remote_path: str = "/Papyrus Scans",
    ) -> str:
        """Upload a file to Dropbox. Returns the path."""
        try:
            import dropbox
        except ImportError:
            raise CloudError("Dropbox SDK not installed. Install with: pip install papyrus[cloud]")

        access_token = decrypt_value(access_token_encrypted)
        dbx = dropbox.Dropbox(access_token)

        dest_path = f"{remote_path}/{filename}"

        def _upload():
            with open(filepath, "rb") as f:
                dbx.files_upload(f.read(), dest_path)

        await asyncio.to_thread(_upload)
        return dest_path


cloud_service = CloudService()
