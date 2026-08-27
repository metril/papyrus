"""F5: post_scan_config secrets (ftp_password, and anything else matching
password/secret/token) are encrypted at rest and never returned in
plaintext from the scanners API.

Mirrors test_api_scanners.py's conventions (admin_client/db fixtures, real
Postgres via the `db` fixture).
"""
from app.models import Scanner
from app.services.crypto import decrypt_value


async def test_get_never_returns_plaintext_secret(db, admin_client):
    resp = await admin_client.post(
        "/api/scanners",
        json={
            "name": "FTP Scanner",
            "device": "test:device:1",
            "post_scan_config": {
                "ftp_host": "ftp.example.com",
                "ftp_username": "scanuser",
                "ftp_password": "hunter2",
            },
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["post_scan_config"]["ftp_password"] == "*set*"
    assert "hunter2" not in resp.text

    # GET (list) must redact the same way.
    listing = await admin_client.get("/api/scanners")
    scanner_out = next(s for s in listing.json() if s["name"] == "FTP Scanner")
    assert scanner_out["post_scan_config"]["ftp_password"] == "*set*"
    assert "hunter2" not in listing.text


async def test_non_secret_fields_pass_through_unredacted(db, admin_client):
    resp = await admin_client.post(
        "/api/scanners",
        json={
            "name": "FTP Scanner 2",
            "device": "test:device:2",
            "post_scan_config": {
                "ftp_host": "ftp.example.com",
                "ftp_username": "scanuser",
                "ftp_password": "hunter2",
                "email": "delivery@example.com",
            },
        },
    )
    body = resp.json()["post_scan_config"]
    assert body["ftp_host"] == "ftp.example.com"
    assert body["ftp_username"] == "scanuser"
    assert body["email"] == "delivery@example.com"


async def test_stored_value_is_encrypted_not_plaintext(db, admin_client):
    resp = await admin_client.post(
        "/api/scanners",
        json={
            "name": "FTP Scanner 3",
            "device": "test:device:3",
            "post_scan_config": {"ftp_password": "hunter2"},
        },
    )
    scanner_id = resp.json()["id"]

    await db.rollback()  # fresh snapshot -- the router committed on its own session
    scanner = await db.get(Scanner, scanner_id)

    stored = scanner.post_scan_config["ftp_password"]
    assert stored != "hunter2"
    assert stored != "*set*"
    assert decrypt_value(stored) == "hunter2"


async def test_patch_with_sentinel_keeps_existing_encrypted_value(db, admin_client):
    create = await admin_client.post(
        "/api/scanners",
        json={
            "name": "FTP Scanner 4",
            "device": "test:device:4",
            "post_scan_config": {"ftp_password": "hunter2", "ftp_host": "old.example.com"},
        },
    )
    scanner_id = create.json()["id"]

    patch = await admin_client.patch(
        f"/api/scanners/{scanner_id}",
        json={
            "post_scan_config": {
                "ftp_password": "*set*",
                "ftp_host": "new.example.com",
            }
        },
    )
    assert patch.status_code == 200
    assert patch.json()["post_scan_config"]["ftp_password"] == "*set*"
    assert patch.json()["post_scan_config"]["ftp_host"] == "new.example.com"

    await db.rollback()
    scanner = await db.get(Scanner, scanner_id)
    assert decrypt_value(scanner.post_scan_config["ftp_password"]) == "hunter2"
    assert scanner.post_scan_config["ftp_host"] == "new.example.com"


async def test_patch_with_new_value_re_encrypts(db, admin_client):
    create = await admin_client.post(
        "/api/scanners",
        json={
            "name": "FTP Scanner 5",
            "device": "test:device:5",
            "post_scan_config": {"ftp_password": "hunter2"},
        },
    )
    scanner_id = create.json()["id"]

    await admin_client.patch(
        f"/api/scanners/{scanner_id}",
        json={"post_scan_config": {"ftp_password": "new-password-999"}},
    )

    # A single rollback+read at the end, after every admin_client call --
    # rolling back mid-test would expire the `admin_user` ORM object the
    # require_admin dependency override reads from the same `db` session.
    await db.rollback()
    after = (await db.get(Scanner, scanner_id)).post_scan_config["ftp_password"]

    assert after != "hunter2"
    assert decrypt_value(after) == "new-password-999"


async def test_sentinel_on_create_with_nothing_stored_is_dropped(db, admin_client):
    """A "*set*" sentinel makes no sense on creation (there's no existing
    value to keep) -- it must not be persisted as the literal placeholder."""
    resp = await admin_client.post(
        "/api/scanners",
        json={
            "name": "FTP Scanner 6",
            "device": "test:device:6",
            "post_scan_config": {"ftp_password": "*set*"},
        },
    )
    scanner_id = resp.json()["id"]
    await db.rollback()
    scanner = await db.get(Scanner, scanner_id)
    assert "ftp_password" not in (scanner.post_scan_config or {})
