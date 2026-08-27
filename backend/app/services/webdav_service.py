"""WebDAV/Nextcloud client service."""

import asyncio
import logging
import xml.etree.ElementTree as ET
from enum import Enum
from urllib.parse import urlsplit, urlunsplit

from app.exceptions import ExternalServiceError
from app.services.crypto import decrypt_value
from app.services.http_client import get_http_client

logger = logging.getLogger(__name__)


class WebDAVError(ExternalServiceError):
    pass


class WebDAVConnectError(str, Enum):
    """Why `WebDAVService.test_connection` couldn't confirm connectivity --
    distinguishable so the caller can surface something more useful than one
    flat "could not connect" for a bad password vs. a TLS/DNS failure (F154).
    """

    AUTH = "auth"
    TRANSPORT = "transport"
    NOT_WEBDAV = "not_webdav"


def _safe_join(base_url: str, path: str) -> str:
    """Join `base_url` with a WebDAV-relative `path`, always keeping the
    connection's own scheme+host.

    `path` can never redirect the request to a different host: naive string
    concatenation (the previous implementation) lets a caller-supplied path
    like ``"@evil.com/x"`` turn ``"http://realhost"`` into
    ``"http://realhost@evil.com/x"`` -- a URL whose host is *evil.com*, with
    "realhost" merely as (discarded) userinfo, while Basic auth for the real
    server is still attached (F13). Building the URL structurally via
    ``urlsplit``/``urlunsplit`` means whatever is in `path` only ever
    contributes to the path component.
    """
    base = urlsplit(base_url.rstrip("/"))
    if not path.startswith("/"):
        path = "/" + path
    return urlunsplit((base.scheme, base.netloc, base.path + path, "", ""))


def _relative_to_base(href: str, base_path: str) -> str:
    """Strip the connection's own base path off a WebDAV `href`, returning a
    path relative to the connection root.

    `_safe_join` above always re-prepends `base_path` when building the next
    request URL, so an entry's `path` must NOT already include it -- passing
    the raw absolute `href` back in as the next `path` (as the frontend does
    when navigating into a folder) would otherwise double-prefix it, e.g.
    ``/remote.php/dav/files/alice`` + ``/remote.php/dav/files/alice/Docs``.
    """
    base_path = base_path.rstrip("/")
    relative = href[len(base_path):] if base_path and href.startswith(base_path) else href
    return relative if relative.startswith("/") else "/" + relative


