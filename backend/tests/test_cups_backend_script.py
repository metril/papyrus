"""Regression test for the papyrus CUPS backend script's curl invocation.

The backend receives the document title/username from CUPS untrusted. Passing
them through ``curl -F`` lets a title beginning with '@'/'<' (curl's
read-from-file syntax) or containing ';' break curl entirely -> HTTP 000 ->
the job silently never reaches Papyrus. The fix sends every text field via
``curl --form-string`` and uses a fixed, safe filename for the uploaded part.

This test shims ``curl`` with a recorder and drives the real script, asserting
the hostile title is passed literally via --form-string and never via -F.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "cups" / "papyrus-backend"


def _pairs(args, flag):
    """Values that immediately follow every occurrence of ``flag`` in argv."""
    return [args[i + 1] for i, a in enumerate(args) if a == flag and i + 1 < len(args)]


def _run_backend(tmp_path, *, http_code="201", title="doc"):
    """Drive the real backend script with curl (and sleep) shimmed.

    Returns (completed_process, recorded_argv, curl_call_count). The curl shim
    records the last invocation's argv, counts calls, and returns ``http_code``.
    ``sleep`` is a no-op so the retry loop doesn't actually wait.
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
    proc = subprocess.run(
        ["bash", str(SCRIPT), "7", "alice", title, "1",
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
