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
    # stdio-multi extras: the current reasoning-effort level and the command
    # names the core can execute.
    effort: str | None = None
    commands: list[str] | None = None
    # The running kvist's own version (version.kvist_version()), for a
    # frontend footer or a bug report — never part of the handshake check.
    kvist_version: str | None = None
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
        if self.commands is not None:
            payload["commands"] = self.commands
        if self.kvist_version is not None:
            payload["kvist_version"] = self.kvist_version
        return payload


@dataclass(slots=True)
class ManifestEvent:
    """UI-facing catalogue of roles, providers, effort levels, etc.

    Sent once right after the hello so a frontend can populate its pickers from
    the core rather than hardcoding its own copies.  The payload is built by
    ``core_manifest.build_manifest``.
    """

    manifest: dict
    type: ClassVar[str] = "manifest"

    def to_dict(self) -> dict:
        return {"type": self.type, **self.manifest}

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
    # Additive (protocol v1 unchanged): the call this step runs and the model
    # response it came from. ``thought`` is always "" since the v2 contract;
    # ``user_message`` carries the response's words on the first step of its
    # batch and "" on the others, so an older frontend shows them once.
    call_id: str = ""
    batch_id: str = ""
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
            "call_id": self.call_id,
            "batch_id": self.batch_id,
        }


@dataclass(slots=True)
class PlanEvent:
    """The plan the model proposes for the person's decision (plan mode)."""

    markdown: str
    type: ClassVar[str] = "plan"

    def to_dict(self) -> dict:
        return {"type": self.type, "markdown": self.markdown}


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
    # Additive: "done" when the run finished and passed its completion gates,
    # "incomplete" for every other ending (an honest "not done", a stop, the
    # step budget, a refusal).
    outcome: str = "done"
    # Additive: the closing words of end_session or a closed session's reply.
    # A frontend shows them as an ordinary message, with no status; the
    # outcome stays "incomplete" for frontends that do not know the field.
    closed: bool = False
    # Additive: the person paused the task; it continues with their next
    # message. The outcome stays "incomplete" for frontends that do not know it.
    paused: bool = False
    type: ClassVar[str] = "final"

    def to_dict(self) -> dict:
        payload = {
            "type": self.type,
            "message": self.message,
            "user_messages": self.user_messages,
            "outcome": self.outcome,
        }
        if self.closed:
            payload["closed"] = True
        if self.paused:
            payload["paused"] = True
        return payload


@dataclass(slots=True)
class TaskStateEvent:
    """The task's lifecycle state: running, pause_requested, paused, cancelling, cancelled, ...

    ``pause_requested`` means the loop has not reached a safe point yet; a
    frontend shows "pausing…" and never "paused" until ``paused`` arrives.
    """

    state: str
    reason: str = ""
    type: ClassVar[str] = "task_state"

    def to_dict(self) -> dict:
        return {"type": self.type, "state": self.state, "reason": self.reason}


@dataclass(slots=True)
class ScreenEvent:
    """The agent's virtual screen: live preview URLs on 127.0.0.1 carrying their access token.

    One screen, several views: ``stream_url`` (MJPEG) for a small preview,
    ``url`` for a browser tab. ``state``: starting, running, paused,
    unavailable, closed (URLs are empty once closed).
    """

    screen_id: str
    skill: str
    title: str
    state: str
    url: str = ""
    stream_url: str = ""
    frame_url: str = ""
    type: ClassVar[str] = "screen"

    def to_dict(self) -> dict:
        return {"type": self.type, "screen_id": self.screen_id, "skill": self.skill, "title": self.title,
                "state": self.state, "url": self.url, "stream_url": self.stream_url, "frame_url": self.frame_url}


@dataclass(slots=True)
class SkillEvent:
    """A skill was attached to or detached from the task."""

    skill: str
    version: str
    name: str
    state: str
    type: ClassVar[str] = "skill"

    def to_dict(self) -> dict:
        return {"type": self.type, "skill": self.skill, "version": self.version, "name": self.name,
                "state": self.state}


@dataclass(slots=True)
class HelperEvent:
    """A compact notice about a helper agent; its full activity is in its service ``session``."""

    agent_id: str
    event: str
    state: str
    session: str = ""
    task: str = ""
    summary: str = ""
    stopped_by: str | None = None
    type: ClassVar[str] = "helper"

    def to_dict(self) -> dict:
        payload = {"type": self.type, "agent_id": self.agent_id, "event": self.event, "state": self.state,
                   "session": self.session, "task": self.task, "summary": self.summary}
        if self.stopped_by:
            payload["stopped_by"] = self.stopped_by
        return payload


