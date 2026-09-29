# -*- coding: utf-8 -*-
"""Structured JSONL performance logger.

Logs go to `.kvist/logs/debug/logs.jsonl` inside the workspace.
Controlled by the ``DEBUG_LOGGING`` constant below.  Set it to ``True``
to enable recording; the default is ``False`` so the logger is a no-op
in production and adds zero overhead.
"""
from __future__ import annotations

import json
import os
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kernel.durable_json import (
    JsonStorageError,
    exclusive_storage_lock,
    validate_storage_path,
)
from kernel.redaction import redact_text, redact_value

# ──────────────────────────────────────────────────────────────────────
# Flip this to True when you need debug performance logging.
# In normal operation this MUST be False.
# ──────────────────────────────────────────────────────────────────────
DEBUG_LOGGING: bool = False
_FLUSH_THRESHOLD = 10  # flush buffer every N records
_MAX_ERROR_LEN = 300   # truncate error strings in log entries


def _iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


class PerfLogger:
    """Buffered, thread-safe JSONL logger for performance metrics.

    When ``DEBUG_LOGGING`` is ``False`` every public method is a no-op.
    """

    def __init__(self, workspace: Path) -> None:
        self._enabled = DEBUG_LOGGING
        if not self._enabled:
            return
        self._workspace = workspace.resolve()
        self._log_dir = self._workspace / ".kvist" / "logs" / "debug"
        self._log_path = self._log_dir / "logs.jsonl"
        self._write_lock_path = self._log_dir / "performance-log"
        self._buffer: list[str] = []
        self._lock = threading.Lock()
        # render rate tracking
        self._render_timestamps: list[float] = []
        self._render_call_count = 0

    # ── helpers ───────────────────────────────────────────────────────

    def _append(self, record: dict[str, Any]) -> None:
        safe_record = redact_value(record)
        line = json.dumps(safe_record, ensure_ascii=False, default=str)
        with self._lock:
            self._buffer.append(line)
            if len(self._buffer) >= _FLUSH_THRESHOLD:
                self._flush_unlocked()

    def _flush_unlocked(self) -> None:
        """Write buffer to disk.  Caller must hold ``self._lock``."""
        if not self._buffer:
            return
        try:
            with exclusive_storage_lock(
                self._write_lock_path,
                boundary=self._workspace,
            ):
                path = validate_storage_path(
                    self._log_path,
                    self._workspace,
                )
                flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
                flags |= getattr(os, "O_CLOEXEC", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(path, flags, 0o600)
                try:
                    info = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_nlink > 1
                        or (
                            hasattr(os, "geteuid")
                            and info.st_uid != os.geteuid()
                        )
                    ):
                        raise OSError("unsafe performance log identity")
                    fchmod = getattr(os, "fchmod", None)
                    if fchmod is not None:
                        fchmod(descriptor, 0o600)
                    payload = (
                        "\n".join(self._buffer) + "\n"
                    ).encode("utf-8")
                    written = 0
                    while written < len(payload):
                        count = os.write(descriptor, payload[written:])
                        if count <= 0:
                            raise OSError("performance log write made no progress")
                        written += count
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except (JsonStorageError, OSError):
            pass  # never crash the agent because of logging
        self._buffer.clear()

    # ── 1. API request timing + context + streaming ──────────────────

    def log_api_request(
        self,
        *,
        step: int,
        model: str,
        request_start: float,
        ttft: float | None = None,
        stream_end: float | None = None,
        total_duration: float,
        messages_count: int,
        input_tokens: int,
        estimated_tokens: int,
        prompt_chars: int,
        history_size_kb: float,
        chunk_count: int | None = None,
        avg_chunk_size: float | None = None,
        chunks_per_second: float | None = None,
        ok: bool = True,
        error: str | None = None,
    ) -> None:
        if not self._enabled:
            return
        record: dict[str, Any] = {
            "type": "api_request",
            "ts": _iso_now(),
            "step": step,
            "model": model,
            "timing": {
                "request_start": round(request_start, 4),
                "ttft": round(ttft, 4) if ttft is not None else None,
                "stream_end": round(stream_end, 4) if stream_end is not None else None,
                "total_duration": round(total_duration, 4),
            },
            "context": {
                "messages_count": messages_count,
                "input_tokens": input_tokens,
                "estimated_tokens": estimated_tokens,
                "prompt_chars": prompt_chars,
                "history_size_kb": round(history_size_kb, 2),
            },
            "streaming": {
                "chunk_count": chunk_count,
                "avg_chunk_size": round(avg_chunk_size, 2) if avg_chunk_size is not None else None,
                "chunks_per_second": round(chunks_per_second, 2) if chunks_per_second is not None else None,
            },
            "ok": ok,
            "error": redact_text(error)[:_MAX_ERROR_LEN] if error else None,
        }
        self._append(record)

    # ── 2. UI / render ───────────────────────────────────────────────

    def log_render(
        self,
        *,
        render_duration_ms: float,
    ) -> None:
        """Log a render event.

        Called from ``AgentUI.update_live``.  To avoid the logger itself
        becoming a bottleneck only *slow* renders (> 16 ms ≈ 60 fps
        budget) and every 10th call are actually persisted.
        """
        if not self._enabled:
            return

        now = time.monotonic()
        self._render_call_count += 1
        self._render_timestamps.append(now)
        # keep a sliding window of 20 timestamps for rate calculation
        self._render_timestamps = self._render_timestamps[-20:]

        is_slow = render_duration_ms > 16.0
        is_sampled = self._render_call_count % 10 == 0

        if not is_slow and not is_sampled:
            return

        # compute render calls per second from the sliding window
        if len(self._render_timestamps) >= 2:
            window = self._render_timestamps[-1] - self._render_timestamps[0]
            rps = (len(self._render_timestamps) - 1) / window if window > 0 else 0.0
        else:
            rps = 0.0

        record: dict[str, Any] = {
            "type": "render",
            "ts": _iso_now(),
            "render_duration_ms": round(render_duration_ms, 2),
            "render_calls_per_second": round(rps, 2),
        }
        self._append(record)

    # ── 3. Event loop lag ────────────────────────────────────────────

    def log_event_loop_lag(
        self,
        *,
        lag_ms: float,
        step: int,
        context: str,
    ) -> None:
        """Log an event-loop stall exceeding 100 ms."""
        if not self._enabled:
            return
        if lag_ms < 100.0:
            return
        record: dict[str, Any] = {
            "type": "event_loop_lag",
            "ts": _iso_now(),
            "lag_ms": round(lag_ms, 2),
            "step": step,
            "context": context,
        }
        self._append(record)

    # ── lifecycle ────────────────────────────────────────────────────

    def flush(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._flush_unlocked()

    def close(self) -> None:
        self.flush()
