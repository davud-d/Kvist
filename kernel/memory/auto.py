# -*- coding: utf-8 -*-
"""Memory that keeps itself: sessions are read in the background, so Kvist is not a goldfish.

An interactive core reads a session after it has been quiet for a while, as
soon as the person leaves it (a new session, a switch), and at launch for the
latest sessions an earlier run did not get to (closing Kvist never waits for
a model). Nothing runs without the person's consent to processing, or while
memory is paused; both are read again before every job, so revoking takes
effect at once.

One worker thread, one job at a time, below the agent's own work: a job reads
only finished turns, and a turn starting meanwhile only postpones the quiet
timer. Leases keep two cores open on one workspace from paying to read the
same session, and a daily cap bounds what background reading can cost.
"""
from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from kernel.session.durable_json import JsonStorageError
from kernel.memory.errors import MemoryStoreError
from kernel.memory.extractor import ModelAccess, process_session
from kernel.memory.store import MemoryStore
from kernel.config.user_config import get_memory_paused, get_memory_processing_allowed

IDLE_SECONDS = 600
LEASE_SECONDS = 300
# Background model calls per workspace and day; a manual /memory process is not counted.
DAILY_CALLS = 40
CATCH_UP_SESSIONS = 3


class AutoProcessor:
    def __init__(self, workspace: Path, model: Callable[[], ModelAccess], debug: Callable[[str], None]) -> None:
        self._workspace = Path(workspace)
        self._model = model
        self._debug = debug
        self._holder = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._jobs: queue.Queue[str | None] = queue.Queue()
        self._queued: set[str] = set()
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._worker: threading.Thread | None = None
        self._closed = False

    def turn_started(self) -> None:
        self._cancel_timer()

    def turn_finished(self, session: str) -> None:
        """Read the session once it has been quiet for ``IDLE_SECONDS``; a new turn restarts the wait."""
        with self._lock:
            self._cancel_timer_locked()
            if self._closed:
                return
            self._timer = threading.Timer(IDLE_SECONDS, self.enqueue, args=(session,))
            self._timer.daemon = True
            self._timer.start()

    def session_left(self, session: str) -> None:
        self._cancel_timer()
        self.enqueue(session)

    def catch_up(self, exclude: str) -> None:
        """At launch: the latest sessions, in case an earlier run closed before reading them."""
        from kernel.session.session_search import list_sessions

        try:
            sessions = list_sessions(self._workspace, include_service=False)
        except (JsonStorageError, OSError, ValueError) as error:
            self._debug(f"[memory] catch-up skipped: {error}")
            return
        recent = sorted(sessions, key=lambda row: row.get("updated_at") or 0, reverse=True)
        for row in [row for row in recent if row["session"] != exclude][:CATCH_UP_SESSIONS]:
            self.enqueue(row["session"])

    def enqueue(self, session: str) -> None:
        if not _allowed():
            return
        with self._lock:
            if self._closed or session in self._queued:
                return
            self._queued.add(session)
            if self._worker is None:
                self._worker = threading.Thread(target=self._work, name="kvist-memory", daemon=True)
                self._worker.start()
        self._jobs.put(session)

    def close(self) -> None:
        """Stop taking jobs; a job in flight is abandoned with the process (its batch is read again later)."""
        with self._lock:
            self._closed = True
            self._cancel_timer_locked()
        self._jobs.put(None)

    def run_once(self, session: str) -> dict | None:
        """One job, synchronously: what the worker does for each queued session."""
        if not _allowed():
            return None
        store = MemoryStore.for_workspace(self._workspace)
        try:
            if not store.try_lease(session, self._holder, LEASE_SECONDS):
                self._debug(f"[memory] {session}: another Kvist is reading it")
                return None
            try:
                summary = process_session(store, session, self._model(), reserve=self._reserver(store))
            finally:
                store.release_lease(session, self._holder)
        except MemoryStoreError as error:
            self._debug(f"[memory] {session}: not read ({error})")
            return None
        self._debug(f"[memory] {session}: {len(summary['accepted'])} new, {len(summary['merged'])} matched, "
                    f"{len(summary['rejected'])} rejected" + (f"; {summary['problem']}" if summary["problem"] else ""))
        return summary

    def _reserver(self, store: MemoryStore) -> Callable[[int], int]:
        day = time.strftime("%Y-%m-%d")
        return lambda wanted: store.take_model_calls(day, wanted, DAILY_CALLS)

    def _work(self) -> None:
        while True:
            session = self._jobs.get()
            if session is None:
                return
            with self._lock:
                self._queued.discard(session)
            self.run_once(session)

    def _cancel_timer(self) -> None:
        with self._lock:
            self._cancel_timer_locked()

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


def _allowed() -> bool:
    return get_memory_processing_allowed() and not get_memory_paused()