@dataclass(slots=True)
class HelperActivityEvent:
    """One call of a helper as it happens: ``phase`` is ``step`` when it starts, ``result`` when it ends.

    Live only, like ``action_output``: the helper's service session journals its
    full activity, so the main session's journal keeps just the ``helper`` notices.
    ``ok``, ``summary`` and ``output`` (the last lines, redacted) come with ``result``.
    """

    agent_id: str
    phase: str
    call_id: str
    action_type: str
    label: str = ""
    ok: bool | None = None
    summary: str = ""
    output: str = ""
    type: ClassVar[str] = "helper_activity"

    def to_dict(self) -> dict:
        return {"type": self.type, "agent_id": self.agent_id, "phase": self.phase, "call_id": self.call_id,
                "action_type": self.action_type, "label": self.label, "ok": self.ok,
                "summary": self.summary, "output": self.output}


@dataclass(slots=True)
class ArtifactEvent:
    """A file or image a skill produced for the person; ``preview`` is a small JPEG data URL (live only)."""

    name: str
    mime: str
    path: str
    caption: str = ""
    skill: str = ""
    preview: str = ""
    type: ClassVar[str] = "artifact"

    def to_dict(self) -> dict:
        payload = {"type": self.type, "name": self.name, "mime": self.mime, "path": self.path,
                   "caption": self.caption, "skill": self.skill}
        if self.preview:
            payload["preview"] = self.preview
        return payload


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
    # True means ``text`` is the next bytes to append to the live answer. Older
    # events omitted it and carried a cumulative snapshot in ``text``.
    append: bool = True
    type: ClassVar[str] = "stream_chunk"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "stream_id": self.stream_id,
            "text": self.text,
            "append": self.append,
        }



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
    # True means ``text`` is the next bytes to append to the live reasoning.
    # Older events omitted it and carried a cumulative snapshot in ``text``.
    append: bool = True
    type: ClassVar[str] = "reasoning_chunk"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "stream_id": self.stream_id,
            "text": self.text,
            "append": self.append,
        }


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
class ContextEvent:
    """Live size of the context window, from ``context_meter``.

    Emitted while a call streams (at most every ``REFRESH_SECONDS``) and at
    every point where the size changes at once: a request is sent, a call
    ends, a tool result or a new message is added. ``context_tokens`` is the
    sum of ``prompt``, ``generated`` and ``pending``; a frontend assigns it.
    """

    context_tokens: int
    context_limit: int
    prompt: int = 0
    generated: int = 0
    pending: int = 0
    type: ClassVar[str] = "context"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "context_tokens": self.context_tokens,
            "context_limit": self.context_limit,
            "prompt": self.prompt,
            "generated": self.generated,
            "pending": self.pending,
        }


@dataclass(slots=True)
class UsageEvent:
    """Token usage of one model call (real tiktoken counts, not estimates).

    Emitted after each completed model call so a frontend can accumulate run
    totals. ``kind`` names the call site ("step", "plan", "chat", "report",
    "model" when unspecified); ``step`` is the agent loop step (0 outside it).

    ``input_tokens``/``output_tokens`` are spend and are meant to be summed;
    ``context_tokens`` and ``context_limit`` are the core's current answer from
    ``context_meter.context_usage``: the size of the last conversation prompt
    and the budget the core trims it to. A frontend assigns them as they are.
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
    a frontend number the lines of a diff. ``lines_added``/``lines_removed`` are
    the write's line diff, for a frontend that shows per-file counts.
    ``patch`` is the write's unified diff (redacted, capped), for a frontend
    that shows what changed in a file.
    """

    step: int
    action_type: str
    ok: bool
    summary: str
    output: str = ""
    exit_code: int | None = None
    path: str | None = None
    first_line: int | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    patch: str | None = None
    # Additive: the call this result answers (see StepEvent).
    call_id: str = ""
    batch_id: str = ""
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
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "patch": self.patch,
            "call_id": self.call_id,
            "batch_id": self.batch_id,
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
    call_id: str = ""
    type: ClassVar[str] = "action_output"

    def to_dict(self) -> dict:
        return {"type": self.type, "step": self.step, "output": self.output, "call_id": self.call_id}


@dataclass(slots=True)
class JobStartedEvent:
    """A ``run_shell`` command with ``background: true`` began running.

    ``job_id`` identifies the job in every later ``job_*`` event and in the
    ``job_output``/``job_stop`` actions. ``call_id`` is the ``step`` call that
    started it, so a frontend can attach the job to that call's card.
    """

    job_id: str
    command: list[str]
    purpose: str = ""
    call_id: str = ""
    type: ClassVar[str] = "job_started"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "job_id": self.job_id,
            "command": list(self.command),
            "purpose": self.purpose,
            "call_id": self.call_id,
        }


