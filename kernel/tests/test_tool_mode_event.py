# -*- coding: utf-8 -*-
"""The ``tool_mode`` event: how calls travel in a run, in the live stream and in the session journal."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kernel.agent import agent_model
from kernel.protocol.event_stream_ui import EventStreamUI
from kernel.protocol.frontend_protocol import FrontendChannel
from kernel.session.session_store import run_session_op
from kernel.tests.test_native_tools import _call, _response


@pytest.fixture(autouse=True)
def _fresh_probe_cache(monkeypatch):
    monkeypatch.setattr(agent_model, "_NATIVE_TOOL_SUPPORT", {})
    monkeypatch.delenv("AGENT_NATIVE_TOOLS", raising=False)


class _Provider:
    """One scripted outcome per probe (an exception, True, or prose), then native turns."""

    def __init__(self, probes: list, turns: list | None = None):
        self.probes = list(probes)
        self.turns = list(turns or [])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        if kwargs["tools"][0]["function"]["name"] == "ping":
            outcome = self.probes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            if outcome is True:
                return _response(calls=[_call("probe", "ping", ok=True)])
            return _response(content="I would rather answer in words.")
        outcome = self.turns.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _final_native(message: str = "Done."):
    return _response("", [_call("c1", "final_answer", message=message, outcome="incomplete")])


def _text_final(message: str = "Done in text.") -> str:
    return json.dumps(
        {"thought": "t", "plan_step": "done", "user_message": "m",
         "actions": [{"type": "final_answer", "args": {"message": message, "outcome": "incomplete"}}]}
    )


def _text_read(path: str) -> str:
    return json.dumps(
        {"thought": "t", "plan_step": "inspect", "user_message": "m",
         "actions": [{"type": "read_file", "args": {"path": path}}]}
    )


def _run(agent_factory, provider, text_answers=()):
    agent = agent_factory(max_steps=10, stream=False)
    agent.client = provider
    answers = list(text_answers)
    agent._call_action_model = lambda prompt: answers.pop(0)
    (agent.executor.workspace / "a.py").write_text("A = 1\n", encoding="utf-8")
    agent.run("read a.py and report", routed_role="coder")
    return agent


def _modes(agent) -> list[tuple[str, str]]:
    page = run_session_op(agent.executor.workspace, "history", agent.current_session)
    return [
        (entry["event"]["mode"], entry["event"]["reason"])
        for entry in page["events"]
        if entry["event"]["type"] == "tool_mode"
    ]


# ── one reason each ────────────────────────────────────────────────────────


def test_probe_ok_is_journaled_as_native(agent_factory):
    agent = _run(agent_factory, _Provider([True], [_final_native()]))

    assert _modes(agent) == [("native", "probe_ok")]


def test_an_answer_without_a_call_is_journaled_as_text_probe_no_tool_call(agent_factory):
    agent = _run(agent_factory, _Provider(["prose"]), [_text_final()])

    assert _modes(agent) == [("text", "probe_no_tool_call")]


def test_a_probe_with_no_answer_is_journaled_as_text_probe_failed(agent_factory):
    agent = _run(agent_factory, _Provider([TimeoutError("Request timed out.")]), [_text_final()])

    assert _modes(agent) == [("text", "probe_failed")]


def test_a_probe_the_provider_refused_is_journaled_as_provider_rejected(agent_factory):
    refusal = RuntimeError("Error code: 400 - tools is not supported for this model")
    agent = _run(agent_factory, _Provider([refusal]), [_text_final()])

    assert _modes(agent) == [("text", "provider_rejected")]


def test_tools_refused_in_the_middle_of_a_run_are_journaled_as_provider_rejected(agent_factory):
    provider = _Provider([True], [RuntimeError("Error code: 400 - tools is not supported for this model")])
    agent = _run(agent_factory, provider, [_text_final()])

    assert _modes(agent) == [("native", "probe_ok"), ("text", "provider_rejected")]


def test_the_environment_switch_is_journaled_as_forced_by_env(agent_factory, monkeypatch):
    monkeypatch.setenv("AGENT_NATIVE_TOOLS", "0")
    agent = _run(agent_factory, _Provider([]), [_text_final()])

    assert _modes(agent) == [("text", "forced_by_env")]


# ── a switch inside the run ────────────────────────────────────────────────


def test_a_run_that_starts_on_text_and_moves_to_native_sends_both(agent_factory):
    provider = _Provider([TimeoutError("timed out"), True], [_final_native()])
    agent = _run(agent_factory, provider, [_text_read("a.py")])

    assert _modes(agent) == [("text", "probe_failed"), ("native", "probe_ok")]


def test_repeated_lost_probes_announce_the_text_mode_once(agent_factory):
    provider = _Provider([TimeoutError("timed out")] * 3)
    agent = _run(
        agent_factory, provider, [_text_read("a.py"), _text_read("a.py"), _text_read("a.py"), _text_final()]
    )

    assert _modes(agent) == [("text", "probe_failed")]


def test_a_new_run_announces_its_mode_again_even_when_the_probe_is_remembered(agent_factory):
    first = _run(agent_factory, _Provider([True], [_final_native()]))
    assert _modes(first) == [("native", "probe_ok")]

    # Same workspace, same session: the journal now holds both runs.
    second = _run(agent_factory, _Provider([], [_final_native()]))
    assert _modes(second) == [("native", "probe_ok")] * 2


# ── the live stream ────────────────────────────────────────────────────────


def test_the_live_stream_carries_the_event_as_plain_json():
    channel = FrontendChannel()

    EventStreamUI(channel).print_tool_mode("text", "probe_failed")

    assert [event.to_dict() for event in channel.drain()] == [
        {"type": "tool_mode", "mode": "text", "reason": "probe_failed"}
    ]
