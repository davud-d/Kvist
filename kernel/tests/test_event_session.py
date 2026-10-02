# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json
import threading

import pytest

from kernel import event_stdio
from kernel.agent import LocalAgent
from kernel.agent_state import AgentConfig
from kernel.event_session import EventApprovalGate, EventSession
from kernel.event_stream_ui import EventStreamUI
from kernel.executor import SafeExecutor
from kernel.permission_policy import load_policy
from kernel.frontend_protocol import (
    ErrorEvent,
    FinalEvent,
    FrontendChannel,
    InputRequestEvent,
    MessageEvent,
    StreamCommitEvent,
)
from kernel.tests.conftest import FakeLLMClient

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


def test_event_approval_gate_remember_saves_allow_rule_once_the_command_ran(tmp_path):
    channel = FrontendChannel()
    gate = EventApprovalGate(channel, workspace=tmp_path)
    approved: dict[str, bool] = {}

    def confirm() -> None:
        approved["value"] = gate.confirm_command(["node", "tools/check.js"], "needs approval")

    worker = threading.Thread(target=confirm, daemon=True)
    worker.start()
    request = channel.get(timeout=2.0)
    assert isinstance(request, InputRequestEvent)
    channel.respond(request.request_id, approved=True, remember=True)
    worker.join(timeout=2.0)

    assert approved["value"] is True
    assert load_policy(tmp_path)[0].allow == ()

    gate.commit_remembered_rule()
    policy, note = load_policy(tmp_path)
    assert note == ""
    assert policy.allow == ("node tools/check.js",)

    gate.commit_remembered_rule()
    assert load_policy(tmp_path)[0].allow == ("node tools/check.js",)


def test_a_remembered_choice_does_not_leak_into_the_next_approval(tmp_path):
    channel = FrontendChannel()
    gate = EventApprovalGate(channel, workspace=tmp_path)

    def answer(**response) -> None:
        worker = threading.Thread(
            target=lambda: gate.confirm_command(["node", "tools/check.js"], "needs approval"), daemon=True
        )
        worker.start()
        request = channel.get(timeout=2.0)
        channel.respond(request.request_id, **response)
        worker.join(timeout=2.0)

    answer(approved=True, remember=True)
    answer(approved=True, remember=False)
    gate.commit_remembered_rule()

    assert load_policy(tmp_path)[0].allow == ()


def test_remembering_an_approval_does_not_invalidate_the_approved_run(tmp_path):
    # The rule used to be saved while the person answered, so the policy digest
    # bound to the approval no longer matched at spawn and the command was
    # refused with "approved execution context changed".
    from kernel.terminal import TerminalExecutor

    (tmp_path / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    channel = FrontendChannel()
    executor = TerminalExecutor(tmp_path, approvals=EventApprovalGate(channel, workspace=tmp_path))

    def approve_and_remember() -> None:
        request = channel.get(timeout=5.0)
        channel.respond(request.request_id, approved=True, remember=True)

    responder = threading.Thread(target=approve_and_remember, daemon=True)
    responder.start()
    result = executor.run(["sed", "-n", "1,2p", "notes.txt"])
    responder.join(timeout=5.0)

    assert result.denied is False
    assert result.stdout == "one\ntwo\n"
    assert load_policy(tmp_path)[0].allow == ("sed -n 1,2p notes.txt",)


def test_event_approval_gate_deny_mode_never_prompts():
    channel = FrontendChannel()
    gate = EventApprovalGate(channel, mode="deny")

    assert gate.confirm_command(["rm", "generated.tmp"], "destructive") is False
    assert gate.confirm("Overwrite file", "body", "continue?") is False
    assert channel.get(timeout=0.01) is None


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
    assert payloads[0] == {"type": "hello", "protocol_version": 1}
    assert payloads[-1]["type"] == "final"


if __name__ == "__main__":  # pragma: no cover - manual smoke helper
    pytest.main([__file__, "-q"])


class _ResumableAgent:
    """Minimal agent standing in for LocalAgent.resume()."""

    def __init__(self, result: str | None) -> None:
        self._result = result

    def resume(self) -> str | None:
        return self._result


def test_submit_resume_returns_agent_result():
    channel = FrontendChannel()
    session = EventSession(channel, _ResumableAgent("finished the task"))
    session.submit_resume()

    _drain(session)

    assert session.error is None
    assert session.result == "finished the task"


def test_submit_resume_without_checkpoint_emits_error_event():
    channel = FrontendChannel()
    session = EventSession(channel, _ResumableAgent(None))
    session.submit_resume()

    events = _drain(session)

    assert session.result is None
    assert any(
        isinstance(event, ErrorEvent) and "checkpoint" in event.message
        for event in events
    )
