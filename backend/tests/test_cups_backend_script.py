"""Regression test for the papyrus CUPS backend script's curl invocation.

The backend receives the document title/username from CUPS untrusted. Passing
them through ``curl -F`` lets a title beginning with '@'/'<' (curl's
read-from-file syntax) or containing ';' break curl entirely -> HTTP 000 ->
the job silently never reaches Papyrus. The fix sends every text field via
``curl --form-string`` and uses a fixed, safe filename for the uploaded part.

This test shims ``curl`` with a recorder and drives the real script, asserting
the hostile title is passed literally via --form-string and never via -F.

Also covers: F7 (the shared ingest-token header, read from a file the script
locates via ``PAPYRUS_INGEST_TOKEN_FILE`` so tests don't need the real
root-owned ``/run/papyrus/ingest.token``), F91 (the ``ingest_key`` idempotency
form field, built from the real ``/proc/sys/kernel/random/boot_id``), and F90
(curl's ``--connect-timeout``/``--max-time``).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "cups" / "papyrus-backend"
BOOT_ID_FILE = Path("/proc/sys/kernel/random/boot_id")


def _pairs(args, flag):
    """Values that immediately follow every occurrence of ``flag`` in argv."""
    return [args[i + 1] for i, a in enumerate(args) if a == flag and i + 1 < len(args)]


def _run_backend(tmp_path, *, http_code="201", title="doc", token_file=None, job_id="7"):
    """Drive the real backend script with curl (and sleep) shimmed.

    Returns (completed_process, recorded_argv, curl_call_count). The curl shim
    records the last invocation's argv, counts calls, and returns ``http_code``.
    ``sleep`` is a no-op so the retry loop doesn't actually wait. ``token_file``,
    when given, is passed via ``PAPYRUS_INGEST_TOKEN_FILE`` so a test can point
    the script at a temp file instead of the real ``/run/papyrus/ingest.token``
    (which won't exist, and isn't writable, outside the container).
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    args_file = tmp_path / "curl_args.txt"
    count_file = tmp_path / "curl_calls.txt"
    (bindir / "curl").write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$@" > "$CURL_ARGS_FILE"\n'
        'echo x >> "$CURL_CALLS_FILE"\n'
        f'printf "{http_code}"\n'
    )
    (bindir / "curl").chmod(0o755)
    (bindir / "sleep").write_text("#!/bin/bash\nexit 0\n")  # no real waiting
    (bindir / "sleep").chmod(0o755)

    src = tmp_path / "source.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "PAPYRUS_UPLOAD_DIR": str(upload_dir),
        "CURL_ARGS_FILE": str(args_file),
        "CURL_CALLS_FILE": str(count_file),
        "PRINTER": "Papyrus",
    }
    if token_file is not None:
        env["PAPYRUS_INGEST_TOKEN_FILE"] = str(token_file)
    else:
        # Make sure a real /run/papyrus/ingest.token on the host running the
        # test suite (unlikely, but possible) can't leak into the assertions.
        env.pop("PAPYRUS_INGEST_TOKEN_FILE", None)
    proc = subprocess.run(
        ["bash", str(SCRIPT), job_id, "alice", title, "1",
         "sides=two-sided media=A4", str(src)],
        env=env, capture_output=True, text=True, timeout=30,
    )
    args = args_file.read_text().splitlines() if args_file.exists() else []
    calls = len(count_file.read_text().splitlines()) if count_file.exists() else 0
    return proc, args, calls


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_backend_passes_untrusted_title_via_form_string(tmp_path):
    # Hostile title: leading '@' (curl read-from-file) + ';' (param separator) —
    # the exact shape that returned HTTP 000 and dropped the job.
    title = "@Quarterly;report"
    proc, args, _ = _run_backend(tmp_path, http_code="201", title=title)
    assert proc.returncode == 0, proc.stderr

    # Title is sent literally via --form-string ...
    assert f"title={title}" in _pairs(args, "--form-string")
    # ... and NEVER via -F (that is the bug being fixed).
    assert all(title not in v for v in _pairs(args, "-F")), _pairs(args, "-F")

    # The uploaded part uses a fixed, safe filename — not the title.
    file_arg = next(v for v in _pairs(args, "-F") if v.startswith("file=@"))
    assert "filename=document.pdf" in file_arg
    assert title not in file_arg


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_backend_retries_then_fails_on_connection_error(tmp_path):
    # HTTP 000 (no connection, e.g. API restarting) → retried, then aborted.
    proc, _, calls = _run_backend(tmp_path, http_code="000")
    assert proc.returncode == 1
    assert calls == 10, f"expected 10 attempts, got {calls}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_backend_does_not_retry_on_client_error(tmp_path):
    # 4xx is a permanent rejection → fail immediately, no retry.
    proc, _, calls = _run_backend(tmp_path, http_code="422")
    assert proc.returncode == 1
    assert calls == 1, f"expected no retry, got {calls} calls"


# --------------------------------------------------------------------------- #
# F7: ingest-token header
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_backend_sends_ingest_token_header_when_file_present(tmp_path):
    token_file = tmp_path / "ingest.token"
    token_file.write_text("s3cr3t-token-value")

    proc, args, _ = _run_backend(tmp_path, http_code="201", token_file=token_file)
    assert proc.returncode == 0, proc.stderr

    headers = _pairs(args, "-H")
    assert "X-Papyrus-Ingest-Token: s3cr3t-token-value" in headers


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_backend_omits_ingest_token_header_when_file_missing(tmp_path):
    # No token_file given -> PAPYRUS_INGEST_TOKEN_FILE unset -> the script
    # falls back to its hard-coded default (/run/papyrus/ingest.token), which
    # does not exist in the test environment.
    proc, args, _ = _run_backend(tmp_path, http_code="201")
    assert proc.returncode == 0, proc.stderr

    assert _pairs(args, "-H") == []


# --------------------------------------------------------------------------- #
# F91: ingest_key idempotency form field
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
@pytest.mark.skipif(not BOOT_ID_FILE.is_file(), reason="no /proc/sys/kernel/random/boot_id")
def test_backend_sends_ingest_key_built_from_boot_id_printer_job_id(tmp_path):
    boot_id = BOOT_ID_FILE.read_text().strip()

    proc, args, _ = _run_backend(tmp_path, http_code="201", job_id="7")
    assert proc.returncode == 0, proc.stderr

    # env sets PRINTER=Papyrus in _run_backend; job id is the "7" argv above.
    expected = f"ingest_key={boot_id}:Papyrus:7"
    assert expected in _pairs(args, "--form-string")


# --------------------------------------------------------------------------- #
# F90: curl connect/max timeouts
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_backend_curl_has_connect_and_max_timeouts(tmp_path):
    proc, args, _ = _run_backend(tmp_path, http_code="201")
    assert proc.returncode == 0, proc.stderr

    assert "--connect-timeout" in args
    assert args[args.index("--connect-timeout") + 1] == "5"
    assert "--max-time" in args
    assert args[args.index("--max-time") + 1] == "300"
