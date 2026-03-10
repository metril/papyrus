import os

from app.services.crypto import decrypt_value, encrypt_value


class CloudError(Exception):
    pass


class CloudService:
    async def upload_to_gdrive(
        self,
        filepath: str,
        filename: str,
        access_token_encrypted: str,
        folder_id: str | None = None,
    ) -> str:
        """Upload a file to Google Drive.

        Returns the file ID of the uploaded file.
        """
        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
            from google.oauth2.credentials import Credentials
        except ImportError:
            raise CloudError("Google Drive SDK not installed. Install with: pip install papyrus[cloud]")

        access_token = decrypt_value(access_token_encrypted)
        credentials = Credentials(token=access_token)

        service = build("drive", "v3", credentials=credentials)

        file_metadata = {"name": filename}
        if folder_id:
            file_metadata["parents"] = [folder_id]

        media = MediaFileUpload(filepath)
        result = service.files().create(
            body=file_metadata, media_body=media, fields="id"
        ).execute()

        return result["id"]

    async def upload_to_dropbox(
        self,
        filepath: str,
        filename: str,
        access_token_encrypted: str,
        remote_path: str = "/Papyrus Scans",
    ) -> str:
        """Upload a file to Dropbox.

        Returns the path of the uploaded file.
        """
        try:
            import dropbox
        except ImportError:
            raise CloudError("Dropbox SDK not installed. Install with: pip install papyrus[cloud]")

        access_token = decrypt_value(access_token_encrypted)
        dbx = dropbox.Dropbox(access_token)

        dest_path = f"{remote_path}/{filename}"

        with open(filepath, "rb") as f:
            dbx.files_upload(f.read(), dest_path)

        return dest_path


cloud_service = CloudService()
