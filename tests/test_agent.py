from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_state import PlanInfo
from tests.conftest import FakeLLMClient


def test_local_agent_run_chat_path_uses_router_result(agent_factory, monkeypatch):
    agent = agent_factory(stream=False)
    agent.client = FakeLLMClient(content="Hello **world**")
    monkeypatch.setattr("agent_model._load_role_prompt", lambda role: "chat prompt")

    result = agent.run("hello", routed_role="chat")

    assert result == "Hello **world**"
    assert agent._state.current_agent_role == "chat"


def test_local_agent_run_coder_path_builds_plan_and_runtime(agent_factory, monkeypatch):
    agent = agent_factory()
    calls = {}

    agent._create_plan = lambda goal: PlanInfo(goal, "simple", ["inspect"], ["done"], [])
    def fake_run_loop(goal, start_step):
        calls["args"] = (goal, start_step)
        return "finished"

    agent._run_loop = fake_run_loop
    monkeypatch.setattr("agent.detect_dependency_versions", lambda workspace: {"pytest": "==8"})

    result = agent.run("edit file", routed_role="coder")

    assert result == "finished"
    assert calls["args"] == ("edit file", 1)
    assert agent._state.plan_info.plan == ["inspect"]
    assert agent._state.dependency_versions == {"pytest": "==8"}


def test_local_agent_resume_returns_none_without_checkpoint(agent_factory):
    assert agent_factory().resume() is None


def test_switch_session_saves_outgoing_and_restores_selected_session(agent_factory):
    agent = agent_factory()
    agent.new_session("alpha")
    agent._session_history = [
        {"goal": "alpha task", "role": "coder", "result": "alpha result"}
    ]
    agent._chat_history = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "alpha chat"},
    ]

    agent.new_session("beta")
    agent._session_history = [
        {"goal": "beta task", "role": "devops", "result": "beta result"}
    ]
    agent.save_session()

    agent.switch_session("alpha")

    assert agent.current_session == "alpha"
    assert agent._session_history[0]["goal"] == "alpha task"
    assert agent._chat_history[-1]["content"] == "alpha chat"

    restored = agent_factory()
    assert restored.current_session == "alpha"
    assert restored._session_history[0]["goal"] == "alpha task"


def test_switch_session_rejects_invalid_name_before_mutating_state(agent_factory):
    agent = agent_factory()

    with pytest.raises(ValueError, match="Invalid session name"):
        agent.switch_session("../outside")

    assert agent.current_session == "default"
