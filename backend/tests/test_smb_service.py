"""Tests for the async SMBService wrapper.

These tests never touch a real SMB server: they monkeypatch the service's
private ``_*_sync`` bodies and assert the async public methods delegate to
them with the same arguments, off the event loop thread.
"""
from app.services.smb_service import SMBService


async def test_browse_delegates_with_args(monkeypatch):
    svc = SMBService()
    captured = {}

    def fake_sync(server, share_name, path, username, password_encrypted, domain):
        captured.update(
            server=server, share_name=share_name, path=path,
            username=username, password_encrypted=password_encrypted, domain=domain,
        )
        return [{"name": "a.txt", "is_directory": False, "size": 1, "modified_at": None}]

    monkeypatch.setattr(svc, "_browse_sync", fake_sync)

    result = await svc.browse(
        server="srv", share_name="share", path="/docs",
        username="user", password_encrypted="enc", domain="WORKGROUP",
    )

    assert result == [{"name": "a.txt", "is_directory": False, "size": 1, "modified_at": None}]
    assert captured == {
        "server": "srv",
        "share_name": "share",
        "path": "/docs",
        "username": "user",
        "password_encrypted": "enc",
        "domain": "WORKGROUP",
    }


async def test_download_delegates_with_args(monkeypatch):
    svc = SMBService()
    captured = {}

    def fake_sync(
        server, share_name, remote_path, local_path, username, password_encrypted, domain
    ):
        captured.update(
            server=server, share_name=share_name, remote_path=remote_path,
            local_path=local_path, username=username,
            password_encrypted=password_encrypted, domain=domain,
        )
        return local_path

    monkeypatch.setattr(svc, "_download_sync", fake_sync)

    result = await svc.download(
        server="srv", share_name="share", remote_path="/docs/a.pdf",
        local_path="/tmp/a.pdf", username="user",
        password_encrypted="enc", domain="WORKGROUP",
    )

    assert result == "/tmp/a.pdf"
    assert captured == {
        "server": "srv",
        "share_name": "share",
        "remote_path": "/docs/a.pdf",
        "local_path": "/tmp/a.pdf",
        "username": "user",
        "password_encrypted": "enc",
        "domain": "WORKGROUP",
    }


async def test_upload_delegates_with_args(monkeypatch):
    svc = SMBService()
    captured = {}

    def fake_sync(
        server, share_name, remote_path, local_path, username, password_encrypted, domain
    ):
        captured.update(
            server=server, share_name=share_name, remote_path=remote_path,
            local_path=local_path, username=username,
            password_encrypted=password_encrypted, domain=domain,
        )

    monkeypatch.setattr(svc, "_upload_sync", fake_sync)

    result = await svc.upload(
        server="srv", share_name="share", remote_path="/docs/a.pdf",
        local_path="/tmp/a.pdf", username="user",
        password_encrypted="enc", domain="WORKGROUP",
    )

    assert result is None
    assert captured == {
        "server": "srv",
        "share_name": "share",
        "remote_path": "/docs/a.pdf",
        "local_path": "/tmp/a.pdf",
        "username": "user",
        "password_encrypted": "enc",
        "domain": "WORKGROUP",
    }
