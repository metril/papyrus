"""Regression tests for docker/entrypoint.sh process supervision.

The entrypoint starts avahi-daemon, cupsd, and uvicorn as backgrounded
children of one shell. Historically that shell waited only on uvicorn, so a
cupsd or avahi-daemon crash left the container running printerless -- the web
UI kept serving while AirPrint clients saw "printer is offline" until a manual
``docker compose down && up``. The entrypoint must instead notice either
daemon dying, shut the survivors down, and exit non-zero so compose's
``restart: unless-stopped`` recreates the container in a known-good state.

Also covers the bounded ``lpstat -r`` cupsd-readiness poll (formerly a blind
``sleep 2``) and, as refactor guards, the existing F102 shutdown semantics:
SIGTERM is forwarded to all three children and waited on, and uvicorn's exit
code is the container's exit code on the normal path.

These tests drive the real script with every external binary shimmed via
PATH. Daemon shims use ``/bin/sleep`` explicitly because ``sleep`` itself is
shimmed to a no-op (the readiness poll must not slow the suite down).
"""
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "entrypoint.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")

# A daemon shim that records its start, exits 0 on SIGTERM (recording that
# too), and otherwise lingers far longer than any test timeout. The trap must
# kill the sleep child: an orphaned /bin/sleep keeps the inherited
# stdout/stderr pipes open, and Popen.communicate() would block on pipe EOF
# long after the entrypoint itself exited.
_DAEMON_SHIM = """#!/bin/bash
touch "{marker_dir}/{name}.started"
trap 'touch "{marker_dir}/{name}.termed"; kill "$CHILD" 2>/dev/null; exit 0' TERM
/bin/sleep 30 &
CHILD=$!
wait "$CHILD"
"""

_CRASH_SHIM = """#!/bin/bash
touch "{marker_dir}/{name}.started"
/bin/sleep 0.3
exit 1
"""

_EXIT_CODE_SHIM = """#!/bin/bash
touch "{marker_dir}/{name}.started"
/bin/sleep 0.3
exit {code}
"""


def _write_shim(bindir: Path, name: str, body: str) -> None:
    shim = bindir / name
    shim.write_text(body)
    shim.chmod(0o755)


def _setup(tmp_path, *, cupsd=None, avahi=None, uvicorn=None, lpstat=None):
    """Create the shim bin dir + marker dir; return (env, markers)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    markers = tmp_path / "markers"
    markers.mkdir()
    app_dir = tmp_path / "app"
    app_dir.mkdir()

    fmt = {"marker_dir": markers}
    _write_shim(bindir, "cupsd", (cupsd or _DAEMON_SHIM).format(name="cupsd", **fmt))
    _write_shim(bindir, "avahi-daemon", (avahi or _DAEMON_SHIM).format(name="avahi", **fmt))
    _write_shim(bindir, "uvicorn", (uvicorn or _DAEMON_SHIM).format(name="uvicorn", **fmt))
    _write_shim(bindir, "lpstat", (lpstat or "#!/bin/bash\nexit 0\n").format(**fmt))
    _write_shim(bindir, "python", "#!/bin/bash\nexit 0\n")  # alembic no-op
    _write_shim(bindir, "sleep", "#!/bin/bash\nexit 0\n")  # no real waiting

    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "PAPYRUS_APP_DIR": str(app_dir),
        "PAPYRUS_INGEST_TOKEN_FILE": str(tmp_path / "run" / "ingest.token"),
        "PAPYRUS_SCAN_DIR": str(tmp_path / "scans"),
        "PAPYRUS_UPLOAD_DIR": str(tmp_path / "uploads"),
    }
    return env, markers


def _run(env, *, timeout=15):
    """Run the entrypoint to completion. Returns (rc, stderr); rc is None on
    timeout (the pre-fix hang mode) -- the whole process group is killed so no
    /bin/sleep stragglers outlive the test."""
    proc = subprocess.Popen(
        ["bash", str(SCRIPT)],
        env=env, start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        _, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stderr
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        _, stderr = proc.communicate()
        return None, stderr


def _wait_for(path: Path, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}")


# --------------------------------------------------------------------------- #
# Supervision: a dead daemon must stop the container, not linger unnoticed.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("daemon", ["cupsd", "avahi"])
def test_daemon_death_stops_container_with_nonzero_exit(tmp_path, daemon):
    kwargs = {("cupsd" if daemon == "cupsd" else "avahi"): _CRASH_SHIM}
    env, markers = _setup(tmp_path, **kwargs)

    rc, stderr = _run(env)

    assert rc is not None, "entrypoint kept running after a daemon died (pre-fix hang)"
    assert rc != 0, f"expected non-zero exit after {daemon} died, got {rc}\n{stderr}"
    # The survivors were told to shut down rather than SIGKILLed by teardown.
    assert (markers / "uvicorn.termed").exists(), stderr
    other = "avahi" if daemon == "cupsd" else "cupsd"
    assert (markers / f"{other}.termed").exists(), stderr


# --------------------------------------------------------------------------- #
# Readiness: bounded lpstat -r poll instead of a blind sleep.
# --------------------------------------------------------------------------- #
def test_waits_for_cupsd_scheduler_before_starting_uvicorn(tmp_path):
    # lpstat fails twice, then reports the scheduler running. The entrypoint
    # must keep polling (>= 3 calls) and still start uvicorn.
    lpstat = (
        "#!/bin/bash\n"
        'echo x >> "{marker_dir}/lpstat.calls"\n'
        'if [ "$(wc -l < "{marker_dir}/lpstat.calls")" -lt 3 ]; then\n'
        '  echo "scheduler is not running"; exit 1\n'
        "fi\n"
        'echo "scheduler is running"; exit 0\n'
    )
    env, markers = _setup(tmp_path, uvicorn=_EXIT_CODE_SHIM.replace("{code}", "0"), lpstat=lpstat)

    rc, stderr = _run(env)

    assert rc == 0, stderr
    calls_file = markers / "lpstat.calls"
    assert calls_file.exists(), "entrypoint never polled lpstat for cupsd readiness"
    assert len(calls_file.read_text().splitlines()) >= 3


# --------------------------------------------------------------------------- #
# Refactor guards: the existing F102 shutdown semantics must survive.
# --------------------------------------------------------------------------- #
def test_sigterm_is_forwarded_to_all_children_and_waited_on(tmp_path):
    env, markers = _setup(tmp_path)

    proc = subprocess.Popen(
        ["bash", str(SCRIPT)],
        env=env, start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        _wait_for(markers / "uvicorn.started")
        proc.send_signal(signal.SIGTERM)
        _, stderr = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise AssertionError("entrypoint did not exit after SIGTERM")

    assert proc.returncode == 0, stderr
    for name in ("uvicorn", "cupsd", "avahi"):
        assert (markers / f"{name}.termed").exists(), f"{name} was not TERMed\n{stderr}"


def test_uvicorn_exit_code_is_the_container_exit_code(tmp_path):
    env, markers = _setup(tmp_path, uvicorn=_EXIT_CODE_SHIM.replace("{code}", "7"))

    rc, stderr = _run(env)

    assert rc == 7, f"expected uvicorn's exit code 7, got {rc}\n{stderr}"
    for name in ("cupsd", "avahi"):
        assert (markers / f"{name}.termed").exists(), f"{name} was not TERMed\n{stderr}"
