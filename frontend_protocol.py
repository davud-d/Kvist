# -*- coding: utf-8 -*-
from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass
from typing import ClassVar


PROTOCOL_VERSION = 1


# --- Out events (core -> frontend) -------------------------------------------
# Pure data. No imports of agent/rich/redaction modules: redaction of action
# args happens in the transport adapter, not here.


@dataclass(slots=True)
class HelloEvent:
    protocol_version: int = PROTOCOL_VERSION
    provider: str | None = None
    models: dict[str, str] | None = None
    role: str | None = None
    approval_mode: str | None = None
    # Optional additive fields (still protocol v1: frontends ignore unknown
    # fields): the active named session and whether it holds a resumable
    # checkpoint. Session lifecycle operations themselves go through the
    # separate --session-op mode, not this stream.
    session: str | None = None
    has_checkpoint: bool | None = None
    # stdio-multi extras: the current reasoning-effort level, whether the
    # planning stage is on, and the command names the core can execute.
    effort: str | None = None
    planning: bool | None = None
    commands: list[str] | None = None
    type: ClassVar[str] = "hello"

    def to_dict(self) -> dict:
        payload = {
            "type": self.type,
            "protocol_version": self.protocol_version,
        }
        if self.provider is not None:
            payload["provider"] = self.provider
        if self.models is not None:
            payload["models"] = self.models
        if self.role is not None:
            payload["role"] = self.role
        if self.approval_mode is not None:
            payload["approval_mode"] = self.approval_mode
        if self.session is not None:
            payload["session"] = self.session
        if self.has_checkpoint is not None:
            payload["has_checkpoint"] = self.has_checkpoint
        if self.effort is not None:
            payload["effort"] = self.effort
        if self.planning is not None:
            payload["planning"] = self.planning
        if self.commands is not None:
            payload["commands"] = self.commands
        return payload


@dataclass(slots=True)
class LiveStartEvent:
    type: ClassVar[str] = "live_start"

    def to_dict(self) -> dict:
        return {"type": self.type}


@dataclass(slots=True)
class LiveStopEvent:
    type: ClassVar[str] = "live_stop"

    def to_dict(self) -> dict:
        return {"type": self.type}


@dataclass(slots=True)
class StatusEvent:
    user_message: str
    status: str
    step: int
    progress: int
    plan_step: str
    is_error: bool
    type: ClassVar[str] = "status"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "user_message": self.user_message,
            "status": self.status,
            "step": self.step,
            "progress": self.progress,
            "plan_step": self.plan_step,
            "is_error": self.is_error,
        }


@dataclass(slots=True)
class StepEvent:
    step: int
    plan_step: str
    thought: str
    user_message: str
    action_type: str
    action_args: dict
    reasoning_seconds: float = 0.0
    action_description: str = ""
    type: ClassVar[str] = "step"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "step": self.step,
            "plan_step": self.plan_step,
            "thought": self.thought,
            "user_message": self.user_message,
            "action_type": self.action_type,
            "action_args": self.action_args,
            "reasoning_seconds": self.reasoning_seconds,
            "action_description": self.action_description,
        }


@dataclass(slots=True)
class PlanEvent:
    goal: str
    complexity: str
    plan: list[str]
    success_criteria: list[str]
    risks: list[str]
    type: ClassVar[str] = "plan"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "goal": self.goal,
            "complexity": self.complexity,
            "plan": self.plan,
            "success_criteria": self.success_criteria,
            "risks": self.risks,
        }


@dataclass(slots=True)
class SandboxNoticeEvent:
    note: str
    type: ClassVar[str] = "sandbox_notice"

    def to_dict(self) -> dict:
        return {"type": self.type, "note": self.note}


@dataclass(slots=True)
class FinalEvent:
    message: str
    user_messages: list[str]
    type: ClassVar[str] = "final"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "message": self.message,
            "user_messages": self.user_messages,
        }


@dataclass(slots=True)
class MessageEvent:
    markdown: str
    type: ClassVar[str] = "message"

    def to_dict(self) -> dict:
        return {"type": self.type, "markdown": self.markdown}


@dataclass(slots=True)
class StreamStartEvent:
    stream_id: str
    type: ClassVar[str] = "stream_start"

    def to_dict(self) -> dict:
        return {"type": self.type, "stream_id": self.stream_id}


@dataclass(slots=True)
class StreamChunkEvent:
    stream_id: str
    text: str
    type: ClassVar[str] = "stream_chunk"

    def to_dict(self) -> dict:
        return {"type": self.type, "stream_id": self.stream_id, "text": self.text}


