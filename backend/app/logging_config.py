"""Logging setup: structured (JSON) or plain dev output, request-ID aware.

Uses stdlib ``logging.config.dictConfig`` only (no structlog). All app and
uvicorn log records flow through a single stderr handler on the root logger
so output is uniform regardless of which module's ``logging.getLogger(name)``
produced the record.
"""
import json
import logging
import logging.config
import re
from datetime import datetime, timezone

from app.request_context import get_request_id

DEV_FORMAT = "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s"

_TOKEN_QUERY_RE = re.compile(r"([?&]token=)[^&\s\"]+")


class RequestIdFilter(logging.Filter):
    """Attaches the current request ID (or "-" outside a request) to every
    log record, so both formatters below can reference it uniformly."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


class RedactWebSocketTokenFilter(logging.Filter):
    """Redacts the WS `?token=<plaintext>` query param from log records.

    `app/auth/ws.py`'s `?token=` fallback (browsers can't set a custom
    Authorization header on a WebSocket handshake) puts a plaintext API
    token straight into the request path. Uvicorn logs that full path --
    with the query string -- for every WS handshake, accepted or rejected:
    plain HTTP access lines go through the `uvicorn.access` logger
    (`h11_impl.py`, path as the 3rd positional `%s` arg), but the WS
    accept/reject/close lines specifically go through `uvicorn.error`
    (`websockets_impl.py`/`wsproto_impl.py`, path as the 2nd positional
    `%s` arg) -- so this doesn't hardcode either logger name or arg
    position: it scans every string in `record.args` for a `token=` query
    string and redacts it in place, and is attached to the shared handler
    below so it runs for every record regardless of which logger emitted
    it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(
                _TOKEN_QUERY_RE.sub(r"\1[redacted]", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


class JSONFormatter(logging.Formatter):
    """Renders each record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(json_logs: bool) -> None:
    """Configure root/uvicorn logging. Safe to call more than once — each
    call fully replaces the handlers on the loggers it targets rather than
    appending to them, so it never produces duplicate output."""
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_id": {"()": RequestIdFilter},
            "redact_ws_token": {"()": RedactWebSocketTokenFilter},
        },
        "formatters": {
            "json": {"()": JSONFormatter},
            "dev": {"format": DEV_FORMAT},
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "formatter": "json" if json_logs else "dev",
                # redact_ws_token runs here (not just on the uvicorn.access
                # logger) because every uvicorn/app logger propagates to
                # this one shared handler, and the WS handshake
                # accept/reject/close lines that actually carry the
                # `?token=` query string go through `uvicorn.error`, not
                # `uvicorn.access` -- see RedactWebSocketTokenFilter's
                # docstring.
                "filters": ["request_id", "redact_ws_token"],
            },
        },
        "root": {
            "level": "INFO",
            "handlers": ["default"],
        },
        "loggers": {
            # Delegate to the root handler instead of installing their own,
            # so uvicorn's own startup/access logs come out in our format too.
            "uvicorn": {"handlers": [], "propagate": True},
            "uvicorn.error": {"handlers": [], "propagate": True},
            "uvicorn.access": {"handlers": [], "propagate": True},
        },
    }
    logging.config.dictConfig(config)
