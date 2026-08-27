"""Tests for `app.services.webdav_service` and `app.services.net_guard`.

No live WebDAV server is used: the shared httpx client is swapped for an
`httpx.MockTransport` (the same pattern `test_webhook_signing.py` and
`test_ipp_client.py` use) so requests are captured/answered in-process.
"""
import logging

import httpx
import pytest

import app.services.http_client as http_client_module
from app.services.crypto import encrypt_value
from app.services.net_guard import UnsafeHostError, assert_safe_host
from app.services.webdav_service import (
    WebDAVConnectError,
    WebDAVError,
    _safe_join,
    webdav_service,
)


@pytest.fixture(autouse=True)
async def _reset_http_client():
    yield
    if http_client_module._client is not None:
        await http_client_module._client.aclose()
    http_client_module._client = None


def _install_transport(handler) -> None:
    http_client_module._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# _safe_join — host cannot be rewritten by a caller-supplied path (F13)
# --------------------------------------------------------------------------- #
def test_safe_join_keeps_real_host_when_path_looks_like_userinfo():
    # A naive f"{base}{path}" concatenation of "http://realhost" and
    # "@evil.com/x" produces "http://realhost@evil.com/x" -- a URL whose
    # host is evil.com, with "realhost" reduced to discarded userinfo, while
    # Basic auth for the real server is still attached.
    url = _safe_join("http://realhost", "@evil.com/x")
    assert url.startswith("http://realhost/")
    assert "evil.com" not in url.split("/", 3)[2]  # host component only


def test_safe_join_normalizes_missing_leading_slash():
    assert _safe_join("http://host", "docs") == "http://host/docs"


def test_safe_join_preserves_base_path_prefix():
    assert _safe_join("http://host/remote.php/dav", "/files/x") == (
        "http://host/remote.php/dav/files/x"
    )


# --------------------------------------------------------------------------- #
# net_guard.assert_safe_host
# --------------------------------------------------------------------------- #
async def test_assert_safe_host_allows_private_lan_address():
    await assert_safe_host("192.168.1.50")  # must not raise


async def test_assert_safe_host_rejects_loopback():
    with pytest.raises(UnsafeHostError):
        await assert_safe_host("127.0.0.1")


async def test_assert_safe_host_rejects_link_local():
    with pytest.raises(UnsafeHostError):
        await assert_safe_host("169.254.169.254")  # cloud IMDS


async def test_assert_safe_host_rejects_unresolvable_host(monkeypatch):
    # Drive this from a fake getaddrinfo rather than a real DNS lookup for a
    # ".invalid" name -- a sandboxed test runner may have no resolver
    # reachable at all, which would make a real lookup hang/timeout instead
    # of failing fast.
    import socket as socket_module

    from app.services import net_guard

    def fake_getaddrinfo(host, port):
        raise socket_module.gaierror("Name or service not known")

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(UnsafeHostError):
        await assert_safe_host("this-host-does-not-resolve.invalid")


# --------------------------------------------------------------------------- #
# test_connection — reason enum + logging (F154)
# --------------------------------------------------------------------------- #
async def test_connection_success_returns_none(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207)

    _install_transport(handler)
    result = await webdav_service.test_connection(
        "http://nextcloud.local", "user", encrypt_value("pw")
    )
    assert result is None