@dataclass(slots=True)
class StreamCommitEvent:
    stream_id: str
    text: str
    type: ClassVar[str] = "stream_commit"

    def to_dict(self) -> dict:
        return {"type": self.type, "stream_id": self.stream_id, "text": self.text}


@dataclass(slots=True)
class ReasoningStartEvent:
    # Shares the answer's stream_id so a frontend can attach the thinking block
    # to the reply it precedes. Emitted on the first reasoning token, which is
    # how a frontend learns dynamically that the model is a thinking model.
    stream_id: str
    type: ClassVar[str] = "reasoning_start"

    def to_dict(self) -> dict:
        return {"type": self.type, "stream_id": self.stream_id}


@dataclass(slots=True)
class ReasoningChunkEvent:
    stream_id: str
    text: str
    type: ClassVar[str] = "reasoning_chunk"

    def to_dict(self) -> dict:
        return {"type": self.type, "stream_id": self.stream_id, "text": self.text}


@dataclass(slots=True)
class ReasoningCommitEvent:
    stream_id: str
    duration_seconds: float
    type: ClassVar[str] = "reasoning_commit"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "stream_id": self.stream_id,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(slots=True)
class DebugEvent:
    message: str
    type: ClassVar[str] = "debug"

    def to_dict(self) -> dict:
        return {"type": self.type, "message": self.message}


@dataclass(slots=True)
class UsageEvent:
    """Token usage of one model call (real tiktoken counts, not estimates).

    Emitted after each completed model call so a frontend can accumulate run
    totals. ``kind`` names the call site ("step", "plan", "chat", "report",
    "model" when unspecified); ``step`` is the agent loop step (0 outside it).

    ``input_tokens``/``output_tokens`` are spend and are meant to be summed;
    ``context_tokens`` is the size of this one prompt and is meant to *replace*
    the previous value — it is 0 for calls that do not carry the conversation
    (plan, report), where a frontend keeps the last known figure.
    ``context_limit`` is the budget the core trims that prompt to.
    """

    input_tokens: int
    output_tokens: int
    model: str = ""
    kind: str = ""
    step: int = 0
    context_tokens: int = 0
    context_limit: int = 0
    type: ClassVar[str] = "usage"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "model": self.model,
            "kind": self.kind,
            "step": self.step,
            "context_tokens": self.context_tokens,
            "context_limit": self.context_limit,
        }


@dataclass(slots=True)
class ActionResultEvent:
    """Outcome of one executed action, so a frontend can show what it did.

    ``summary`` is one short line; ``output`` is the (redacted, size-capped)
    command output or error text. ``exit_code`` is set for shell-like actions
    only. ``path``/``first_line`` are set for file writes: ``first_line`` is the
    1-based line of the resulting file where the written text begins, which lets
    a frontend number the lines of a diff.
    """

    step: int
    action_type: str
    ok: bool
    summary: str
    output: str = ""
    exit_code: int | None = None
    path: str | None = None
    first_line: int | None = None
    type: ClassVar[str] = "action_result"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "step": self.step,
            "action_type": self.action_type,
            "ok": self.ok,
            "summary": self.summary,
            "output": self.output,
            "exit_code": self.exit_code,
            "path": self.path,
            "first_line": self.first_line,
        }


@dataclass(slots=True)
class ActionOutputEvent:
    """Live output of a running shell command, before its ``action_result``.

    ``output`` is a rolling snapshot of the most recent output (redacted and
    capped), not a delta, so a frontend replaces what it shows. The final,
    complete output still arrives with the ``action_result``.
    """

    step: int
    output: str
    type: ClassVar[str] = "action_output"

    def to_dict(self) -> dict:
        return {"type": self.type, "step": self.step, "output": self.output}


@dataclass(slots=True)
class SessionEvent:
    """The session a resumed run continues in (may differ from the hello's)."""

    session: str
    has_checkpoint: bool
    type: ClassVar[str] = "session"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "session": self.session,
            "has_checkpoint": self.has_checkpoint,
        }


@dataclass(slots=True)
class RouteEvent:
    """The role a turn actually runs under, emitted once per turn.

    ``source`` says who decided: "explicit" (the caller passed the role),
    "direct"/"llm"/"keyword_fallback"/"default" (the router), or "resume"
    (a bare "continue" inherited the stopped run's role).
    """

    role: str
    source: str
    type: ClassVar[str] = "route"

    def to_dict(self) -> dict:
        return {"type": self.type, "role": self.role, "source": self.source}


