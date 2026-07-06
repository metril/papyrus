"""Logging setup: structured (JSON) or plain dev output, request-ID aware.

Uses stdlib ``logging.config.dictConfig`` only (no structlog). All app and
uvicorn log records flow through a single stderr handler on the root logger
so output is uniform regardless of which module's ``logging.getLogger(name)``
produced the record.
"""
import json
import logging
import logging.config
from datetime import datetime, timezone

from app.request_context import get_request_id

DEV_FORMAT = "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s"


class RequestIdFilter(logging.Filter):
    """Attaches the current request ID (or "-" outside a request) to every
    log record, so both formatters below can reference it uniformly."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
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
                "filters": ["request_id"],
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
