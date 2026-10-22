# -*- coding: utf-8 -*-
"""The lifecycle of one task: running, pausing, paused, cancelling and how it ended.

A pause and a full stop both interrupt the loop at its next safe point; they
differ in what survives. A pause keeps the task's live resources (a dev server,
the virtual screen, a helper's state) and waits for the person's next message.
"Stop all" cancels the main agent, its helpers, the running calls and every
process the task owns. Neither undoes anything already done.

``pause_requested`` is reported until the loop actually reaches a safe point;
only then is the state ``paused`` — the interface must never claim a pause the
agent has not reached. Helpers are children: a pause or stop of the parent
reaches them, while a helper's own stop never reaches its parent.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

RUNNING = "running"
PAUSE_REQUESTED = "pause_requested"
PAUSED = "paused"
RESUMING = "resuming"
CANCELLING = "cancelling"
CANCELLED = "cancelled"
COMPLETED = "completed"
FAILED = "failed"
AWAITING_INPUT = "awaiting_input"
IDLE = "idle"

STATES = frozenset({RUNNING, PAUSE_REQUESTED, PAUSED, RESUMING, CANCELLING, CANCELLED, COMPLETED, FAILED,
                    AWAITING_INPUT, IDLE})
_FINAL = frozenset({CANCELLED, COMPLETED, FAILED})

# Why a task was stopped; recorded so a report never says "stopped" without who or what.
STOP_REASONS = frozenset({"user_paused", "user_cancelled", "app_closed", "timeout", "error", "crash", "parent"})


@dataclass(slots=True)
class StateChange:
    state: str
    reason: str
    at: float = field(default_factory=time.time)


class TaskControl:
    """Thread-safe control of one agent's task; callable from transport and UI threads."""

    def __init__(self, interrupt: threading.Event | None = None, *, name: str = "main") -> None:
        # Shared with the loop's existing safe-point checks: set by both a pause
        # and a cancel, so every place that already stops for a cancel also
        # stops for a pause.
        self.interrupt = interrupt or threading.Event()
        self.name = name
        self._lock = threading.RLock()
        self._state = IDLE
        self._reason = ""
        self._pause = threading.Event()
        self._cancel = threading.Event()
        self._children: list[TaskControl] = []
        self.history: list[StateChange] = []
        self.listeners: list[Callable[[str, str], None]] = []

    # --- reading ---------------------------------------------------------

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def pause_requested(self) -> bool:
        return self._pause.is_set() and not self._cancel.is_set()

    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def interrupted(self) -> bool:
        return self.interrupt.is_set()

    # --- requests from outside the loop -----------------------------------

    def request_pause(self, reason: str = "user_paused") -> bool:
        """Ask the loop (and every helper) to stop at the next safe point and keep its state."""
        with self._lock:
            if self._state not in (RUNNING, RESUMING, AWAITING_INPUT):
                return False
            self._pause.set()
            self.interrupt.set()
            self._set(PAUSE_REQUESTED, reason)
            children = list(self._children)
        for child in children:
            child.request_pause("parent")
        return True

    def request_cancel(self, reason: str = "user_cancelled") -> bool:
        """Stop everything this task runs; idempotent, also valid while paused."""
        with self._lock:
            if self._state in _FINAL:
                return False
            self._cancel.set()
            self.interrupt.set()
            self._set(CANCELLING, reason)
            children = list(self._children)
        for child in children:
            child.request_cancel("parent")
        return True

    # --- transitions made by the loop -------------------------------------

    def begin(self, resumed: bool = False) -> None:
        with self._lock:
            self._pause.clear()
            self._cancel.clear()
            self.interrupt.clear()
            self._set(RESUMING if resumed else RUNNING, "")

    def running(self) -> None:
        with self._lock:
            if self._state in (RESUMING, AWAITING_INPUT):
                self._set(RUNNING, "")

    def awaiting_input(self, waiting: bool) -> None:
        with self._lock:
            if waiting and self._state == RUNNING:
                self._set(AWAITING_INPUT, "")
            elif not waiting and self._state == AWAITING_INPUT:
                self._set(RUNNING, "")

    def reached_safe_point(self) -> str:
        """Record where an interrupted loop ended up: ``paused`` or ``cancelled``."""
        with self._lock:
            if self._cancel.is_set():
                self._set(CANCELLED, self._reason or "user_cancelled")
            else:
                self._set(PAUSED, self._reason or "user_paused")
            return self._state

    def finish(self, failed: bool = False) -> None:
        with self._lock:
            if self._state in (PAUSED, CANCELLED):
                return
            self._set(FAILED if failed else COMPLETED, "error" if failed else "")

    # --- helpers ---------------------------------------------------------

    def adopt(self, child: "TaskControl") -> None:
        with self._lock:
            self._children.append(child)

    def release(self, child: "TaskControl") -> None:
        with self._lock:
            if child in self._children:
                self._children.remove(child)

    def wait(self, seconds: float) -> bool:
        """Sleep up to ``seconds``; True when interrupted first."""
        return self.interrupt.wait(max(0.0, seconds))

    def _set(self, state: str, reason: str) -> None:
        self._state = state
        self._reason = reason
        self.history.append(StateChange(state, reason))
        del self.history[:-50]
        for listener in list(self.listeners):
            listener(state, reason)
