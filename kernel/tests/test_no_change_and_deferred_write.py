# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from kernel.agent_runtime import _declares_no_change, _no_change_claim_unproven
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


def test_change_blocker_still_allows_running_the_reported_problem(agent_factory):
    agent = agent_factory()
    state = _start(agent, "в stock_ledger.py ошибка, исправь её")
    state.modify_goal = True

    agent._refresh_completion_requirements(force_enforcement=True)

    assert state.completion_blockers
    assert agent._required_followup_error(
        make_command("run_shell", {"command": ["python", "repro.py"]})
    ) is None


def test_no_change_claim_needs_a_read_and_a_clean_run(agent_factory):
    agent = agent_factory()
    state = _start(agent, "в stock_ledger.py ошибка, исправь её")
    state.modify_goal = True
    finish = make_command(
        "final_answer", {"message": "ошибка не воспроизводится", "outcome": "no_change_needed"}
    )

    assert _declares_no_change(finish)
    assert _no_change_claim_unproven(agent, finish)
    assert "no_change_needed" in state.pending_instruction

    state.files_read.append("stock_ledger.py")
    state.last_shell_returncode = 1
    assert _no_change_claim_unproven(agent, finish)

    state.last_shell_returncode = 0
    assert not _no_change_claim_unproven(agent, finish)
    agent._refresh_completion_requirements(force_enforcement=True)
    assert state.completion_blockers == []


def test_repeated_unproven_no_change_claim_becomes_an_honest_incomplete(agent_factory):
    agent = agent_factory()
    state = _start(agent, "в stock_ledger.py ошибка, исправь её")
    state.finish_refusals = 1
    state.response_language = "ru"
    finish = make_command(
        "final_answer", {"message": "ошибки нет", "outcome": "no_change_needed"}
    )

    assert not _no_change_claim_unproven(agent, finish)

    assert finish.action.args["outcome"] == "incomplete"
    assert "не подтверждено запуском" in finish.action.args["message"]
    assert not state.no_change_declared


def test_no_change_claim_is_refused_when_a_file_changed(agent_factory):
    agent = agent_factory()
    state = _start(agent, "исправь ошибку")
    state.files_read.append("a.py")
    state.last_shell_returncode = 0
    state.files_changed.append("a.py")
    finish = make_command("final_answer", {"message": "x", "outcome": "no_change_needed"})

    assert _no_change_claim_unproven(agent, finish)


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
