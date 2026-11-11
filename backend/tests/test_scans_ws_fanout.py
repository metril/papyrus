"""`scans` channel fan-out honours scan ownership."""
import uuid
from types import SimpleNamespace

from app.auth.ws import WebSocketIdentity
from app.services.ws_manager import ConnectionManager


class _Sock:
    def __init__(self):
        self.got = []

    async def accept(self):
        pass

    async def send_json(self, m):
        self.got.append(m)


def _user(role="user"):
    return SimpleNamespace(id=uuid.uuid4(), role=role)


async def _setup():
    m = ConnectionManager()
    alice, bob, admin = _user(), _user(), _user("admin")
    socks = {}
    for name, user, perms in [
        ("alice_tok", alice, ["scan"]), ("bob_tok", bob, ["scan"]),
        ("admin_tok", admin, ["scan", "admin"]), ("admin_tok_noadm", admin, ["scan"]),
        ("alice_sess", alice, None), ("bob_sess", bob, None),
    ]:
        s = _Sock()
        await m.connect("scans", s, WebSocketIdentity(user=user, permissions=perms))
        socks[name] = s
    return m, alice, socks


async def test_event_reaches_owner_and_admin_only():
    m, alice, socks = await _setup()
    for kind in ("scan_completed", "scan_deleted"):
        await m.broadcast_scan({"type": kind, "data": {"scan_id": "x"}}, alice.id)
    got = {k: len(v.got) for k, v in socks.items()}
    assert got == {"alice_tok": 2, "bob_tok": 0, "admin_tok": 2, "admin_tok_noadm": 0,
                   "alice_sess": 2, "bob_sess": 0}


async def test_null_owner_reaches_sessions_not_tokens():
    m, _, socks = await _setup()
    await m.broadcast_scan({"type": "scan_completed", "data": {}}, None)
    got = {k: len(v.got) for k, v in socks.items()}
    assert got == {"alice_tok": 0, "bob_tok": 0, "admin_tok": 0, "admin_tok_noadm": 0,
                   "alice_sess": 1, "bob_sess": 1}


async def test_unowned_scans_broadcast_fails_closed():
    m, _, socks = await _setup()
    await m.broadcast("scans", {"type": "scan_completed", "data": {}})
    assert all(not s.got for s in socks.values())
