# -*- coding: utf-8 -*-
"""Work an answered turn left running, and the turn its end starts (``LocalAgent.wake``)."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from kernel.tests.test_background_jobs import _spawn_script, _spawn_until_cancelled, _wait_finished
from kernel.tests.conftest import NullUI


@pytest.fixture(autouse=True)
def _json_protocol(monkeypatch):
    monkeypatch.setenv("AGENT_NATIVE_TOOLS", "0")


class _Listener:
    def __init__(self) -> None:
        self.calls = 0
        self.called = threading.Event()

    def __call__(self) -> None:
        self.calls += 1
        self.called.set()


def _agent(agent_factory, prompts: list[str], *, listener: _Listener | None):
    agent = agent_factory(max_steps=4, stream=False)
    agent._state.native_tools = False
    agent._state.current_agent_role = "coder"
    agent._state.response_language = "ru"
    agent._last_response_language = "ru"
    agent._call_action_model = lambda prompt: prompts.append(prompt) or "Готово."
    agent.wake_listener = listener
    return agent


def _answer_with_a_job_running(agent, spawn):
    """One answered turn during which a background job was started."""
    agent.control.begin()
    job = agent.executor.jobs.launch(["make"], "build", "call_1", spawn)
    agent._run_loop("собери проект в фоне", start_step=1)
    return job


def test_an_answered_turn_leaves_the_job_running_and_its_end_wakes_the_agent(agent_factory):
    prompts: list[str] = []
    listener = _Listener()
    agent = _agent(agent_factory, prompts, listener=listener)
    gate = threading.Event()

    job = _answer_with_a_job_running(agent, _spawn_script(["built in 3s\n"], gate=gate))
    assert job.status == "running"
    assert listener.calls == 0

    gate.set()
    _wait_finished(job)
    assert listener.called.wait(5)
    assert agent.wake_due()

    ledger_before = list(agent._message_ledger["user_ids"]) if getattr(agent, "_message_ledger", None) else []
    agent.wake()

    assert "built in 3s" in prompts[-1]
    assert "[job_1 · exited with code 0] make" in prompts[-1]
    # Not the person's message, and the person's language stays.
    assert agent._message_ledger["user_ids"] == ledger_before
    assert agent._state.response_language == "ru"
    assert agent._state.current_agent_role == "coder"
    assert not agent.wake_due()


def test_without_a_listener_the_answer_stops_the_job_as_before(agent_factory):
    agent = _agent(agent_factory, [], listener=None)

    job = _answer_with_a_job_running(agent, _spawn_until_cancelled("ready\n"))

    assert job.status == "stopped"
    assert not agent.wake_due()


def test_a_stopped_job_wakes_nobody(agent_factory):
    listener = _Listener()
    agent = _agent(agent_factory, [], listener=listener)
    job = _answer_with_a_job_running(agent, _spawn_until_cancelled("ready\n"))

    agent.stop_left_running()

    assert job.status == "stopped"
    assert listener.calls == 0
    assert not agent.wake_due()


def test_a_new_session_stops_what_the_old_one_left_running(agent_factory):
    listener = _Listener()
    agent = _agent(agent_factory, [], listener=listener)
    job = _answer_with_a_job_running(agent, _spawn_until_cancelled("ready\n"))

    agent.new_session()

    assert job.status == "stopped"
    assert listener.calls == 0


def test_the_start_note_says_what_the_answer_does_to_the_job(agent_factory):
    from kernel.executor import _background_note

    assert "keeps running" in _background_note(60, True, True)
    assert "ended when you give your final answer" in _background_note(60, True, False)


def test_a_helper_that_finishes_after_the_answer_wakes_the_agent(agent_factory):
    listener = _Listener()
    agent = _agent(agent_factory, [], listener=listener)
    gate = threading.Event()
    replies = ["The config lives in settings.py."]

    def create(**kwargs):
        gate.wait(5)
        text = replies.pop(0) if replies else "Nothing more."
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=None)

    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    object.__setattr__(agent._ui, "_ui", NullUI())
    agent.control.begin()
    started = agent.helpers.start("find the config", role="reader", context="", attachments=[], started_by="main")
    agent._run_loop("найди конфиг с помощником", start_step=1)
    helper = agent.helpers.get(started["agent_id"])
    assert helper.state == "running"
    assert "keeps working" in started["note"]

    gate.set()
    assert listener.called.wait(10)
    assert helper.state == "done"
    assert agent.wake_due()
