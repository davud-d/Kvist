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


def test_infer_task_kind_catches_verbless_report_phrases():
    from agent_planner import infer_task_kind

    for goal in (
        "какие места тут улучшить?",
        "что не так с этим конфигом",
        "твоё мнение по структуре модуля",
        "интересно, где тут узкие места",
        "what's wrong with this setup",
    ):
        assert infer_task_kind(goal) == "report", goal

    assert infer_task_kind("создай hello.py") == "action"
    assert infer_task_kind("исправь баг в main.py") == "action"


def test_create_plan_reads_task_kind_from_planner(agent_factory):
    """The planner's own classification is authoritative over the heuristic."""
    agent = agent_factory()
    # Goal reads report-ish, but the planner labels it an action -> trust planner.
    agent._call_model = lambda **kwargs: (
        '{"goal":"G","complexity":"simple","task_kind":"action",'
        '"plan":["do"],"success_criteria":["done"],"risks":["r"]}'
    )

    plan = agent._create_plan("объясни и почини")

    assert plan.task_kind == "action"


def test_create_plan_infers_task_kind_when_planner_omits_it(agent_factory):
    agent = agent_factory()
    agent._call_model = lambda **kwargs: (
        '{"goal":"G","complexity":"simple",'
        '"plan":["do"],"success_criteria":["done"],"risks":["r"]}'
    )

    plan = agent._create_plan("что не так с этим конфигом")

    assert plan.task_kind == "report"


def test_fallback_plan_sets_task_kind(agent_factory):
    agent = agent_factory()

    def fail(**kwargs):
        raise RuntimeError("planner failed")

    agent._call_model = fail
    plan = agent._create_plan("что не так с этим конфигом")

    assert plan.task_kind == "report"


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
