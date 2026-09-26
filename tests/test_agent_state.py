from __future__ import annotations

from agent_state import AgentConfig, PlanInfo, RunState


def test_agent_config_and_run_state_defaults_are_isolated():
    first = RunState()
    second = RunState()
    first.files_read.append("a.py")

    assert AgentConfig().max_steps == 150
    assert second.files_read == []
    assert first.current_agent_role == "coder"


def test_plan_info_preserves_ordered_plan_contract():
    plan = PlanInfo("goal", "complex", ["inspect", "verify"], ["done"], [])
    assert plan.plan == ["inspect", "verify"]
    assert plan.complexity == "complex"
