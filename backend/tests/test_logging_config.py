"""Tests for `app.logging_config`: the JSON/dev formatters, the request-ID
filter, and idempotency of `setup_logging`.

Formatter/filter behavior is exercised directly against hand-built
`LogRecord`s (no need to spin up real logging plumbing for that). The
idempotency test does call `setup_logging` against the real root logger, so
it snapshots/restores root logger state to avoid leaking configuration into
other tests.
"""
import json
import logging

import pytest

from app.logging_config import DEV_FORMAT, JSONFormatter, RequestIdFilter, setup_logging
from app.request_context import request_id_var


def _make_record(msg="hello world", exc_info=None) -> logging.LogRecord:
    return logging.LogRecord(
        name="app.some.module",
        level=logging.INFO,
        pathname=__file__,
        lineno=42,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )


def test_request_id_filter_defaults_to_dash_outside_a_request():
    assert request_id_var.get() is None
    record = _make_record()

    assert RequestIdFilter().filter(record) is True
    assert record.request_id == "-"


def test_request_id_filter_uses_the_current_request_id():
    token = request_id_var.set("req-abc123")
    try:
        record = _make_record()
        RequestIdFilter().filter(record)
        assert record.request_id == "req-abc123"
    finally:
        request_id_var.reset(token)


def test_json_formatter_output_parses_as_json_with_expected_keys():
    record = _make_record(msg="hello %s", exc_info=None)
    record.msg = "hello %s"
    record.args = ("world",)
    record.request_id = "req-xyz"

    line = JSONFormatter().format(record)
    payload = json.loads(line)

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.some.module"
    assert payload["message"] == "hello world"
    assert payload["request_id"] == "req-xyz"
    assert "ts" in payload
    # ISO8601 UTC timestamp round-trips.
    from datetime import datetime

    datetime.fromisoformat(payload["ts"])
    assert "exc_info" not in payload


def test_json_formatter_includes_exc_info_text_when_present():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _make_record(exc_info=sys.exc_info())
    record.request_id = "-"

    payload = json.loads(JSONFormatter().format(record))
    assert "exc_info" in payload
    assert "ValueError: boom" in payload["exc_info"]


def test_dev_format_contains_request_id_placeholder():
    assert "%(request_id)s" in DEV_FORMAT


@pytest.fixture
def _isolated_root_logger():
    """setup_logging() mutates the real root/uvicorn loggers via dictConfig.
    Snapshot and restore them so this test doesn't leak a stderr handler (or
    an emptied uvicorn.access handler list) into other test modules."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level

    uvicorn_loggers = ["uvicorn", "uvicorn.error", "uvicorn.access"]
    original_uvicorn = {
        name: (list(logging.getLogger(name).handlers), logging.getLogger(name).propagate)
        for name in uvicorn_loggers
    }

    yield

    root.handlers = original_handlers
    root.setLevel(original_level)
    for name, (handlers, propagate) in original_uvicorn.items():
        lg = logging.getLogger(name)
        lg.handlers = handlers
        lg.propagate = propagate


def test_setup_logging_is_idempotent(_isolated_root_logger):
    setup_logging(json_logs=True)
    root = logging.getLogger()
    handlers_after_first = list(root.handlers)
    assert len(handlers_after_first) == 1

    setup_logging(json_logs=True)
    handlers_after_second = list(root.handlers)

    assert len(handlers_after_second) == 1
    assert isinstance(handlers_after_second[0], logging.StreamHandler)


def test_setup_logging_configures_uvicorn_loggers_to_propagate_without_own_handlers(
    _isolated_root_logger,
):
    setup_logging(json_logs=False)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        assert lg.handlers == []
        assert lg.propagate is True
