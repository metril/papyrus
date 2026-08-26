"""Tests for `resolve_static_file` (F1): the SPA fallback in `app.main` must
never serve a file outside `static_dir`, even when the requested path is a
traversal sequence, a %2f-decoded separator (uvicorn hands the route
already-decoded path segments, so this looks identical to a literal `..`
segment by the time it reaches the handler), or a symlink that escapes
`static_dir`.

These are pure filesystem tests against a `tmp_path` static dir — no ASGI
client involved — so they don't need `static_dir` to exist for the real app.
"""
import os

from app.main import resolve_static_file


def _make_static_dir(tmp_path):
    static_dir = tmp_path / "static"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "assets" / "x.js").write_text("console.log('hi');")
    (static_dir / "index.html").write_text("<html></html>")
    return static_dir


def test_resolves_an_existing_asset_under_static_dir(tmp_path):
    static_dir = _make_static_dir(tmp_path)

    resolved = resolve_static_file(str(static_dir), "assets/x.js")

    assert resolved == os.path.realpath(str(static_dir / "assets" / "x.js"))


def test_returns_none_for_a_missing_file(tmp_path):
    static_dir = _make_static_dir(tmp_path)

    assert resolve_static_file(str(static_dir), "assets/does-not-exist.js") is None


def test_returns_none_for_empty_path(tmp_path):
    static_dir = _make_static_dir(tmp_path)

    assert resolve_static_file(str(static_dir), "") is None


def test_rejects_dotdot_traversal_outside_static_dir(tmp_path):
    static_dir = _make_static_dir(tmp_path)
    # A file that exists but sits outside static_dir — traversal should not
    # reach it even though the target file is real.
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")

    assert resolve_static_file(str(static_dir), "../secret.txt") is None
    assert resolve_static_file(str(static_dir), "../../etc/passwd") is None


def test_rejects_a_decoded_traversal_sequence_reaching_proc_self_environ(tmp_path):
    """Uvicorn percent-decodes the raw request target before routing, so a
    request for `/..%2f..%2f..%2fproc/self/environ` arrives at the handler
    as the literal string `../../../proc/self/environ` — indistinguishable
    from a hand-typed `..` path. Simulate the post-decode string directly.
    """
    static_dir = _make_static_dir(tmp_path)
    decoded_path = "../../../proc/self/environ"

    assert resolve_static_file(str(static_dir), decoded_path) is None


def test_rejects_a_symlink_that_escapes_static_dir(tmp_path):
    static_dir = _make_static_dir(tmp_path)
    outside_target = tmp_path / "outside.txt"
    outside_target.write_text("should not be served")
    symlink = static_dir / "escape.txt"
    symlink.symlink_to(outside_target)

    assert resolve_static_file(str(static_dir), "escape.txt") is None


def test_accepts_a_symlink_that_stays_inside_static_dir(tmp_path):
    static_dir = _make_static_dir(tmp_path)
    inside_target = static_dir / "assets" / "x.js"
    symlink = static_dir / "alias.js"
    symlink.symlink_to(inside_target)

    resolved = resolve_static_file(str(static_dir), "alias.js")

    assert resolved == os.path.realpath(str(inside_target))


def test_rejects_a_sibling_directory_whose_name_is_prefixed_by_static_dir(tmp_path):
    """Guards against a naive `startswith(static_dir)` check (without the
    trailing separator) that would wrongly admit e.g. `static-evil/`."""
    static_dir = _make_static_dir(tmp_path)
    sibling = tmp_path / (static_dir.name + "-evil")
    sibling.mkdir()
    (sibling / "leak.txt").write_text("leak")

    assert resolve_static_file(str(static_dir), "../static-evil/leak.txt") is None
