# -*- coding: utf-8 -*-
"""The daemon's output: an append-only NDJSON file that every attached frontend reads.

``serve_session_multi`` writes one event per ``write`` call. The same file serves a late frontend
(replay from the start) and a live one (tail from an offset), so there is one mechanism, not two.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from kernel.session.session_events import _TRANSIENT_EVENTS as TRANSIENT_EVENTS
from kernel.ssh.wire import event_type

SEGMENT_LIMIT = 64 * 1024 * 1024
_TURN_END = b'{"type": "turn_end"}\n'
# Re-written at the top of a new segment so a frontend that starts there still gets its handshake.
_HEADER_TYPES = ("hello", "manifest", "session")


class EventLog:
    """Thread-safe ``out_stream`` for the daemon.

    A new segment starts with the daemon; the previous file is kept as ``<name>.1``. A running
    daemon starts another one at a turn boundary once the file is large and nobody is reading.
    """

    def __init__(self, path: Path, *, limit: int = SEGMENT_LIMIT, after_manifest: str | None = None) -> None:
        self.path = path
        self._limit = limit
        self._after_manifest = after_manifest.encode("utf-8") if after_manifest else None
        self._grown = threading.Condition()
        self._readers = 0
        # Turns that ended while no frontend was attached: work nobody has seen yet.
        self._unseen_turns = 0
        self._header: dict[str, bytes] = {}
        self._fd = -1
        self._size = 0
        self.segment = 0
        self._open_segment()

    def _open_segment(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
        if self.path.exists():
            os.replace(self.path, self.path.with_name(self.path.name + ".1"))
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        self._fd = os.open(self.path, flags, 0o600)
        self._size = 0
        self.segment = max(self.segment + 1, time.time_ns() // 1000)

    def _append(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            view = view[os.write(self._fd, view):]
        self._size += len(data)

    def write(self, text: str) -> None:
        data = text.encode("utf-8")
        kind = event_type(data)
        with self._grown:
            self._append(data)
            if kind in _HEADER_TYPES:
                self._header[kind] = data
            if kind == "manifest" and self._after_manifest:
                self._append(self._after_manifest)
                self._after_manifest = None
            if kind == "turn_end":
                if not self._readers:
                    self._unseen_turns += 1
                os.fsync(self._fd)
                self._rotate_when_large()
            self._grown.notify_all()

    def flush(self) -> None:
        """Every write already reached the file."""

    def _rotate_when_large(self) -> None:
        if self._size < self._limit or self._readers:
            return
        self._open_segment()
        for kind in _HEADER_TYPES:
            if kind in self._header:
                self._append(self._header[kind])

    def size(self) -> int:
        with self._grown:
            return self._size

    def take_unseen(self) -> bool:
        """Whether a turn ended with nobody attached since the last call; asking clears it."""
        with self._grown:
            unseen, self._unseen_turns = self._unseen_turns > 0, 0
            return unseen

    def attach_reader(self) -> int:
        """Pin the current segment (it cannot rotate while read) and return its id."""
        with self._grown:
            self._readers += 1
            return self.segment

    def detach_reader(self) -> None:
        with self._grown:
            self._readers -= 1

    def wait_for_growth(self, offset: int, timeout: float) -> None:
        with self._grown:
            if self._size <= offset:
                self._grown.wait(timeout)


def lines_from(path: Path, offset: int) -> Iterator[tuple[bytes, int]]:
    """Complete lines after ``offset`` with the offset each one ends at."""
    with open(path, "rb") as handle:
        handle.seek(offset)
        for raw in handle:
            if not raw.endswith(b"\n"):
                return
            offset += len(raw)
            yield raw, offset


def _end_of_last_turn(path: Path) -> int:
    boundary = 0
    for raw, end in lines_from(path, 0):
        if raw == _TURN_END:
            boundary = end
    return boundary


def fresh_replay(path: Path, open_requests: set[str]) -> Iterator[tuple[bytes | None, int]]:
    """What a frontend that starts from nothing needs: the whole segment, minus what only mattered live.

    Finished turns lose their live-only events (``TRANSIENT_EVENTS``); the turn in progress stays whole.
    A dialog is replayed only while it is still open, so nothing already answered comes back. A skipped
    line yields ``None`` so the caller still learns how far the file was read.
    """
    boundary = _end_of_last_turn(path)
    for raw, end in lines_from(path, 0):
        kind = event_type(raw)
        if kind == "input_closed":
            yield None, end
        elif kind == "input_request" and request_id(raw) not in open_requests:
            yield None, end
        elif kind in TRANSIENT_EVENTS and end <= boundary:
            yield None, end
        else:
            yield raw, end


def request_id(raw: bytes) -> str:
    try:
        return json.loads(raw).get("request_id", "")
    except json.JSONDecodeError:
        return ""
