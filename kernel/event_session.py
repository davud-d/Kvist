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

import shlex
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

from kernel.frontend_protocol import ErrorEvent, FrontendChannel


class EventApprovalGate:
    """`approvals.ApprovalGate` that confirms via a `FrontendChannel` request."""

    def __init__(self, channel: FrontendChannel, mode: str = "ask", workspace: str | Path | None = None) -> None:
        if mode not in {"ask", "deny"}:
            raise ValueError("Approval mode must be 'ask' or 'deny'.")
        self._channel = channel
        self._mode = mode
        self._workspace = Path(workspace) if workspace else None
        self._remembered_pattern: str | None = None

    def confirm_command(self, command: list[str], reason: str) -> bool:
        self._remembered_pattern = None
        if self._mode == "deny":
            return False
        # Shell-join for display; the pattern for "don't ask again" is the exact
        # argv joined with spaces (not shell-escaped), which is what the policy
        # layer matches.
        command_str = shlex.join(command)
        pattern = " ".join(command)
        # Intrinsic DANGEROUS commands must never be saved; the remember option
        # is shown only when the pattern is eligible.
        from kernel.command_policy import RiskLevel, assess_command
        assessment = assess_command(command, role="coder")
        remember_pattern = None if assessment.risk == RiskLevel.DANGEROUS else pattern

        response = self._channel.request(
            "confirm",
            "Dangerous command",
            body="",
            subject={"kind": "command", "command": command_str, "reason": reason},
            remember_pattern=remember_pattern,
        )
        if response.approved is True and response.remember is True and remember_pattern and self._workspace:
            self._remembered_pattern = remember_pattern
        return response.approved is True

    def commit_remembered_rule(self) -> None:
        pattern, self._remembered_pattern = self._remembered_pattern, None
        if pattern:
            self._save_allow_pattern(pattern)

    def confirm(self, title: str, body: str, question: str) -> bool:
        _ = question
        self._remembered_pattern = None
        if self._mode == "deny":
            return False
        # File overwrites: diff is in body, but we want it structured for the UI.
        # The diff preview starts after "overwrite <path>:\n\n" and is already unified.
        subject = None
        if "overwrite" in title.lower() and "\n\n" in body:
            parts = body.split("\n\n", 1)
            if parts[0].startswith("The agent wants to overwrite "):
                path = parts[0].split("overwrite ", 1)[1].rstrip(":")
                diff = parts[1] if len(parts) > 1 else ""
                subject = {"kind": "overwrite", "path": path, "diff": diff}
        response = self._channel.request(
            "confirm",
            title,
            body=body if not subject else "",
            subject=subject,
            remember_pattern=None,  # Overwrites and capability grants are never saved
        )
        return response.approved is True

    def _save_allow_pattern(self, pattern: str) -> None:
        """Append the pattern to project allow rules."""
        if not self._workspace:
            return
        from kernel.permission_policy import append_allow_rule
        try:
            append_allow_rule(self._workspace, pattern)
        except (OSError, ValueError) as error:
            # Best-effort: the approval itself already went through.
            print(f"Failed to save allow rule: {error}", file=sys.stderr)


class EventSession:
    """Drive a LocalAgent on a worker thread and expose its events as a stream."""

    def __init__(self, channel: FrontendChannel, agent) -> None:
        self._channel = channel
        self._agent = agent
        self._worker: threading.Thread | None = None
        self.result: str | None = None
        self.error: Exception | None = None

    @property
    def agent(self):
        """The wrapped LocalAgent. Read-only handle for launch-time metadata."""
        return self._agent

    def submit(self, user_goal: str, routed_role: str | None = None) -> None:
        self._start_worker(self._run, (user_goal, routed_role))

    def submit_call(self, target: Callable[[], object]) -> None:
        """Run ``target`` on the worker thread so its dialogs can stream.

        A command wizard blocks on input requests; running it off the caller's
        thread lets ``events()`` keep delivering those requests. The return
        value lands in ``result`` like a run's answer does.
        """
        self._start_worker(self._run_call, (target,))

    def submit_resume(self) -> None:
        """Resume the active session's checkpoint instead of running a new goal."""
        self._start_worker(self._run_resume, ())

    def replace_agent(self, agent) -> None:
        """Adopt a rebuilt agent (provider/model switch) for the next run.

        Called from a command's own worker, so it cannot refuse while a worker
        is alive; the transport only runs commands between turns.
        """
        self._agent = agent

    def emit(self, event) -> None:
        """Queue an out-of-band event from any thread, in stream order."""
        self._channel.emit(event)

    def _start_worker(self, target, args: tuple) -> None:
        if self.is_running():
            raise RuntimeError("A run is already active on this session.")
        self.result = None
        self.error = None
        worker = threading.Thread(target=target, args=args, daemon=True)
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

    def _run_call(self, target: Callable[[], object]) -> None:
        try:
            self.result = target()
        except Exception as exc:
            self.error = exc
            self._channel.emit(ErrorEvent(str(exc)))

    def _run_resume(self) -> None:
        try:
            result = self._agent.resume()
        except Exception as exc:
            self.error = exc
            self._channel.emit(ErrorEvent(str(exc)))
            return
        if result is None:
            self._channel.emit(ErrorEvent("No saved checkpoint to resume."))
            return
        self.result = result

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

    def respond(
        self, request_id: str, *, value=None, approved=None, remember: bool = False
    ) -> None:
        self._channel.respond(
            request_id, value=value, approved=approved, remember=remember
        )

    def cancel(self) -> None:
        """Ask the active run to stop at its next safe point.

        Delegates straight to the agent's transport-agnostic ``request_cancel``.
        Harmless when idle: the flag is cleared at the start of the next run.
        """
        self._agent.request_cancel()

    def close(self, timeout: float = 5.0) -> None:
        self._channel.close()
        worker = self._worker
        if worker is not None:
            worker.join(timeout=timeout)

    def is_running(self) -> bool:
        worker = self._worker
        return worker is not None and worker.is_alive()
