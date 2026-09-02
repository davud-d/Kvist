# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

from frontend_protocol import (
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
    SandboxNoticeEvent,
    StatusEvent,
    StepEvent,
    StreamChunkEvent,
    StreamCommitEvent,
    StreamStartEvent,
    UsageEvent,
)
from redaction import redact_text, redact_value


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

    def start_live(self) -> None:
        self._channel.emit(LiveStartEvent())

    def stop_live(self) -> None:
        self._channel.emit(LiveStopEvent())

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
        self._channel.emit(
            StepEvent(
                step,
                command.plan_step,
                redact_text(command.thought),
                redact_text(command.user_message),
                str(command.action.type),
                redact_value(command.action.args),
                reasoning_seconds,
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


def _normalize_option(option: str | dict[str, str]) -> dict[str, str]:
    if isinstance(option, dict):
        return option
    return {"value": option, "label": option}
