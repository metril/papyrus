"""Paperless-ngx integration service."""

import httpx

from app.services.crypto import decrypt_value


class PaperlessError(Exception):
    pass


class PaperlessService:
    async def push_document(
        self,
        filepath: str,
        filename: str,
        paperless_url: str,
        api_token_encrypted: str,
        title: str | None = None,
        correspondent: str | None = None,
        tags: list[str] | None = None,
    ) -> int:
        """Upload a document to Paperless-ngx.

        Returns the Paperless task ID.
        """
        api_token = decrypt_value(api_token_encrypted)
        url = f"{paperless_url.rstrip('/')}/api/documents/post_document/"

        with open(filepath, "rb") as f:
            files = {"document": (filename, f)}
            data: dict[str, str] = {}
            if title:
                data["title"] = title
            if correspondent:
                data["correspondent"] = correspondent
            if tags:
                for tag in tags:
                    data["tags"] = tag  # Paperless accepts multiple tags fields

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Token {api_token}"},
                    files=files,
                    data=data,
                )

        if resp.status_code not in (200, 202):
            raise PaperlessError(f"Paperless upload failed ({resp.status_code}): {resp.text}")

        # Paperless returns a task ID as a string like "abc-123"
        task_id = resp.text.strip().strip('"')
        return task_id

    async def test_connection(
        self,
        paperless_url: str,
        api_token_encrypted: str,
    ) -> bool:
        """Test connection to Paperless-ngx."""
        api_token = decrypt_value(api_token_encrypted)
        url = f"{paperless_url.rstrip('/')}/api/documents/?page_size=1"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    url,
                    headers={"Authorization": f"Token {api_token}"},
                )
            return resp.status_code == 200
        except Exception:
            return False


paperless_service = PaperlessService()
