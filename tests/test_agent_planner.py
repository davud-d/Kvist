from __future__ import annotations

import pytest


def test_create_plan_uses_model_json(agent_factory):
    agent = agent_factory()
    agent._call_model = lambda **kwargs: (
        '{"goal":"G","complexity":"complex","plan":["inspect"],'
        '"success_criteria":["done"],"risks":["risk"]}'
    )

    plan = agent._create_plan("goal")

    assert plan.goal == "G"
    assert plan.complexity == "complex"
    assert plan.plan == ["inspect"]


def test_create_plan_falls_back_and_resets_disabled_backends(agent_factory):
    agent = agent_factory()
    disabled = {"gemini"}
    agent.client._disabled_backends = disabled

    def fail(**kwargs):
        raise RuntimeError("planner failed")

    agent._call_model = fail
    plan = agent._create_plan("refactor multiple files in project")

    assert plan.complexity == "complex"
    assert disabled == set()


def test_parse_json_dict_requires_object(agent_factory):
    agent = agent_factory()
    with pytest.raises(ValueError, match="JSON object"):
        agent._parse_json_dict("[1, 2]")


def test_is_complex_goal_heuristic(agent_factory):
    agent = agent_factory()
    assert agent._is_complex_goal("small edit") is False
    assert agent._is_complex_goal("architect a production system") is True