@dataclass(slots=True)
class ErrorEvent:
    message: str
    type: ClassVar[str] = "error"

    def to_dict(self) -> dict:
        return {"type": self.type, "message": self.message}


@dataclass(slots=True)
class InputRequestEvent:
    request_id: str
    kind: str  # one of "choice", "text", "secret", "confirm"
    title: str
    body: str = ""
    options: list[dict] | None = None
    current_value: str = ""
    help_text: str = ""
    subject: dict | None = None  # {kind: "command", command, reason} or {kind: "overwrite", path, diff}
    remember_pattern: str | None = None  # non-null = user may choose "don't ask again for this pattern"
    type: ClassVar[str] = "input_request"

    def to_dict(self) -> dict:
        payload = {
            "type": self.type,
            "request_id": self.request_id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "options": self.options,
            "current_value": self.current_value,
            "help_text": self.help_text,
        }
        if self.subject is not None:
            payload["subject"] = self.subject
        if self.remember_pattern is not None:
            payload["remember_pattern"] = self.remember_pattern
        return payload


# --- Inbound response --------------------------------------------------------


@dataclass(slots=True)
class InputResponse:
    request_id: str
    value: str | None = None
    approved: bool | None = None
    remember: bool = False  # true = save this approval pattern to project permissions

    @classmethod
    def from_dict(cls, d: dict) -> InputResponse:
        return cls(
            request_id=d.get("request_id", ""),
            value=d.get("value"),
            approved=d.get("approved"),
            remember=d.get("remember", False),
        )


# The one inbound control message that is not an InputResponse: it asks the
# active run to stop and carries no fields. A plain InputResponse never has a
# "type" key, so the transport routes on that discriminator alone and every
# pre-existing frontend keeps parsing unchanged.
CANCEL_MESSAGE_TYPE = "cancel"


def is_cancel_message(d: dict) -> bool:
    return d.get("type") == CANCEL_MESSAGE_TYPE


# --- Channel -----------------------------------------------------------------


@dataclass(slots=True)
class _Waiter:
    event: threading.Event
    response: InputResponse | None = None


class FrontendChannel:
    """Thread-safe bridge between the agent worker thread and a frontend transport.

    Out-events are produced by the worker via `emit` and consumed by a transport
    thread via `drain`/`get`. Input requests originate on the worker thread
    (`request`, which blocks) and are fulfilled from the transport thread
    (`respond`). `close` unblocks every pending waiter so a shutdown never hangs.
    """

    def __init__(self) -> None:
        self._out: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._waiters: dict[str, _Waiter] = {}
        self._closed = False

    def emit(self, event) -> None:
        self._out.put(event)

    def drain(self) -> list:
        events: list = []
        while True:
            try:
                events.append(self._out.get_nowait())
            except queue.Empty:
                break
        return events

    def get(self, timeout=None):
        try:
            return self._out.get(timeout=timeout)
        except queue.Empty:
            return None

    def request(
        self,
        kind: str,
        title: str,
        *,
        body: str = "",
        options: list[dict] | None = None,
        current_value: str = "",
        help_text: str = "",
        subject: dict | None = None,
        remember_pattern: str | None = None,
    ) -> InputResponse:
        request_id = uuid.uuid4().hex
        waiter = _Waiter(event=threading.Event())
        with self._lock:
            if self._closed:
                return InputResponse(request_id, value=None, approved=None)
            self._waiters[request_id] = waiter

        self.emit(
            InputRequestEvent(
                request_id=request_id,
                kind=kind,
                title=title,
                body=body,
                options=options,
                current_value=current_value,
                help_text=help_text,
                subject=subject,
                remember_pattern=remember_pattern,
            )
        )

        waiter.event.wait()
        with self._lock:
            self._waiters.pop(request_id, None)
        if waiter.response is None:
            return InputResponse(request_id, value=None, approved=None)
        return waiter.response

    def respond(self, request_id: str, *, value=None, approved=None) -> None:
        with self._lock:
            waiter = self._waiters.get(request_id)
            if waiter is None:
                return
            waiter.response = InputResponse(request_id, value=value, approved=approved)
            waiter.event.set()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            waiters = list(self._waiters.values())
        for waiter in waiters:
            if waiter.response is None:
                waiter.response = InputResponse("", value=None, approved=None)
            waiter.event.set()
