"""WebDAV/Nextcloud client service."""

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from app.services.crypto import decrypt_value


class WebDAVError(Exception):
    pass


class WebDAVService:
    """WebDAV client for Nextcloud and other WebDAV-compatible servers."""

    async def test_connection(self, base_url: str, username: str, password_encrypted: str) -> bool:
        """Test WebDAV connectivity with a PROPFIND on the root."""
        password = decrypt_value(password_encrypted)
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.request(
                    "PROPFIND",
                    f"{base_url.rstrip('/')}/",
                    auth=(username, password),
                    headers={"Depth": "0"},
                )
                return resp.status_code in (207, 200)
            except Exception:
                return False

    async def list_files(
        self,
        base_url: str,
        username: str,
        password_encrypted: str,
        path: str = "/",
    ) -> list[dict]:
        """List files and directories at the given WebDAV path."""
        password = decrypt_value(password_encrypted)
        url = f"{base_url.rstrip('/')}{path}"

        propfind_body = """<?xml version="1.0" encoding="utf-8" ?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:displayname/>
    <d:getcontentlength/>
    <d:getlastmodified/>
    <d:resourcetype/>
    <d:getcontenttype/>
  </d:prop>
</d:propfind>"""

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                "PROPFIND",
                url,
                auth=(username, password),
                headers={"Depth": "1", "Content-Type": "application/xml"},
                content=propfind_body.encode(),
            )
            if resp.status_code != 207:
                raise WebDAVError(f"PROPFIND failed ({resp.status_code}): {resp.text[:200]}")

        entries = []
        root = ET.fromstring(resp.text)
        ns = {"d": "DAV:"}

        for response in root.findall("d:response", ns):
            href_el = response.find("d:href", ns)
            if href_el is None or href_el.text is None:
                continue
            href = href_el.text.rstrip("/")

            # Skip the directory itself (first entry is the queried path)
            req_path = path.rstrip("/")
            if href.endswith(req_path) or href == req_path:
                continue

            propstat = response.find("d:propstat", ns)
            if propstat is None:
                continue
            prop = propstat.find("d:prop", ns)
            if prop is None:
                continue

            name_el = prop.find("d:displayname", ns)
            name = name_el.text if name_el is not None and name_el.text else href.split("/")[-1]

            resource_type = prop.find("d:resourcetype", ns)
            is_dir = resource_type is not None and resource_type.find("d:collection", ns) is not None

            size_el = prop.find("d:getcontentlength", ns)
            size = int(size_el.text) if size_el is not None and size_el.text else None

            modified_el = prop.find("d:getlastmodified", ns)
            modified_at = None
            if modified_el is not None and modified_el.text:
                try:
                    from email.utils import parsedate_to_datetime
                    modified_at = parsedate_to_datetime(modified_el.text).isoformat()
                except Exception:
                    pass

            content_type_el = prop.find("d:getcontenttype", ns)
            mime_type = content_type_el.text if content_type_el is not None else None

            entries.append({
                "name": name,
                "path": href,
                "is_directory": is_dir,
                "size": size,
                "modified_at": modified_at,
                "mime_type": mime_type,
            })

        # Sort: directories first, then alphabetical
        entries.sort(key=lambda e: (not e["is_directory"], e["name"].lower()))
        return entries

    async def download_file(
        self,
        base_url: str,
        username: str,
        password_encrypted: str,
        remote_path: str,
        local_path: str,
    ) -> str:
        """Download a file from WebDAV to a local path."""
        password = decrypt_value(password_encrypted)
        url = f"{base_url.rstrip('/')}{remote_path}"

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(url, auth=(username, password))
            if resp.status_code != 200:
                raise WebDAVError(f"Download failed ({resp.status_code})")

            with open(local_path, "wb") as f:
                f.write(resp.content)

        return local_path

    async def upload_file(
        self,
        base_url: str,
        username: str,
        password_encrypted: str,
        filepath: str,
        filename: str,
        destination_folder: str = "/",
    ) -> None:
        """Upload a local file to a WebDAV path."""
        password = decrypt_value(password_encrypted)
        dest = f"{base_url.rstrip('/')}{destination_folder.rstrip('/')}/{filename}"

        with open(filepath, "rb") as f:
            content = f.read()

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.put(
                dest,
                auth=(username, password),
                content=content,
            )
            if resp.status_code not in (200, 201, 204):
                raise WebDAVError(f"Upload failed ({resp.status_code}): {resp.text[:200]}")

    async def mkdir(
        self,
        base_url: str,
        username: str,
        password_encrypted: str,
        path: str,
    ) -> None:
        """Create a directory on the WebDAV server."""
        password = decrypt_value(password_encrypted)
        url = f"{base_url.rstrip('/')}{path}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request("MKCOL", url, auth=(username, password))
            if resp.status_code not in (201, 405):  # 405 = already exists
                raise WebDAVError(f"MKCOL failed ({resp.status_code})")


webdav_service = WebDAVService()