@dataclass(slots=True)
class JobOutputEvent:
    """Live output of a background job: a redacted, capped snapshot of its tail.

    Not a delta, like ``action_output``: a frontend replaces what it shows.
    Live-only, never recorded in the session journal.
    """

    job_id: str
    output: str
    type: ClassVar[str] = "job_output"

    def to_dict(self) -> dict:
        return {"type": self.type, "job_id": self.job_id, "output": self.output}


@dataclass(slots=True)
class JobFinishedEvent:
    """A background job ended (exit, timeout, ``job_stop`` or the end of the turn).

    ``status`` is ``exited`` (``exit_code`` set), ``timed_out`` or ``stopped``.
    ``output`` is the redacted tail kept for the person, capped.
    """

    job_id: str
    status: str
    exit_code: int | None
    output: str = ""
    type: ClassVar[str] = "job_finished"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "job_id": self.job_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "output": self.output,
        }


@dataclass(slots=True)
class JobNoticeEvent:
    """What the runtime told the model about background jobs before a request.

    Recorded so the conversation can be reconstructed: ``text`` is exactly the
    redacted, capped note the model saw (new output and completions).
    """

    text: str
    job_ids: list[str]
    type: ClassVar[str] = "job_notice"

    def to_dict(self) -> dict:
        return {"type": self.type, "text": self.text, "job_ids": list(self.job_ids)}


@dataclass(slots=True)
class WakeEvent:
    """The core starts a turn nobody typed: helpers or jobs left running ended.

    Sent by a multi-turn transport whose frontend asked for wake-ups
    (``--wake``), before the turn's own events; the turn ends with
    ``turn_end`` like any other. No request text: the ``job_notice`` and
    ``helper`` events of the turn say what ended.
    """

    type: ClassVar[str] = "wake"

    def to_dict(self) -> dict:
        return {"type": self.type}


@dataclass(slots=True)
class SessionEvent:
    """The session a resumed run continues in, or the one a switch moved to.

    ``closed``/``closed_notice`` are present only for a session end_session
    closed: it is read-only, and the notice is the core's text for the place
    of the input box, in the session's language.
    """

    session: str
    has_checkpoint: bool
    closed_notice: str | None = None
    # The session's stored title when it has one, so a frontend can name its window.
    title: str | None = None
    type: ClassVar[str] = "session"

    def to_dict(self) -> dict:
        payload = {
            "type": self.type,
            "session": self.session,
            "has_checkpoint": self.has_checkpoint,
        }
        if self.closed_notice is not None:
            payload["closed"] = True
            payload["closed_notice"] = self.closed_notice
        if self.title:
            payload["title"] = self.title
        return payload


@dataclass(slots=True)
class SessionEndedEvent:
    """end_session closed the session for good (additive, protocol v1).

    ``reason`` is the model's one-line reason, recorded for the log; the
    person's closing words arrive as the turn's ``final``.
    """

    session_id: str
    warned_msg_id: int | None
    closed_msg_id: int
    reason: str
    closed_notice: str
    type: ClassVar[str] = "session_ended"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "warned_msg_id": self.warned_msg_id,
            "closed_msg_id": self.closed_msg_id,
            "reason": self.reason,
            "closed_notice": self.closed_notice,
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
class SessionTitleEvent:
    """A session's title became known: generated in the background from its
    first completed turn, or set by hand with /rename (session_titler.py,
    agent_logging.rename_session). ``session`` names the session the title
    belongs to, which may differ from the one currently active."""

    session: str
    title: str
    type: ClassVar[str] = "session_title"

    def to_dict(self) -> dict:
        return {"type": self.type, "session": self.session, "title": self.title}


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
# Inbound control messages besides cancel ("stop all"): pause the task at its
# next safe point, and stop one helper agent ({"type": "helper_stop", "agent_id"}).
PAUSE_MESSAGE_TYPE = "pause"
HELPER_STOP_MESSAGE_TYPE = "helper_stop"


def is_cancel_message(d: dict) -> bool:
    return d.get("type") == CANCEL_MESSAGE_TYPE


def is_pause_message(d: dict) -> bool:
    return d.get("type") == PAUSE_MESSAGE_TYPE


def is_helper_stop_message(d: dict) -> bool:
    return d.get("type") == HELPER_STOP_MESSAGE_TYPE and isinstance(d.get("agent_id"), str)


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

    def respond(
        self, request_id: str, *, value=None, approved=None, remember: bool = False
    ) -> None:
        with self._lock:
            waiter = self._waiters.get(request_id)
            if waiter is None:
                return
            waiter.response = InputResponse(
                request_id, value=value, approved=approved, remember=remember
            )
            waiter.event.set()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            waiters = list(self._waiters.values())
        for waiter in waiters:
            if waiter.response is None:
                waiter.response = InputResponse("", value=None, approved=None)
            waiter.event.set()
