"""Ratcheting OpenAPI contract test.

Every operation must have a summary, a unique operationId, a documented
success response (schema, file, 204 or redirect) and a documented error
response. Operations that do not comply yet are listed in
``openapi_allowlist.txt``; the test also fails when an allowlisted operation
becomes compliant, so the list can only shrink. Regenerate it (only to
shrink) with ``OPENAPI_WRITE_ALLOWLIST=1 pytest tests/test_openapi_contract.py``.
"""
import os
from collections import Counter
from pathlib import Path

import pytest

from app.main import app

ALLOWLIST = Path(__file__).with_name("openapi_allowlist.txt")
METHODS = {"get", "put", "post", "delete", "patch"}


def _operations() -> list[tuple[str, str, dict]]:
    schema = app.openapi()
    return [
        (path, method, op)
        for path, item in schema["paths"].items()
        for method, op in item.items()
        if method in METHODS
    ]


def _problems(op: dict) -> list[str]:
    problems = []
    if not op.get("summary"):
        problems.append("missing summary")
    responses = op.get("responses", {})
    ok = False
    for code, resp in responses.items():
        if not code.startswith(("2", "3")):
            continue
        if code == "204" or code.startswith("3"):
            ok = True
            continue
        for media, body in resp.get("content", {}).items():
            if media != "application/json" or body.get("schema"):
                ok = True
    if not ok:
        problems.append("no typed success response")
    if not any(
        code[0] in "45" and "ErrorResponse" in str(resp.get("content", {}))
        for code, resp in responses.items()
    ):
        problems.append("no ErrorResponse error response")
    return problems


def _load_allowlist() -> set[str]:
    if not ALLOWLIST.exists():
        return set()
    return {ln.strip() for ln in ALLOWLIST.read_text().splitlines() if ln.strip()}


def test_operation_ids_unique():
    ids = Counter(op["operationId"] for _, _, op in _operations())
    assert [i for i, n in ids.items() if n > 1] == []


def test_contract_ratchet():
    noncompliant = {op["operationId"] for _, _, op in _operations() if _problems(op)}
    if os.environ.get("OPENAPI_WRITE_ALLOWLIST"):
        ALLOWLIST.write_text("\n".join(sorted(noncompliant)) + "\n")
        pytest.skip("allowlist written")
    allow = _load_allowlist()
    new = sorted(noncompliant - allow)
    fixed = sorted(allow - noncompliant)
    assert not new, f"Operations violate the API contract: {new}"
    assert not fixed, f"Now compliant - remove from openapi_allowlist.txt: {fixed}"
