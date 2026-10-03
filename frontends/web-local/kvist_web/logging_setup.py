"""Structured component logs with mandatory secret redaction."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .security import redact


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "level": record.levelname.lower(),
            "component": getattr(record, "component", record.name),
            "message": redact(record.getMessage()),
        }
        for key in ("server", "workspace", "session", "connection", "event"):
            value = getattr(record, key, None)
            if value not in (None, ""):
                payload[key] = redact(value)
        process_id = getattr(record, "process_id", None)
        if process_id not in (None, ""):
            payload["process"] = redact(process_id)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(path: Path, *, verbose: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("kvist.web")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.propagate = False
