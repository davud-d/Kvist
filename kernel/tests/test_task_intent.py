from __future__ import annotations

from kernel.task_intent import asks_for_explanation, infer_task_kind, is_create_goal


def test_infer_task_kind_catches_verbless_report_phrases():
    for goal in (
        "какие места тут улучшить?",
        "что не так с этим конфигом",
        "твоё мнение по структуре модуля",
        "интересно, где тут узкие места",
        "what's wrong with this setup",
    ):
        assert infer_task_kind(goal) == "report", goal

    assert infer_task_kind("создай hello.py") == "change"
    assert infer_task_kind("исправь баг в main.py") == "change"
    assert infer_task_kind("исправь баг и объясни, что было не так") == "change"
    assert infer_task_kind("прогони тесты") == "action"


def test_create_goal_needs_a_word_that_asks_for_something_new():
    assert is_create_goal("create a parser")
    assert is_create_goal("создай hello.py")
    assert not is_create_goal("fix the parser")


def test_a_change_that_asks_for_an_explanation_owes_it():
    assert asks_for_explanation("исправь баг и объясни, что было не так")
    assert not asks_for_explanation("исправь баг в main.py")


def test_build_runs_a_build_and_asking_how_to_create_creates_nothing():
    assert not is_create_goal("build the project and run the tests")
    assert not is_create_goal("how to create a parser?")
    assert not is_create_goal("don't generate a new file, just show the diff")
    assert not is_create_goal("recreate the failure and report")
    assert is_create_goal("Generate a changelog for this release")


def test_a_report_marker_must_start_a_word():
    assert not asks_for_explanation("open the preview tab")
    assert asks_for_explanation("review this module")


def test_a_long_request_still_starts_on_the_fast_model(agent_factory):
    agent = agent_factory()
    agent._state.user_goal = "refactor " + "x" * 400

    assert agent._prefer_smart_model() is False


def test_stagnation_escalates_to_the_smart_model(agent_factory):
    agent = agent_factory()
    agent._state.stagnation_count = 1

    assert agent._prefer_smart_model() is True
