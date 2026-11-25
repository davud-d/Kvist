# -*- coding: utf-8 -*-
from __future__ import annotations

from kernel.agent.agent_model import _answer_repeats_opening

ANSWER = "tkinter работает: Tk 8.6 лежит в ~/.local/tk, приложение запущено на виртуальном экране."


def test_an_answer_written_twice_is_shown_once():
    opening = f"{ANSWER}\n\nБез root поставила так: скачала пакеты."
    answer = f"{ANSWER}\n\nБез root поставила так."
    assert _answer_repeats_opening(opening, answer)


def test_the_answer_still_being_written_already_counts_as_a_repeat():
    assert _answer_repeats_opening(f"{ANSWER}\n\nДалее.", ANSWER)


def test_a_short_shared_line_is_not_a_repeat():
    assert not _answer_repeats_opening("Готово.\n\nДетали ниже.", "Готово.")


def test_different_words_before_the_answer_are_kept():
    assert not _answer_repeats_opening("Проверяю результат.", ANSWER)
