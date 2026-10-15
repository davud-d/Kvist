# -*- coding: utf-8 -*-
"""How background jobs reach the model and end with the turn."""
from __future__ import annotations

import json

import pytest

from kernel import agent_model
from kernel.frontend_protocol import FrontendChannel, JobFinishedEvent, JobNoticeEvent
from kernel.event_stream_ui import EventStreamUI
from kernel.session_events import JournalingUI
from kernel.tests.test_background_jobs import _spawn_script, _spawn_until_cancelled, _wait_finished
from kernel.tests.test_native_tools import ScriptedClient, _call, _response


@pytest.fixture(autouse=True)
def _fresh_probe_cache(monkeypatch):
    # The native-tools probe is cached per (provider, model) for the process;
    # a stale "supported" entry would send later tests down the native path.
    monkeypatch.setattr(agent_model, "_NATIVE_TOOL_SUPPORT", {})
    monkeypatch.delenv("AGENT_NATIVE_TOOLS", raising=False)


def _agent(agent_factory, **overrides):
    agent = agent_factory(**overrides)
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"
    agent._state.response_language = "en"
    return agent


def test_json_protocol_prompt_carries_new_job_output_once(agent_factory):
    agent = _agent(agent_factory, max_steps=4)
    agent._state.native_tools = False
    job = agent.executor.jobs.launch(["srv"], "", "", _spawn_script(["listening on :8080\n"]))
    _wait_finished(job)
    prompts: list[str] = []

    def model(prompt: str) -> str:
        prompts.append(prompt)
        return "The server printed that it is listening."

    agent._call_action_model = model

    agent._run_loop("Is the server up?", start_step=1)

    assert "Background jobs:" in prompts[0]
    assert "listening on :8080" in prompts[0]
    assert "[job_1 · exited with code 0] srv" in prompts[0]


def test_native_conversation_gets_the_note_as_a_user_message(agent_factory):
    agent = _agent(agent_factory, max_steps=4, stream=False)
    agent.client = ScriptedClient([_response(content="Looks fine.")])
    job = agent.executor.jobs.launch(["srv"], "", "", _spawn_script(["compiled in 2s\n"]))
    _wait_finished(job)

    agent._run_loop("Check the build", start_step=1)

    request = [r for r in agent.client.requests if r["tools"][0]["function"]["name"] != "ping"][0]
    notes = [m for m in request["messages"] if m["role"] == "user" and "Background jobs:" in m["content"]]
    assert len(notes) == 1
    assert "compiled in 2s" in notes[0]["content"]


def test_a_job_still_running_is_stopped_when_the_turn_ends(agent_factory):
    agent = _agent(agent_factory, max_steps=4)
    agent._state.native_tools = False
    job = agent.executor.jobs.launch(["srv"], "", "", _spawn_until_cancelled("ready\n"))
    agent._call_action_model = lambda prompt: "Done."

    agent._run_loop("Say done", start_step=1)

    assert job.status == "stopped"
    assert agent.executor.jobs.running() == []
    assert agent.executor.jobs.listener is None


def test_job_events_are_journaled_but_live_output_is_not(agent_factory, tmp_path):
    from kernel.session_events import SessionJournal

    channel = FrontendChannel()
    live = EventStreamUI(channel)
    journal_path = tmp_path / ".kvist" / "sessions" / "t.events.jsonl"
    journal = SessionJournal(tmp_path, lambda: journal_path, print)
    ui = JournalingUI(live, journal)
    journal.begin_turn("start the server")

    ui.print_job_started("job_1", ["srv"], "dev server", "call_1")
    ui.print_job_output("job_1", "partial")
    ui.print_job_notice("Background jobs:\n[job_1 · running] srv", ["job_1"])
    ui.print_job_finished("job_1", "stopped", None, "partial")
    journal.end_turn()

    kinds = [json.loads(line)["event"]["type"] for line in journal_path.read_text().splitlines()]
    assert "job_started" in kinds
    assert "job_notice" in kinds
    assert "job_finished" in kinds
    assert "job_output" not in kinds
    assert any(isinstance(e, JobFinishedEvent) for e in channel.drain())
