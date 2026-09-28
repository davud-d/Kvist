from __future__ import annotations

from task_intent import asks_for_explanation, infer_task_kind, is_complex_goal, is_create_goal


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
