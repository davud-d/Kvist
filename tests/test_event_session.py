# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json
import threading

import pytest

import event_stdio
from agent import LocalAgent
from agent_state import AgentConfig
from event_session import EventApprovalGate, EventSession
from event_stream_ui import EventStreamUI
from executor import SafeExecutor
from frontend_protocol import (
    PROTOCOL_VERSION,
    FinalEvent,
    FrontendChannel,
    InputRequestEvent,
    MessageEvent,
    StreamCommitEvent,
)
from tests.conftest import FakeLLMClient

_TERMINAL_EVENTS = (FinalEvent, StreamCommitEvent, MessageEvent)


class _ChoiceAgent:
    """Minimal agent whose run blocks on a single choose_option request."""

    def __init__(self, ui: EventStreamUI) -> None:
        self._ui = ui
        self.used: str | None = None

    def run(self, user_goal: str, routed_role: str | None = None) -> str:
        _ = user_goal, routed_role
        value = self._ui.choose_option("Pick one", ["a", "b"])
        self.used = value
        self._ui.print_final(f"picked {value}")
        return value


class _FinalAgent:
    """Minimal read-only agent that emits one terminal event."""

    def __init__(self, ui: EventStreamUI) -> None:
        self._ui = ui

    def run(self, user_goal: str, routed_role: str | None = None) -> str:
        _ = user_goal, routed_role
        self._ui.print_final("done")
        return "done"


def _drain(session: EventSession, timeout: float = 5.0) -> list:
    """Collect every event, failing loudly if the stream does not end in time."""
    collected: list = []
    error: list[BaseException] = []

    def run() -> None:
        try:
            for event in session.events():
                collected.append(event)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
            error.append(exc)

    consumer = threading.Thread(target=run, daemon=True)
    consumer.start()
    consumer.join(timeout=timeout)
    assert not consumer.is_alive(), "event stream did not terminate"
    if error:
        raise error[0]
    return collected


def test_in_process_chat_run_streams_terminal_event(tmp_path):
    channel = FrontendChannel()
    ui = EventStreamUI(channel)
    agent = LocalAgent(
        FakeLLMClient(content="Hello **world**"),
        SafeExecutor(tmp_path),
        AgentConfig(persist_logs=False, api_request_interval=0, stream=False),
        ui=ui,
    )
    session = EventSession(channel, agent)
    session.submit("hello", routed_role="chat")

    events = _drain(session)

    assert events, "expected a non-empty event stream"
    assert any(isinstance(event, _TERMINAL_EVENTS) for event in events)
    assert session.error is None
    assert session.result == "Hello **world**"


def test_input_request_round_trip_delivers_value():
    channel = FrontendChannel()
    ui = EventStreamUI(channel)
    agent = _ChoiceAgent(ui)
    session = EventSession(channel, agent)
    session.submit("pick")

    events: list = []
    for event in session.events():
        events.append(event)
        if isinstance(event, InputRequestEvent):
            assert event.kind == "choice"
            session.respond(event.request_id, value="x")

    assert agent.used == "x"
    assert session.result == "x"
    assert any(isinstance(event, FinalEvent) for event in events)


def test_event_approval_gate_approves_and_fails_closed():
    channel = FrontendChannel()
    gate = EventApprovalGate(channel)

    approved: dict[str, bool] = {}

    def confirm() -> None:
        approved["value"] = gate.confirm_command(["rm", "-rf", "/"], "cleanup")

    worker = threading.Thread(target=confirm, daemon=True)
    worker.start()
    request = channel.get(timeout=2.0)
    assert isinstance(request, InputRequestEvent)
    assert request.kind == "confirm"
    channel.respond(request.request_id, approved=True)
    worker.join(timeout=2.0)
    assert approved["value"] is True

    # Closing the channel resolves the pending request as a denial.
    denied: dict[str, bool] = {}

    def confirm_denied() -> None:
        denied["value"] = gate.confirm_command(["rm", "-rf", "/"], "cleanup")

    worker2 = threading.Thread(target=confirm_denied, daemon=True)
    worker2.start()
    assert isinstance(channel.get(timeout=2.0), InputRequestEvent)
    channel.close()
    worker2.join(timeout=2.0)
    assert denied["value"] is False


def test_stdio_pump_writes_jsonl_ending_in_terminal_event():
    channel = FrontendChannel()
    ui = EventStreamUI(channel)
    session = EventSession(channel, _FinalAgent(ui))
    session.submit("read-only task")

    out = io.StringIO()
    event_stdio.pump(session, out, io.StringIO(""))

    lines = [line for line in out.getvalue().splitlines() if line]
    assert lines, "expected at least one serialized event line"
    payloads = [json.loads(line) for line in lines]
    for payload in payloads:
        assert "type" in payload
    # First line is always the hello handshake; last is the terminal event.
    assert payloads[0]["type"] == "hello"
    assert payloads[0]["protocol_version"] == PROTOCOL_VERSION
    assert payloads[-1]["type"] == "final"


if __name__ == "__main__":  # pragma: no cover - manual smoke helper
    pytest.main([__file__, "-q"])
