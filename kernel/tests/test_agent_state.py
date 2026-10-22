from __future__ import annotations

from kernel.agent.agent_state import AgentConfig, RunState


def test_agent_config_and_run_state_defaults_are_isolated():
    first = RunState()
    second = RunState()
    first.files_read.append("a.py")

    assert AgentConfig().max_steps == 150
    assert second.files_read == []
    assert first.current_agent_role == "coder"
    assert first.plan_phase == "" and first.approved_plan == ""
