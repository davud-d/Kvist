# -*- coding: utf-8 -*-
"""Headless event session: run a LocalAgent on a worker thread and stream events.

The agent's synchronous ``run`` blocks, so a frontend that consumes structured
events needs it driven off the calling thread. ``EventSession`` owns that worker
thread and exposes the produced events as an iterator whose end-of-stream is
"worker finished AND the channel is drained". ``EventApprovalGate`` routes the
executor's human-in-the-loop confirmations through the same channel, staying
fail-closed when the channel is closed.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator

from frontend_protocol import ErrorEvent, FrontendChannel


class EventApprovalGate:
    """`approvals.ApprovalGate` that confirms via a `FrontendChannel` request."""

    def __init__(self, channel: FrontendChannel, mode: str = "ask") -> None:
        if mode not in {"ask", "deny"}:
            raise ValueError("Approval mode must be 'ask' or 'deny'.")
        self._channel = channel
        self._mode = mode

    def confirm_command(self, command: list[str], reason: str) -> bool:
        if self._mode == "deny":
            return False
        response = self._channel.request(
            "confirm",
            "Dangerous command",
            body=f"argv: {command}\nReason: {reason}",
        )
        return bool(response.approved)

    def confirm(self, title: str, body: str, question: str) -> bool:
        _ = question
        if self._mode == "deny":
            return False
        return bool(self._channel.request("confirm", title, body=body).approved)


class EventSession:
    """Drive a LocalAgent on a worker thread and expose its events as a stream."""

    def __init__(self, channel: FrontendChannel, agent) -> None:
        self._channel = channel
        self._agent = agent
        self._worker: threading.Thread | None = None
        self.result: str | None = None
        self.error: Exception | None = None

    def submit(self, user_goal: str, routed_role: str | None = None) -> None:
        if self.is_running():
            raise RuntimeError("A run is already active on this session.")
        self.result = None
        self.error = None
        worker = threading.Thread(
            target=self._run,
            args=(user_goal, routed_role),
            daemon=True,
        )
        self._worker = worker
        worker.start()

    def _run(self, user_goal: str, routed_role: str | None) -> None:
        try:
            self.result = self._agent.run(user_goal, routed_role)
        except Exception as exc:
            # Surface the failure both to the caller (``self.error``) and to the
            # event stream, so a transport watching only events still sees it.
            self.error = exc
            self._channel.emit(ErrorEvent(str(exc)))

    def events(self) -> Iterator:
        while True:
            event = self._channel.get(timeout=0.05)
            if event is not None:
                yield event
                continue
            worker = self._worker
            if worker is None or not worker.is_alive():
                for remaining in self._channel.drain():
                    yield remaining
                return

    def respond(self, request_id: str, *, value=None, approved=None) -> None:
        self._channel.respond(request_id, value=value, approved=approved)

    def close(self, timeout: float = 5.0) -> None:
        self._channel.close()
        worker = self._worker
        if worker is not None:
            worker.join(timeout=timeout)

    def is_running(self) -> bool:
        worker = self._worker
        return worker is not None and worker.is_alive()
