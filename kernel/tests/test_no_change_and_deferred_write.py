# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from kernel.role_router import has_deferred_write_marker
from kernel.tests.conftest import make_command

DEFERRED_GOAL = (
    "в файле stock_ledger.py есть логическая ошибка. задача: 4. запусти воспроизведение "
    "и покажи вывод. 5. предложи исправление и проверь его. "
    "правила: файлы не меняй, пока не закончишь пункты 1-4."
)


@pytest.mark.parametrize(
    "text",
    [
        "файлы не меняй, пока не закончишь пункты 1-4",
        "не трогай код до того, как я скажу",
        "do not modify anything until the analysis is done",
        "don't change files before you have a reproduction",
    ],
)
def test_deferred_write_marker_detected(text):
    assert has_deferred_write_marker(text)


@pytest.mark.parametrize(
    "text",
    [
        "почини баг, но не меняй тесты",
        "прочитай конфиг, но ничего не меняй",
        "fix the bug without modifying the tests",
        "не меняй API. пока не поздно, добавь логи",
    ],
)
def test_plain_scope_is_not_a_deferral(text):
    assert not has_deferred_write_marker(text)


def _start(agent, goal):
    state = agent._state
    state.user_goal = goal
    state.create_goal = False
    state.modify_goal = False
    return state


def test_deferred_write_goal_is_not_held_to_a_change(agent_factory):
    agent = agent_factory()
    state = _start(agent, DEFERRED_GOAL)

    agent._refresh_completion_requirements(force_enforcement=True)

    assert state.completion_blockers == []
    assert state.required_next_actions == []


def test_last_shell_returncode_follows_the_latest_run(agent_factory):
    agent = agent_factory()
    command = make_command("run_shell", {"command": ["python", "repro.py"]})

    agent._register_feedback(
        1, command, {"ok": False, "action_type": "run_shell", "data": {"returncode": 1, "stdout": "", "stderr": "boom"}}
    )
    assert agent._state.last_shell_returncode == 1

    agent._register_feedback(
        2, command, {"ok": True, "action_type": "run_shell", "data": {"returncode": 0, "stdout": "ok", "stderr": ""}}
    )
    assert agent._state.last_shell_returncode == 0

    agent._register_feedback(3, command, {"ok": False, "action_type": "run_shell", "data": {"denied": True}})
    assert agent._state.last_shell_returncode == 0
