"""Structured logging — one system, JSON, correlation attached automatically.

`docs/design/16-OBSERVABILITY.md` §1: the legacy codebase configures `structlog` and then
uses stdlib `logging` in every service, producing mixed-format output that cannot be
parsed. v2 has one path.

Stdlib-only for the same reason as `core.config`: the logging entry point must be usable
from every layer, including ones the boundary contract forbids from taking dependencies.
The JSON shape matches `16` §2 exactly, so swapping the emitter later changes no field.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from oipulse.observability.correlation import (
    current_causation_id,
    current_correlation_id,
)

__all__ = ["JsonFormatter", "configure_logging", "get_logger"]

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Render a record as one JSON object, with correlation attached from context."""

    def __init__(self, role: str = "unknown", instance_id: str = "local") -> None:
        super().__init__()
        self._role = role
        self._instance_id = instance_id

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
            "role": self._role,
            "instance_id": self._instance_id,
            "logger": record.name,
        }

        cid = current_correlation_id()
        if cid is not None:
            payload["correlation_id"] = cid
        zid = current_causation_id()
        if zid is not None:
            payload["causation_id"] = zid

        # Structured extras: log.info("state_built", extra={"underlying": "NIFTY"})
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(
    level: str = "info", role: str = "unknown", instance_id: str = "local"
) -> None:
    """Install the JSON formatter as the sole root handler.

    Existing handlers are removed rather than added to: leaving a default handler in
    place is how mixed-format output appears.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(role=role, instance_id=instance_id))
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
