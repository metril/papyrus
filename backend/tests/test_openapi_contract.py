"""Strict OpenAPI contract test.

Every operation must have a summary, a unique operationId, a documented
success response (schema, file, 204 or redirect) and a documented error
response.
"""
from collections import Counter

from app.main import app

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
        code[0] in "45" and code != "422" and "ErrorResponse" in str(resp.get("content", {}))
        for code, resp in responses.items()
    ):
        problems.append("no ErrorResponse error response")
    return problems


def test_operation_ids_unique():
    ids = Counter(op["operationId"] for _, _, op in _operations())
    assert [i for i, n in ids.items() if n > 1] == []


def test_operations_comply_with_contract():
    bad = {op["operationId"]: _problems(op) for _, _, op in _operations() if _problems(op)}
    assert not bad, f"Operations violate the API contract: {bad}"


def test_no_default_validation_schemas():
    schemas = app.openapi()["components"]["schemas"]
    assert "HTTPValidationError" not in schemas
    assert "ValidationError" not in schemas
    assert "HTTPValidationError" not in str(app.openapi()["paths"])
