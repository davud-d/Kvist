from __future__ import annotations

from kernel.task_intent import asks_for_explanation, infer_task_kind, is_complex_goal, is_create_goal


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


def test_is_complex_goal_heuristic():
    assert is_complex_goal("small edit") is False
    assert is_complex_goal("architect a production system") is True
    assert is_complex_goal("x" * 181) is True


def test_a_topic_noun_alone_does_not_start_a_run_on_the_smart_model():
    assert not is_complex_goal("what is my system date")
    assert not is_complex_goal("show the project name")
    assert is_complex_goal("рефактор модуля оплаты")
    assert is_complex_goal("change the config in multiple files")


def test_build_runs_a_build_and_asking_how_to_create_creates_nothing():
    assert not is_create_goal("build the project and run the tests")
    assert not is_create_goal("how to create a parser?")
    assert not is_create_goal("don't generate a new file, just show the diff")
    assert not is_create_goal("recreate the failure and report")
    assert is_create_goal("Generate a changelog for this release")


def test_a_report_marker_must_start_a_word():
    assert not asks_for_explanation("open the preview tab")
    assert asks_for_explanation("review this module")
