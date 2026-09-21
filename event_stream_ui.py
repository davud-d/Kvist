# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

from frontend_protocol import (
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
    StatusEvent,
    StepEvent,
    StreamChunkEvent,
    StreamCommitEvent,
    StreamStartEvent,
    UsageEvent,
)
from redaction import redact_text, redact_value


_OUTPUT_TAIL_CHARS = 4000


class EventStreamUI:
    """`ui.AgentUI` implementation that serializes agent output onto a channel.

    Every method maps to a `frontend_protocol` event and emits it; a remote
    frontend does the rendering and any verbosity filtering. Secret safety is
    provided by redaction (not terminal escaping) since the transport is data,
    not a terminal.
    """

    def __init__(self, channel: FrontendChannel) -> None:
        self._channel = channel
        self.verbose = False
        # The action loop streams no answer, so its reasoning has no stream of
        # its own: one is opened when the first thought arrives and sealed when
        # the model starts writing the action.
        self._loop_reasoning: tuple[str, float] | None = None
        self._loop_reasoning_reported = False
        # Live command output arrives from the reader threads of a child
        # process; the rolling tail is emitted at most once per interval.
        self._output_lock = threading.Lock()
        self._output_step = -1
        self._output_tail = ""
        self._output_last_emit = 0.0
        self._output_timer: threading.Timer | None = None
        self.output_interval = 0.12

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
            return
        if self._loop_reasoning is None:
            self._loop_reasoning = (uuid.uuid4().hex, time.monotonic())
            self._channel.emit(ReasoningStartEvent(self._loop_reasoning[0]))
        self._channel.emit(
            ReasoningChunkEvent(self._loop_reasoning[0], redact_text(text))
        )

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

    def print_plan(self, plan_info: Any) -> None:
        self._channel.emit(
            PlanEvent(
                plan_info.goal,
                plan_info.complexity,
                list(plan_info.plan),
                list(plan_info.success_criteria),
                list(plan_info.risks),
            )
        )

    def print_step_header(
        self, step: int, command: Any, reasoning_seconds: float = 0.0
    ) -> None:
        self.end_thinking()
        # The thought already went out as its own reasoning stream: a second
        # "thought for N seconds" on the step would show it twice.
        if self._loop_reasoning_reported:
            reasoning_seconds = 0.0
            self._loop_reasoning_reported = False
        self._channel.emit(
            StepEvent(
                step,
                command.plan_step,
                redact_text(command.thought),
                redact_text(command.user_message),
                str(command.action.type),
                redact_value(command.action.args),
                reasoning_seconds,
                redact_text(command.action.description),
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

    def print_action_output(self, step: int, text: str) -> None:
        """Feed the live output of a running command (called from reader threads)."""
        with self._output_lock:
            if step != self._output_step:
                self._reset_output(step)
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
            ActionOutputEvent(self._output_step, redact_text(self._output_tail))
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
            )
        )

    def print_route(self, role: str, source: str) -> None:
        self._channel.emit(RouteEvent(str(role), str(source)))

    @contextlib.contextmanager
    def live_markdown(self) -> Iterator[Callable[[str], None]]:
        stream_id = uuid.uuid4().hex
        self._channel.emit(StreamStartEvent(stream_id))
        last = ""
        reasoning_start: float | None = None
        reasoning_committed = False

        def commit_reasoning() -> None:
            nonlocal reasoning_committed
            if reasoning_start is None or reasoning_committed:
                return
            reasoning_committed = True
            duration = time.monotonic() - reasoning_start
            self._channel.emit(ReasoningCommitEvent(stream_id, duration))

        def reasoning(message: str) -> None:
            nonlocal reasoning_start
            if reasoning_committed:
                return
            if reasoning_start is None:
                reasoning_start = time.monotonic()
                self._channel.emit(ReasoningStartEvent(stream_id))
            self._channel.emit(ReasoningChunkEvent(stream_id, redact_text(message)))

        def update(message: str) -> None:
            nonlocal last
            # The first answer token means thinking is over: seal the reasoning
            # block so its text never leaks into the answer stream.
            commit_reasoning()
            last = message
            self._channel.emit(StreamChunkEvent(stream_id, redact_text(message)))

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
        self, final_message: str, user_messages: list[str] | None = None
    ) -> None:
        self._channel.emit(
            FinalEvent(
                redact_text(final_message),
                [redact_text(m) for m in (user_messages or [])],
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
