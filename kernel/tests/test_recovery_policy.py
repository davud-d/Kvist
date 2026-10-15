# -*- coding: utf-8 -*-
from __future__ import annotations

from kernel.tests.conftest import make_command

_DENIAL = {
    "ok": False,
    "action_type": "run_shell",
    "error": "Hard security denial: recursive ripgrep directory traversal is forbidden",
}


def _deny(agent, step: int) -> None:
    agent._register_feedback(
        step,
        make_command("run_shell", {"command": ["rg", "foo"]}, plan_step="inspect"),
        _DENIAL,
    )


def test_hard_denial_is_recorded_but_never_demands_a_workaround(agent_factory):
    agent = agent_factory()

    _deny(agent, 1)

    assert agent._state.recovery_pending
    assert any("ripgrep" in rule for rule in agent._state.hard_denial_rules)

    agent._refresh_completion_requirements(force_enforcement=True)
    assert "no_recovery" not in agent._state.completion_blocker_kinds
    assert not any("strateg" in b for b in agent._state.completion_blockers)


def test_two_distinct_strategies_clear_the_recovery_requirement(agent_factory):
    agent = agent_factory()

    _deny(agent, 1)

    agent._register_feedback(
        2,
        make_command("list_dir", {"path": "src"}),
        {"ok": True, "data": {"path": "src"}},
    )
    assert agent._state.recovery_pending
    assert len(agent._state.recovery_signatures) == 1

    agent._register_feedback(
        3,
        make_command("search_code", {"query": "foo", "path": "src"}),
        {"ok": True, "data": {"query": "foo", "path": "src"}},
    )
    assert not agent._state.recovery_pending
    agent._refresh_completion_requirements()
    assert not any("ripgrep" in b for b in agent._state.completion_blockers)


def test_repeating_one_strategy_does_not_satisfy_recovery(agent_factory):
    agent = agent_factory()

    _deny(agent, 1)
    for step in (2, 3, 4):
        agent._register_feedback(
            step,
            make_command("list_dir", {"path": "src"}),
            {"ok": True, "data": {"path": "src"}},
        )

    assert agent._state.recovery_pending
    assert len(agent._state.recovery_signatures) == 1


def test_new_denial_resets_the_strategy_counter(agent_factory):
    agent = agent_factory()

    _deny(agent, 1)
    agent._register_feedback(
        2,
        make_command("list_dir", {"path": "src"}),
        {"ok": True, "data": {"path": "src"}},
    )
    assert len(agent._state.recovery_signatures) == 1

    _deny(agent, 3)
    assert agent._state.recovery_signatures == set()
    assert agent._state.recovery_pending
