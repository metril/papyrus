import asyncio
import uuid

from app import main as main_module
from app.routers import escl
from app.services.scan_service import remove_scan_leftovers


def test_only_exact_transient_directories_are_removed(tmp_path):
    u = uuid.uuid4()
    gone = [f"pages_{u}", f"batch_{u}", "native_ab12cd_9"]
    keep_dirs = ["pages_mine", f"pages_{u}.bak", "batch_", "native_toolongname", "archive"]
    for name in gone + keep_dirs:
        (tmp_path / name).mkdir()
        (tmp_path / name / "f").write_text("x")
    (tmp_path / f"batch_{u.hex[:8]}-0000-0000-0000-000000000000").write_text("a file")
    (tmp_path / f"{u}.pdf").write_text("history")
    target = tmp_path.parent / f"{tmp_path.name}-target"
    target.mkdir()
    (tmp_path / "pages_00000000-0000-0000-0000-000000000000").symlink_to(target)

    assert remove_scan_leftovers(str(tmp_path)) == 3
    left = {p.name for p in tmp_path.iterdir()}
    assert not left & set(gone)
    assert set(keep_dirs) <= left and f"{u}.pdf" in left
    assert target.exists() and "pages_00000000-0000-0000-0000-000000000000" in left


def test_missing_scan_dir_is_fine(tmp_path):
    assert remove_scan_leftovers(str(tmp_path / "nope")) == 0


async def test_expiry_loop_purges_without_traffic(monkeypatch, tmp_path):
    page_dir = tmp_path / "pages_x"
    page_dir.mkdir()
    (page_dir / "p.jpg").write_text("x")
    escl._scan_jobs["old"] = {
        "state": "Completed", "terminal_at": -10_000.0, "pages": [str(page_dir / "p.jpg")],
        "page_dir": str(page_dir),
    }
    real_sleep = asyncio.sleep

    async def fast_sleep(_s):
        await real_sleep(0.01)

    monkeypatch.setattr(main_module.asyncio, "sleep", fast_sleep)
    task = asyncio.create_task(main_module._escl_expiry_loop())
    await real_sleep(0.1)
    task.cancel()
    assert "old" not in escl._scan_jobs and not page_dir.exists()
    escl._scan_jobs.clear()
