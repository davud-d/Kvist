# -*- coding: utf-8 -*-
"""Wire-level log of what Kvist sends to a provider and what comes back.

Off unless ``KVIST_REQUEST_LOG=<file>`` is set. It exists for one job: handing a
provider's support the exact request behind a failure, with its request id, so
they do not have to guess from a description. One JSON line per exchange,
written when the response stream closes, so an error that arrives in the middle
of a stream is in the line too.

The log sits under the transport, not around the SDK call, because a streamed
reply is only known to be good or bad after its last chunk. Two honest costs:
the request asks for an uncompressed reply (the transport sees the bytes before
httpx decodes them), and a client built on a custom transport no longer reads
the proxy variables of the environment.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from kernel.durable_json import JsonStorageError, append_private_bytes
from kernel.redaction import redact_text, redact_value

REQUEST_LOG_ENV = "KVIST_REQUEST_LOG"

_HEAD_BYTES = 8 * 1024
_TAIL_BYTES = 56 * 1024
_ids = itertools.count(1)
_logger = logging.getLogger(__name__)


def request_log_path() -> Path | None:
    raw = os.environ.get(REQUEST_LOG_ENV, "").strip()
    return Path(raw).expanduser() if raw else None


def wrap_transport(transport: httpx.BaseTransport) -> httpx.BaseTransport:
    """The transport with logging on top, or the transport itself when the log is off."""
    path = request_log_path()
    return _LoggingTransport(transport, path) if path else transport


def _json_or_text(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    try:
        return redact_value(json.loads(text))
    except json.JSONDecodeError:
        return redact_text(text)


class _Capture:
    """The start and the end of a body: a cut keeps both, like the journal's."""

    def __init__(self) -> None:
        self._head = bytearray()
        self._tail = bytearray()
        self.size = 0

    def add(self, chunk: bytes) -> None:
        self.size += len(chunk)
        room = _HEAD_BYTES - len(self._head)
        if room > 0:
            self._head += chunk[:room]
            chunk = chunk[room:]
        self._tail += chunk
        del self._tail[:-_TAIL_BYTES]

    def text(self) -> str:
        omitted = self.size - len(self._head) - len(self._tail)
        head = self._head.decode("utf-8", errors="replace")
        tail = self._tail.decode("utf-8", errors="replace")
        if omitted <= 0:
            return redact_text(head + tail)
        return redact_text(f"{head}\n[... {omitted} bytes omitted ...]\n{tail}")


class _TeeStream(httpx.SyncByteStream):
    def __init__(self, inner: httpx.SyncByteStream, finish) -> None:
        self._inner = inner
        self._finish = finish
        self._capture = _Capture()
        self._error: str | None = None
        self._done = False

    def __iter__(self) -> Iterator[bytes]:
        try:
            for chunk in self._inner:
                self._capture.add(chunk)
                yield chunk
        except httpx.HTTPError as error:
            self._error = f"{type(error).__name__}: {error}"
            raise

    def close(self) -> None:
        self._inner.close()
        if self._done:
            return
        self._done = True
        self._finish(self._capture, self._error)


class _LoggingTransport(httpx.BaseTransport):
    def __init__(self, inner: httpx.BaseTransport, path: Path) -> None:
        self._inner = inner
        self._path = path

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request.headers["accept-encoding"] = "identity"
        body = request.read()
        started = time.monotonic()
        record: dict[str, Any] = {
            "id": next(_ids),
            "time": datetime.now(timezone.utc).astimezone().isoformat(),
            "method": request.method,
            "url": str(request.url),
            "request_bytes": len(body),
            "request_body": _json_or_text(body) if body else None,
        }
        try:
            response = self._inner.handle_request(request)
        except httpx.HTTPError as error:
            record["error"] = f"{type(error).__name__}: {error}"
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self._write(record)
            raise

        def finish(capture: _Capture, error: str | None) -> None:
            record["status"] = response.status_code
            record["response_headers"] = {
                name: value
                for name, value in response.headers.items()
                if name.lower() != "set-cookie"
            }
            record["response_bytes"] = capture.size
            record["response_body"] = capture.text()
            if error:
                record["error"] = error
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self._write(record)

        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=_TeeStream(response.stream, finish),
            extensions=response.extensions,
        )

    def _write(self, record: dict[str, Any]) -> None:
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            append_private_bytes(self._path, line, boundary=self._path.parent)
        except (OSError, JsonStorageError) as error:
            _logger.warning("request log not written: %s", error)

    def close(self) -> None:
        self._inner.close()
