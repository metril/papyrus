"""Legacy /api/* -> /api/v1/* alias middleware."""
import re

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.legacy_alias import SUNSET, TABLE, LegacyAliasMiddleware, route_exists
from app.main import app


def _sample(entry_rx: re.Pattern[str]) -> str:
    return re.sub(r"\(\?P<\w+>\[\^/\]\+\)", "x", entry_rx.pattern)


def _target(tpl: str) -> str:
    return re.sub(r"\{\w+\}", "x", tpl)


def _route_ok(method: str, path: str) -> bool:
    scope = {"type": "websocket" if method == "WS" else "http", "path": path, "root_path": ""}
    if method != "WS":
        scope["method"] = method
    return route_exists(app, scope)


@pytest.mark.parametrize("entry", TABLE, ids=lambda e: f"{e[0]} {e[1].pattern}")
def test_every_entry_resolves_to_real_route(entry):
    method, rx, tpl, _ = entry
    assert _route_ok(method, _target(tpl)), tpl
    # old path is not itself a registered route
    assert not _route_ok(method, _sample(rx))


def test_table_covers_all_http_v1_operations_once():
    keys = [(m, rx.pattern) for m, rx, _, _ in TABLE]
    assert len(keys) == len(set(keys))
    targets = {(m, _target(t)) for m, _, t, _ in TABLE}
    missing = [
        (m.upper(), p)
        for p, item in app.openapi()["paths"].items()
        for m in item
        if p.startswith("/api/v1/") and (m.upper(), _target(p)) not in targets
    ]
    assert not missing, missing


async def test_old_and_new_same_status_and_headers(client):
    for old, new in [
        ("/api/system/health", "/api/v1/system/health"),
        ("/api/auth/providers", "/api/v1/auth/providers"),
        ("/api/printer/status", "/api/v1/printers/default/status"),
        ("/api/scanner/scans", "/api/v1/scans"),
        ("/api/scanner/profiles", "/api/v1/scan-profiles"),
        ("/api/jobs", "/api/v1/jobs"),
    ]:
        a, b = await client.get(old), await client.get(new)
        assert a.status_code == b.status_code, old
        assert a.headers["deprecation"] == "true"
        assert a.headers["sunset"] == SUNSET
        assert a.headers["link"] == f'<{new}>; rel="successor-version"'
        assert "deprecation" not in b.headers


async def test_wrong_method_gives_405_not_404(client):
    assert (await client.put("/api/jobs/upload")).status_code == 405


async def test_permanent_aliases_have_no_deprecation(client):
    r = await client.post("/api/jobs/internal/ingest")
    assert "sunset" not in r.headers and "deprecation" not in r.headers
    assert r.headers["link"].startswith("</api/v1/jobs/internal/ingest>")


async def test_unknown_api_path_404_envelope(client):
    r = await client.get("/api/nope/nothing")
    assert r.status_code == 404
    assert "code" in r.json() and "request_id" in r.json()
    r = await client.get("/api/v1/nope")
    assert r.status_code == 404 and "code" in r.json()


def test_generic_fallback_only_when_target_exists():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["path"])

    mw = LegacyAliasMiddleware(inner, route_exists=lambda s: s["path"] == "/api/v1/brandnew")
    scope = {"type": "http", "method": "GET"}
    assert mw._resolve({**scope, "path": "/api/brandnew"})[0] == "/api/v1/brandnew"
    assert mw._resolve({**scope, "path": "/api/other"}) is None


async def test_ingest_via_old_path(db, client, tmp_path):
    from tests.test_api_jobs import _pdf_file, _seed_upload_dir

    await _seed_upload_dir(db, tmp_path)

    r = await client.post("/api/jobs/internal/ingest", files=_pdf_file("net.pdf"))
    assert r.status_code == 201
    r2 = await client.post("/api/v1/jobs/internal/ingest", files=_pdf_file("net2.pdf"))
    assert r2.status_code == 201


async def test_share_target_multipart_via_old_path(client):
    from tests.test_api_jobs import _pdf_file

    r = await client.post("/api/share-target", files=_pdf_file())
    assert r.status_code == 303
    r2 = await client.post("/api/v1/jobs/share-target", files=_pdf_file())
    assert r2.status_code == 303 and r2.headers["location"] == r.headers["location"]


_ADMIN_RX = (
    "/api/admin", "/api/settings", "/api/printers", "/api/scanners", "/api/webhooks",
)


@pytest.mark.parametrize(
    "entry",
    [e for e in TABLE if e[0] != "WS" and e[1].pattern.startswith(_ADMIN_RX)],
    ids=lambda e: f"{e[0]} {e[1].pattern}",
)
async def test_aliased_admin_paths_reject_unauthenticated(client, entry):
    method, rx, _, _ = entry
    r = await client.request(method, _sample(rx))
    assert r.status_code in (401, 403), (method, rx.pattern, r.status_code)


def test_websocket_via_old_path_and_v1():
    c = TestClient(app)
    for path in ("/api/system/ws/jobs", "/api/v1/ws/jobs"):
        with pytest.raises(WebSocketDisconnect):  # closed 1008: unauthenticated
            with c.websocket_connect(path) as ws:
                ws.receive_text()


async def test_control_chars_in_path_do_not_500(client):
    r = await client.get("/api/jobs/%0d%0aX:y")
    assert r.status_code < 500
    assert "\r" not in r.headers.get("link", "") and "\n" not in r.headers.get("link", "")
