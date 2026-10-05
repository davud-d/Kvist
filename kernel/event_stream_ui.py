# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

from kernel.frontend_protocol import (
    ActionOutputEvent,
    ActionResultEvent,
    DebugEvent,
    ErrorEvent,
    FinalEvent,
    FrontendChannel,
    LiveStartEvent,
    LiveStopEvent,
    MessageEvent,
    PlanEvent,
    ReasoningChunkEvent,
    ReasoningCommitEvent,
    ReasoningStartEvent,
    RouteEvent,
    SandboxNoticeEvent,
    SessionEndedEvent,
    SessionTitleEvent,
    StatusEvent,
    StepEvent,
    StreamChunkEvent,
    StreamCommitEvent,
    StreamStartEvent,
    UsageEvent,
)
from kernel.redaction import redact_text, redact_value


_OUTPUT_TAIL_CHARS = 4000


class EventStreamUI:
    """`ui.AgentUI` implementation that serializes agent output onto a channel.

    Every method maps to a `frontend_protocol` event and emits it; a remote
    frontend does the rendering and any verbosity filtering. Secret safety is
    provided by redaction (not terminal escaping) since the transport is data,
    not a terminal.
    """

    def __init__(
        self, channel: FrontendChannel, new_stream_id: Callable[[], str] | None = None
    ) -> None:
        self._channel = channel
        self.verbose = False
        # JournalingUI replaces the source so its journal copy of a stream
        # carries the id the live copy already sent.
        self._new_stream_id = new_stream_id or (lambda: uuid.uuid4().hex)
        self.last_stream_id = ""
        # The action loop streams no answer, so its reasoning has no stream of
        # its own: one is opened when the first thought arrives and sealed when
        # the model starts writing the action.
        self._loop_reasoning: tuple[str, float] | None = None
        self._loop_reasoning_reported = False
        self._call_started: float | None = None
        # Live command output arrives from the reader threads of a child
        # process; the rolling tail is emitted at most once per interval.
        self._output_lock = threading.Lock()
        self._output_step = -1
        self._output_call_id = ""
        self._output_tail = ""
        self._output_last_emit = 0.0
        self._output_timer: threading.Timer | None = None
        self.output_interval = 0.12
        # A response's words go out once, on the first step of its batch.
        self._said_batch = ""

    def start_live(self) -> None:
        self._channel.emit(LiveStartEvent())

    def stop_live(self) -> None:
        self.end_thinking()
        self._channel.emit(LiveStopEvent())

    def set_thinking(self, text: str) -> None:
        """Cumulative reasoning of the action call in flight ("" = a new call began)."""
        if not text:
            self.end_thinking()
            self._loop_reasoning_reported = False
            self._call_started = time.monotonic()
            return
        if self._loop_reasoning is None:
            # Timed from the call, not from the first fragment: a provider that
            # sends its reasoning in one piece after thinking silently showed
            # "thought for 0 s" for minutes of work.
            started = self._call_started or time.monotonic()
            self._loop_reasoning = (self._open_stream_id(), started)
            self._channel.emit(ReasoningStartEvent(self._loop_reasoning[0]))
        self._channel.emit(
            ReasoningChunkEvent(self._loop_reasoning[0], redact_text(text))
        )

    def _open_stream_id(self) -> str:
        self.last_stream_id = self._new_stream_id()
        return self.last_stream_id

    def end_thinking(self) -> None:
        if self._loop_reasoning is None:
            return
        stream_id, started = self._loop_reasoning
        self._loop_reasoning = None
        self._loop_reasoning_reported = True
        self._channel.emit(ReasoningCommitEvent(stream_id, time.monotonic() - started))

    def update_live(
        self,
        user_message: str,
        status: str,
        step: int,
        progress: int = 0,
        plan_step: str = "working",
        is_error: bool = False,
    ) -> None:
        self._channel.emit(
            StatusEvent(
                redact_text(user_message),
                status,
                step,
                progress,
                plan_step,
                is_error,
            )
        )

    def print_plan(self, markdown: str) -> None:
        self._channel.emit(PlanEvent(redact_text(markdown)))

    def print_step_header(
        self, step: int, command: Any, reasoning_seconds: float = 0.0
    ) -> None:
        self.end_thinking()
        # The thought already went out as its own reasoning stream: a second
        # "thought for N seconds" on the step would show it twice. A batch runs
        # every one of its actions through this call with the same per-call
        # ``reasoning_seconds`` (agent_runtime._run_one_action passes
        # ``self._last_reasoning_seconds`` unchanged for each action), so the
        # flag must stay set for the rest of the batch, not just its first
        # action — it is cleared only when the next model call begins
        # (``set_thinking("")``).
        if self._loop_reasoning_reported:
            reasoning_seconds = 0.0
        text = ""
        if not command.batch_id or command.batch_id != self._said_batch:
            self._said_batch = command.batch_id
            text = command.text
        self._channel.emit(
            StepEvent(
                step,
                command.plan_step,
                "",
                redact_text(text),
                str(command.action.type),
                redact_value(command.action.args),
                reasoning_seconds,
                redact_text(command.action.description),
                command.call_id,
                command.batch_id,
            )
        )

    def debug_print(self, message: str) -> None:
        self._channel.emit(DebugEvent(redact_text(message)))

    def print_usage(self, usage: dict) -> None:
        self._channel.emit(
            UsageEvent(
                input_tokens=max(0, int(usage.get("input_tokens", 0))),
                output_tokens=max(0, int(usage.get("output_tokens", 0))),
                model=redact_text(str(usage.get("model", ""))),
                kind=str(usage.get("kind", "")),
                step=max(0, int(usage.get("step", 0))),
                context_tokens=max(0, int(usage.get("context_tokens", 0))),
                context_limit=max(0, int(usage.get("context_limit", 0))),
            )
        )

    def print_action_output(self, step: int, text: str, call_id: str = "") -> None:
        """Feed the live output of a running command (called from reader threads)."""
        with self._output_lock:
            if step != self._output_step:
                self._reset_output(step)
                self._output_call_id = call_id
            self._output_tail = (self._output_tail + text)[-_OUTPUT_TAIL_CHARS:]
            wait = self._output_last_emit + self.output_interval - time.monotonic()
            if wait <= 0:
                self._emit_output()
            elif self._output_timer is None:
                self._output_timer = threading.Timer(wait, self._flush_output)
                self._output_timer.daemon = True
                self._output_timer.start()

    def _flush_output(self) -> None:
        with self._output_lock:
            self._output_timer = None
            # The command finished while the timer was waiting: its result owns the output now.
            if self._output_step >= 0:
                self._emit_output()

    def _emit_output(self) -> None:
        # The whole tail is redacted at once: a secret split across two reads
        # would slip through if each chunk were redacted alone.
        self._output_last_emit = time.monotonic()
        self._channel.emit(
            ActionOutputEvent(self._output_step, redact_text(self._output_tail), self._output_call_id)
        )

    def _reset_output(self, step: int) -> None:
        if self._output_timer is not None:
            self._output_timer.cancel()
            self._output_timer = None
        self._output_step = step
        self._output_tail = ""
        self._output_last_emit = 0.0

    def print_action_result(self, result: dict) -> None:
        with self._output_lock:
            self._reset_output(-1)
        path = result.get("path")
        self._channel.emit(
            ActionResultEvent(
                step=max(0, int(result.get("step", 0))),
                action_type=str(result.get("action_type", "")),
                ok=bool(result.get("ok")),
                summary=redact_text(str(result.get("summary", ""))),
                output=redact_text(str(result.get("output", ""))),
                exit_code=result.get("exit_code"),
                path=None if path is None else redact_text(str(path)),
                first_line=result.get("first_line"),
                call_id=str(result.get("call_id") or ""),
                batch_id=str(result.get("batch_id") or ""),
            )
        )

    def print_route(self, role: str, source: str) -> None:
        self._channel.emit(RouteEvent(str(role), str(source)))

    def print_session_title(self, session: str, title: str) -> None:
        self._channel.emit(SessionTitleEvent(str(session), str(title)))

    def print_session_ended(self, payload: dict) -> None:
        self._channel.emit(
            SessionEndedEvent(
                session_id=str(payload["session_id"]),
                warned_msg_id=payload.get("warned_msg_id"),
                closed_msg_id=int(payload["closed_msg_id"]),
                reason=redact_text(str(payload.get("reason", ""))),
                closed_notice=str(payload["closed_notice"]),
            )
        )

    @contextlib.contextmanager
    def live_markdown(self) -> Iterator[Callable[[str], None]]:
        stream_id = self._open_stream_id()
        self._channel.emit(StreamStartEvent(stream_id))
        last = ""
        last_reasoning = ""
        reasoning_start: float | None = None
        reasoning_committed = False

        def tail(previous: str, current: str) -> str:
            """Return the append-only delta, or the snapshot if text rewound."""
            return current[len(previous) :] if current.startswith(previous) else current

        def commit_reasoning() -> None:
            nonlocal reasoning_committed
            if reasoning_start is None or reasoning_committed:
                return
            reasoning_committed = True
            duration = time.monotonic() - reasoning_start
            self._channel.emit(ReasoningCommitEvent(stream_id, duration))

        def reasoning(message: str) -> None:
            nonlocal reasoning_start, last_reasoning
            if reasoning_committed:
                return
            if reasoning_start is None:
                reasoning_start = time.monotonic()
                self._channel.emit(ReasoningStartEvent(stream_id))
            previous = last_reasoning
            redacted = redact_text(message)
            append = redacted.startswith(previous)
            last_reasoning = redacted
            self._channel.emit(ReasoningChunkEvent(stream_id, tail(previous, redacted), append=append))

        def update(message: str) -> None:
            nonlocal last
            # The first answer token means thinking is over: seal the reasoning
            # block so its text never leaks into the answer stream.
            commit_reasoning()
            redacted = redact_text(message)
            delta = tail(last, redacted)
            append = redacted.startswith(last)
            last = redacted
            self._channel.emit(StreamChunkEvent(stream_id, delta, append=append))

        update.reasoning = reasoning
        try:
            yield update
        finally:
            commit_reasoning()
            self._channel.emit(StreamCommitEvent(stream_id, redact_text(last)))

    def render_user_markdown(self, message: str, fallback_style: str = "") -> str:
        _ = fallback_style
        return redact_text(message)

    def print_user_markdown(self, message: str) -> None:
        self._channel.emit(MessageEvent(redact_text(message)))

    def print_final(
        self, final_message: str, user_messages: list[str] | None = None, outcome: str = "done"
    ) -> None:
        closed = outcome == "closed"
        self._channel.emit(
            FinalEvent(
                redact_text(final_message),
                [redact_text(m) for m in (user_messages or [])],
                "incomplete" if closed else outcome,
                closed=closed,
            )
        )

    def print_result(self, message: str) -> None:
        self._channel.emit(MessageEvent(redact_text(message)))

    def print_error(self, message: str) -> None:
        self._channel.emit(ErrorEvent(redact_text(message)))

    def print_sandbox_notice(self, note: str) -> None:
        if note:
            self._channel.emit(SandboxNoticeEvent(note))

    def choose_option(
        self,
        title: str,
        options: list[str] | list[dict[str, str]],
        current_value: str = "",
        help_text: str = "",
    ) -> str | None:
        normalized = [_normalize_option(option) for option in options]
        resp = self._channel.request(
            "choice",
            title,
            options=normalized,
            current_value=current_value,
            help_text=help_text,
        )
        return resp.value

    def ask_choice(self, title: str, options: list[tuple[str, str]]) -> str | None:
        """Ask a short runtime choice through the frontend's standard dialog.

        The step-budget prompt uses ``(value, label)`` pairs, while the shared
        IDE dialog speaks the same structured ``choice`` request as the other
        frontend wizards. Keeping this adapter here lets VS Code and JetBrains
        render the prompt without each implementing a second dialog.
        """
        return self.choose_option(
            title,
            [{"value": value, "label": label} for value, label in options],
        )

    def prompt_text(
        self,
        title: str,
        current_value: str = "",
        help_text: str = "",
    ) -> str | None:
        return self._channel.request(
            "text",
            title,
            current_value=current_value,
            help_text=help_text,
        ).value

    def prompt_secret(
        self,
        title: str,
        current_value: str = "",
        help_text: str = "",
    ) -> str | None:
        # current_value is intentionally not forwarded: a secret must never be
        # echoed back into the request event, and the returned value is not logged.
        _ = current_value
        return self._channel.request("secret", title, help_text=help_text).value

    def confirm(self, title: str, body: str = "") -> bool:
        """Yes/no gate for a destructive step; anything but an explicit yes is no."""
        response = self._channel.request("confirm", title, body=redact_text(body))
        return response.approved is True


def _normalize_option(option: str | dict[str, str]) -> dict[str, str]:
    if isinstance(option, dict):
        return option
    return {"value": option, "label": option}