class WebDAVService:
    """WebDAV client for Nextcloud and other WebDAV-compatible servers."""

    async def test_connection(
        self, base_url: str, username: str, password_encrypted: str
    ) -> WebDAVConnectError | None:
        """Test WebDAV connectivity with a PROPFIND on the root.

        Returns `None` on success, or a `WebDAVConnectError` reason on
        failure. Every failure is logged at warning level (F154) -- the
        previous bare `except Exception: return False` discarded the actual
        cause entirely, so a TLS/DNS failure looked identical to a bad
        password both server-side and to the admin.
        """
        password = decrypt_value(password_encrypted)
        client = get_http_client()
        try:
            resp = await client.request(
                "PROPFIND",
                f"{base_url.rstrip('/')}/",
                auth=(username, password),
                headers={"Depth": "0"},
                timeout=10.0,
            )
        except Exception as exc:
            logger.warning("WebDAV connection test to %s failed: %s", base_url, exc)
            return WebDAVConnectError.TRANSPORT

        if resp.status_code in (401, 403):
            logger.warning(
                "WebDAV connection test to %s failed authentication (%d)",
                base_url, resp.status_code,
            )
            return WebDAVConnectError.AUTH
        if resp.status_code not in (207, 200):
            logger.warning(
                "WebDAV connection test to %s returned unexpected status %d",
                base_url, resp.status_code,
            )
            return WebDAVConnectError.NOT_WEBDAV
        return None

    async def list_files(
        self,
        base_url: str,
        username: str,
        password_encrypted: str,
        path: str = "/",
    ) -> list[dict]:
        """List files and directories at the given WebDAV path."""
        password = decrypt_value(password_encrypted)
        url = _safe_join(base_url, path)
        # The entry for the requested collection itself (Depth:1 returns it
        # first, alongside its children) is identified by comparing against
        # the *requested URL's* server-relative path -- not the caller's
        # `path` alone, which used to make `href.endswith("")` true for
        # every entry whenever `path` was the default "/" (every string
        # ends with the empty string), silently emptying every root listing.
        requested_path = urlsplit(url).path.rstrip("/")
        base_path = urlsplit(base_url.rstrip("/")).path

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

        client = get_http_client()
        resp = await client.request(
            "PROPFIND",
            url,
            auth=(username, password),
            headers={"Depth": "1", "Content-Type": "application/xml"},
            content=propfind_body.encode(),
            timeout=30.0,
        )
        if resp.status_code != 207:
            logger.warning(
                "WebDAV PROPFIND on %s failed (%d): %s", url, resp.status_code, resp.text[:200]
            )
            raise WebDAVError("Failed to list files on the WebDAV server")

        entries = []
        root = ET.fromstring(resp.text)
        ns = {"d": "DAV:"}

        for response in root.findall("d:response", ns):
            href_el = response.find("d:href", ns)
            if href_el is None or href_el.text is None:
                continue
            # RFC 4918 permits a server to emit either a path-only href or a
            # full absolute URL (Apache mod_dav does) -- normalize to just
            # the path component before comparing/stripping, or a full-URL
            # href never matches `requested_path`/`base_path` (both already
            # scheme+host-free), so the collection would list itself as its
            # own child and _relative_to_base would fail to strip anything,
            # yielding a "path" like "/http://host/.../Documents" that
            # double-prefixes on the next round trip.
            href = urlsplit(href_el.text).path.rstrip("/")

            # Skip the entry for the requested collection itself.
            if href == requested_path:
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
            is_dir = (
                resource_type is not None and resource_type.find("d:collection", ns) is not None
            )

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
                "path": _relative_to_base(href, base_path),
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
        url = _safe_join(base_url, remote_path)

        client = get_http_client()
        resp = await client.get(url, auth=(username, password), timeout=120.0)
        if resp.status_code != 200:
            logger.warning("WebDAV download from %s failed (%d)", url, resp.status_code)
            raise WebDAVError("Failed to download file from the WebDAV server")

        def _write():
            with open(local_path, "wb") as f:
                f.write(resp.content)

        await asyncio.to_thread(_write)
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
        dest = _safe_join(base_url, f"{destination_folder.rstrip('/')}/{filename}")

        def _read():
            with open(filepath, "rb") as f:
                return f.read()

        content = await asyncio.to_thread(_read)

        client = get_http_client()
        resp = await client.put(
            dest,
            auth=(username, password),
            content=content,
            timeout=120.0,
        )
        if resp.status_code not in (200, 201, 204):
            logger.warning(
                "WebDAV upload to %s failed (%d): %s", dest, resp.status_code, resp.text[:200]
            )
            raise WebDAVError("Failed to upload file to the WebDAV server")

    async def mkdir(
        self,
        base_url: str,
        username: str,
        password_encrypted: str,
        path: str,
    ) -> None:
        """Create a directory on the WebDAV server."""
        password = decrypt_value(password_encrypted)
        url = _safe_join(base_url, path)

        client = get_http_client()
        resp = await client.request("MKCOL", url, auth=(username, password), timeout=10.0)
        if resp.status_code not in (201, 405):  # 405 = already exists
            logger.warning("WebDAV MKCOL on %s failed (%d)", url, resp.status_code)
            raise WebDAVError("Failed to create directory on the WebDAV server")


webdav_service = WebDAVService()
