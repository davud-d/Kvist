from __future__ import annotations

from types import SimpleNamespace

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
