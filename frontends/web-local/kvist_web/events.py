"""In-memory live fan-out; durable replay remains the core session journal."""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

from .protocol import LiveEnvelope


@dataclass(slots=True)
class LiveReplay:
    """Result of reading the transient stream at one broker epoch.

    ``LiveEnvelope.id`` is intentionally only meaningful inside one controller
    process.  A browser must never mistake an old controller cursor for a
    durable session cursor, so a reset is explicit rather than silently waiting
    forever or pretending a bounded in-memory buffer is complete.
    """

    events: list[LiveEnvelope]
    cursor: int
    epoch: str
    reset: bool = False
    reason: str = ""


class EventBroker:
    """Broadcast bounded live events without making them a second session DB."""

    def __init__(self, *, capacity: int = 4_000) -> None:
        self._capacity = capacity
        self._events: deque[LiveEnvelope] = deque(maxlen=capacity)
        self._next_id = 1
        self._epoch = uuid.uuid4().hex
        self._condition = threading.Condition()

    def publish(
        self,
        server_id: str,
        workspace: str,
        session: str,
        event: dict[str, Any],
        *,
        durable: bool = False,
    ) -> LiveEnvelope:
        with self._condition:
            envelope = LiveEnvelope(
                id=self._next_id,
                server_id=server_id,
                workspace=workspace,
                session=session,
                event=event,
                ts=time.time(),
                durable=durable,
            )
            self._next_id += 1
            self._events.append(envelope)
            self._condition.notify_all()
            return envelope

    def after(self, cursor: int, *, timeout: float = 20.0) -> list[LiveEnvelope]:
        """Compatibility view for bridge callers that only need live events.

        The browser-facing HTTP layer uses :meth:`read_after`, which exposes a
        reset/gap rather than losing that information.
        """
        return self.read_after(cursor, timeout=timeout).events

    def read_after(
        self,
        cursor: int,
        *,
        epoch: str | None = None,
        timeout: float = 20.0,
    ) -> LiveReplay:
        """Read transient events, explicitly reporting a stale or evicted cursor."""
        cursor = max(0, int(cursor))
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                current = self._next_id - 1
                if epoch is not None and epoch != self._epoch:
                    return LiveReplay([], current, self._epoch, True, "epoch_changed")
                if cursor > current:
                    return LiveReplay([], current, self._epoch, True, "cursor_ahead")
                oldest = self._events[0].id if self._events else self._next_id
                if cursor < oldest - 1:
                    return LiveReplay([], current, self._epoch, True, "history_gap")
                if current > cursor:
                    return LiveReplay(
                        [event for event in self._events if event.id > cursor],
                        current,
                        self._epoch,
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return LiveReplay([], current, self._epoch)
                self._condition.wait(remaining)

    @property
    def cursor(self) -> int:
        with self._condition:
            return self._next_id - 1

    @property
    def epoch(self) -> str:
        return self._epoch
