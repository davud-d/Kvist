# -*- coding: utf-8 -*-
"""Long-lived inline-completion process for IDE frontends (``--complete-server``).

An IDE asks for a completion on nearly every pause in typing. Starting
``kvist --complete`` each time paid interpreter start-up, the kernel imports and
a fresh TLS connection per request; this process pays them once.

Wire protocol, one JSON object per line.

stdin (IDE → Kvist):
  ``{"id": N, "file_path", "language", "prefix", "suffix"[, "edits": true]}``
                                                            start request N.
                                                            ``edits`` says the editor can draw a deletion.
  ``{"cancel": N}``                                         stop request N.
A new request also cancels every request still running: the editor has moved
on, and its answer could only be discarded.

stdout (Kvist → IDE), ASCII only:
  ``{"id": N, "delta": "text"}``   the next piece of the completion.
  ``{"id": N, "replace": {"before": b, "after": a, "text": "new"}}``
                                   only for ``edits`` requests: ``text`` takes the
                                   place of the ``b`` UTF-16 units before the cursor
                                   through the ``a`` after it. Sent instead of deltas.
  ``{"id": N, "done": true}``      the completion is complete.
  ``{"id": N, "error": "text"}``   the request failed.
A cancelled request sends nothing further. EOF on stdin ends the process.
"""
from __future__ import annotations

import io
import json
import sys
import threading
from dataclasses import asdict
from pathlib import Path
from typing import IO, Any

from kernel.completion import (
    CompletionError,
    Replacement,
    _ModelCaller,
    _build_caller,
    completion_target,
    iter_completion,
    prepare_completion,
    request_from_payload,
)
from kernel.redaction import sanitize_display_text


class _CallerCache:
    """Reuses the model client (and its HTTP connection) while the saved
    provider and fast model stay the same."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: tuple[str, str] | None = None
        self._entry: tuple[_ModelCaller, str] | None = None

    def get(self) -> tuple[_ModelCaller, str]:
        key = completion_target()
        with self._lock:
            if self._entry is None or self._key != key:
                self._entry = _build_caller()
                self._key = key
            return self._entry


class CompletionServer:
    def __init__(self, workspace: Path, out: IO[str]) -> None:
        self._workspace = workspace
        self._out = out
        self._out_lock = threading.Lock()
        self._running: dict[int, threading.Event] = {}
        self._running_lock = threading.Lock()
        self._callers = _CallerCache()

    def handle_line(self, line: str) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(message, dict):
            return
        if "cancel" in message:
            self._cancel(message["cancel"])
            return
        request_id = message.get("id")
        if not isinstance(request_id, int):
            return
        stop = threading.Event()
        with self._running_lock:
            for older in self._running.values():
                older.set()
            self._running[request_id] = stop
        threading.Thread(
            target=self._answer, args=(request_id, message, stop), daemon=True
        ).start()

    def _cancel(self, request_id: object) -> None:
        with self._running_lock:
            stop = self._running.get(request_id) if isinstance(request_id, int) else None
        if stop is not None:
            stop.set()

    def _send(self, message: dict[str, Any]) -> None:
        with self._out_lock:
            self._out.write(json.dumps(message) + "\n")
            self._out.flush()

    def _answer(self, request_id: int, payload: dict, stop: threading.Event) -> None:
        try:
            request = request_from_payload(payload)
            messages = prepare_completion(request, self._workspace)
            caller, model = self._callers.get()
            for piece in iter_completion(caller, model, request, messages, stop.is_set):
                if stop.is_set():
                    return
                if isinstance(piece, Replacement):
                    self._send({"id": request_id, "replace": asdict(piece)})
                    continue
                self._send({"id": request_id, "delta": piece})
            if not stop.is_set():
                self._send({"id": request_id, "done": True})
        except (CompletionError, RuntimeError, OSError) as error:
            if not stop.is_set():
                text = sanitize_display_text(str(error).strip() or type(error).__name__)
                self._send({"id": request_id, "error": text})
        finally:
            with self._running_lock:
                if self._running.get(request_id) is stop:
                    del self._running[request_id]


def serve(workspace: Path) -> None:
    """Serve completion requests from stdin until EOF.

    Anything else that prints (provider SDK warnings) goes to stderr so stdout
    stays a clean message stream.
    """
    out = sys.stdout
    sys.stdout = sys.stderr
    server = CompletionServer(workspace, out)
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    for line in stdin:
        server.handle_line(line)
