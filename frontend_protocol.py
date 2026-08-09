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
    progress: int
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
            "progress": self.progress,
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
class DebugEvent:
    message: str
    type: ClassVar[str] = "debug"

    def to_dict(self) -> dict:
        return {"type": self.type, "message": self.message}


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
    type: ClassVar[str] = "input_request"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "request_id": self.request_id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "options": self.options,
            "current_value": self.current_value,
            "help_text": self.help_text,
        }


# --- Inbound response --------------------------------------------------------


@dataclass(slots=True)
class InputResponse:
    request_id: str
    value: str | None = None
    approved: bool | None = None

    @classmethod
    def from_dict(cls, d: dict) -> InputResponse:
        return cls(
            request_id=d.get("request_id", ""),
            value=d.get("value"),
            approved=d.get("approved"),
        )


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