async def test_connection_auth_failure_returns_auth_reason(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    _install_transport(handler)
    with caplog.at_level(logging.WARNING):
        result = await webdav_service.test_connection(
            "http://nextcloud.local", "user", encrypt_value("wrong")
        )
    assert result is WebDAVConnectError.AUTH
    assert any("nextcloud.local" in r.message for r in caplog.records)


async def test_connection_unexpected_status_returns_not_webdav_reason(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    _install_transport(handler)
    with caplog.at_level(logging.WARNING):
        result = await webdav_service.test_connection(
            "http://example.com", "user", encrypt_value("pw")
        )
    assert result is WebDAVConnectError.NOT_WEBDAV
    assert caplog.records


async def test_connection_transport_exception_returns_transport_reason(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _install_transport(handler)
    with caplog.at_level(logging.WARNING):
        result = await webdav_service.test_connection(
            "http://unreachable.local", "user", encrypt_value("pw")
        )
    assert result is WebDAVConnectError.TRANSPORT
    assert any("connection refused" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# list_files / upload_file — curated errors, never echo resp.text (F38-style, F13)
# --------------------------------------------------------------------------- #
async def test_list_files_error_never_echoes_response_body(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server secrets, stack trace, etc")

    _install_transport(handler)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(WebDAVError) as excinfo:
            await webdav_service.list_files(
                "http://nextcloud.local", "user", encrypt_value("pw")
            )
    assert "internal server secrets" not in str(excinfo.value)
    assert any("internal server secrets" in r.message for r in caplog.records)


async def test_list_files_success_parses_entries():
    body = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/user/</d:href>
    <d:propstat><d:prop><d:displayname/><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/user/report.pdf</d:href>
    <d:propstat><d:prop>
      <d:displayname>report.pdf</d:displayname>
      <d:getcontentlength>1234</d:getcontentlength>
      <d:resourcetype/>
      <d:getcontenttype>application/pdf</d:getcontenttype>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=body)

    _install_transport(handler)
    entries = await webdav_service.list_files(
        "http://nextcloud.local", "user", encrypt_value("pw"), "/remote.php/dav/files/user/"
    )
    assert len(entries) == 1
    assert entries[0]["name"] == "report.pdf"
    assert entries[0]["is_directory"] is False
    assert entries[0]["size"] == 1234


# --------------------------------------------------------------------------- #
# list_files — root-listing "skip self" guard + relative-path round-trip
# (review fix: req_path.rstrip("/") was "" for the default root path, and
# every href.endswith("") is True, so every entry was skipped and the root
# listing came back empty; separately, entries carried the raw absolute
# href, which double-prefixed through _safe_join when navigating into one)
# --------------------------------------------------------------------------- #

_ALICE_BASE = "http://nextcloud.local/remote.php/dav/files/alice"


async def test_list_files_root_listing_returns_children_not_empty():
    body = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/alice/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/</d:href>
    <d:propstat><d:prop>
      <d:displayname>Documents</d:displayname>
      <d:resourcetype><d:collection/></d:resourcetype>
    </d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/report.pdf</d:href>
    <d:propstat><d:prop>
      <d:displayname>report.pdf</d:displayname>
      <d:getcontentlength>1234</d:getcontentlength>
      <d:resourcetype/>
      <d:getcontenttype>application/pdf</d:getcontenttype>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=body)

    _install_transport(handler)
    # `path` intentionally omitted -- exercises the default root ("/"),
    # which is exactly the case the old `req_path = ""` guard broke.
    entries = await webdav_service.list_files(_ALICE_BASE, "user", encrypt_value("pw"))

    assert [e["name"] for e in entries] == ["Documents", "report.pdf"]


async def test_list_files_subfolder_listing_excludes_the_collection_itself():
    body = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/</d:href>
    <d:propstat><d:prop>
      <d:displayname>Documents</d:displayname>
      <d:resourcetype><d:collection/></d:resourcetype>
    </d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/notes.txt</d:href>
    <d:propstat><d:prop>
      <d:displayname>notes.txt</d:displayname>
      <d:getcontentlength>12</d:getcontentlength>
      <d:resourcetype/>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=body)

    _install_transport(handler)
    entries = await webdav_service.list_files(
        _ALICE_BASE, "user", encrypt_value("pw"), "/Documents"
    )

    assert [e["name"] for e in entries] == ["notes.txt"]


async def test_list_files_handles_full_url_hrefs_not_just_path_only():
    """RFC 4918 permits a server to emit either a path-only href or a full
    absolute URL (Apache mod_dav does) -- the collection-self-skip and the
    relative-path stripping must both normalize to the path component first,
    or a full-URL href never matches (the collection lists itself as its own
    child) and the stripped "path" comes out as "/http://host/..." instead
    of "/Documents"."""
    body = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>http://nextcloud.local/remote.php/dav/files/alice/Documents/</d:href>
    <d:propstat><d:prop>
      <d:displayname>Documents</d:displayname>
      <d:resourcetype><d:collection/></d:resourcetype>
    </d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>http://nextcloud.local/remote.php/dav/files/alice/Documents/notes.txt</d:href>
    <d:propstat><d:prop>
      <d:displayname>notes.txt</d:displayname>
      <d:getcontentlength>12</d:getcontentlength>
      <d:resourcetype/>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=body)

    _install_transport(handler)
    entries = await webdav_service.list_files(
        _ALICE_BASE, "user", encrypt_value("pw"), "/Documents"
    )

    assert [e["name"] for e in entries] == ["notes.txt"]
    assert entries[0]["path"] == "/Documents/notes.txt"


async def test_list_files_entry_paths_are_relative_to_base_and_round_trip_through_safe_join():
    body = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/alice/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/</d:href>
    <d:propstat><d:prop>
      <d:displayname>Documents</d:displayname>
      <d:resourcetype><d:collection/></d:resourcetype>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=body)

    _install_transport(handler)
    entries = await webdav_service.list_files(_ALICE_BASE, "user", encrypt_value("pw"))

    assert entries[0]["path"] == "/Documents"
    # Navigating into "Documents" builds this path back into the next
    # request URL via _safe_join (exactly what the frontend does) -- it
    # must land on the same absolute URL the original href pointed at,
    # not a double-prefixed .../alice/remote.php/dav/files/alice/Documents.
    assert (
        _safe_join(_ALICE_BASE, entries[0]["path"])
        == "http://nextcloud.local/remote.php/dav/files/alice/Documents"
    )


async def test_upload_file_error_never_echoes_response_body(tmp_path, caplog):
    src = tmp_path / "scan.pdf"
    src.write_bytes(b"%PDF-1.4 fake")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="secret nextcloud error page")

    _install_transport(handler)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(WebDAVError) as excinfo:
            await webdav_service.upload_file(
                "http://nextcloud.local", "user", encrypt_value("pw"),
                str(src), "scan.pdf", "/",
            )
    assert "secret nextcloud error page" not in str(excinfo.value)
    assert any("secret nextcloud error page" in r.message for r in caplog.records)


async def test_upload_file_success(tmp_path):
    src = tmp_path / "scan.pdf"
    src.write_bytes(b"%PDF-1.4 fake content")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(201)

    _install_transport(handler)
    await webdav_service.upload_file(
        "http://nextcloud.local", "user", encrypt_value("pw"), str(src), "scan.pdf", "/scans"
    )
    assert captured["request"].content == b"%PDF-1.4 fake content"
    assert captured["request"].url.path == "/scans/scan.pdf"


async def test_download_file_writes_content(tmp_path):
    dest = tmp_path / "downloaded.pdf"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"downloaded bytes")

    _install_transport(handler)
    await webdav_service.download_file(
        "http://nextcloud.local", "user", encrypt_value("pw"), "/scans/a.pdf", str(dest)
    )
    assert dest.read_bytes() == b"downloaded bytes"
