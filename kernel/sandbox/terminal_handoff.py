# -*- coding: utf-8 -*-
"""Move a foreground command that runs long into a background job without restarting it.

A restart would lose the work the command already did, so the supervised
process keeps running on its own thread and the job registry adopts that thread.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Callable

from kernel.agent.background_jobs import TAIL_CHARS, BackgroundJob, BackgroundLaunch
from kernel.sandbox.process_tree import SupervisedResult

# A foreground command still running after this long becomes a background job.
FOREGROUND_HANDOFF_SECONDS = 90.0
_POLL_SECONDS = 0.1
# How long an interrupted wait gives the supervisor to tear the subtree down.
_STOP_GRACE_SECONDS = 5.0

Supervise = Callable[[Callable[[str], None] | None, threading.Event | None], SupervisedResult]


class _OutputRelay:
    """Sends output to the foreground sink until a job adopts the command, then to the job."""

    def __init__(self, sink: Callable[[str], None] | None) -> None:
        self._sink = sink
        self._lock = threading.Lock()
        self._target: Callable[[str], None] | None = None
        # What the job must be told about: it never saw the first part of the run.
        self._seen = ""

    def feed(self, text: str) -> None:
        with self._lock:
            if self._target is not None:
                self._target(text)
                return
            self._seen = (self._seen + text)[-TAIL_CHARS:]
        if self._sink is not None:
            self._sink(text)

    def attach(self, target: Callable[[str], None]) -> None:
        with self._lock:
            self._target = target
            if self._seen:
                target(self._seen)


class ForegroundRun:
    """A supervised command on a worker thread that the caller may stop waiting for."""

    def __init__(
        self,
        supervise: Supervise,
        sink: Callable[[str], None] | None,
        user_cancel: threading.Event | None,
    ) -> None:
        self._supervise = supervise
        self._relay = _OutputRelay(sink)
        self._user_cancel = user_cancel
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._outcome: SupervisedResult | None = None
        self._error: Exception | None = None
        threading.Thread(target=self._work, name="foreground-run", daemon=True).start()

    def _work(self) -> None:
        try:
            self._outcome = self._supervise(self._relay.feed, self._cancel)
        except Exception as error:  # re-raised by result() on the caller's thread
            self._error = error
        finally:
            self._done.set()

    def _forward_cancel(self, requested: threading.Event | None) -> None:
        if requested is not None and requested.is_set():
            self._cancel.set()

    def wait(self, seconds: float) -> bool:
        """True when the command ended within ``seconds``; the person's stop is passed on."""
        end = time.monotonic() + seconds
        try:
            while not self._done.wait(_POLL_SECONDS):
                self._forward_cancel(self._user_cancel)
                if time.monotonic() >= end:
                    return False
        except BaseException:
            # The command owns its own session: without this it would outlive the interrupt.
            self._cancel.set()
            self._done.wait(_STOP_GRACE_SECONDS)
            raise
        return True

    def wait_to_end(self) -> None:
        self.wait(math.inf)

    def result(self) -> SupervisedResult:
        if self._error is not None:
            raise self._error
        assert self._outcome is not None
        return self._outcome

    def hand_to_jobs(
        self,
        launch: BackgroundLaunch,
        command: list[str],
        max_seconds: float,
        cleanup: Callable[[], None],
    ) -> BackgroundJob:
        """Register the running command as a job; ``cleanup`` runs once it has ended."""

        def adopted(on_output: Callable[[str], None], job_cancel: threading.Event) -> tuple[int, bool, bool]:
            try:
                self._relay.attach(on_output)
                while not self._done.wait(_POLL_SECONDS):
                    self._forward_cancel(job_cancel)
                outcome = self.result()
            finally:
                cleanup()
            return outcome.returncode, outcome.timed_out, outcome.cancelled

        return launch.jobs.launch(command, launch.purpose, launch.call_id, adopted, max_seconds=max_seconds)
