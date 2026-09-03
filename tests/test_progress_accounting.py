# -*- coding: utf-8 -*-
from __future__ import annotations

from tests.conftest import make_command, make_plan


def _full_step_line(agent) -> str:
    plan_len = len(agent._state.plan_info.plan)
    return f"{plan_len}/{plan_len}"


def test_errors_and_no_changes_seal_as_blocked(agent_factory):
    agent = agent_factory()
    agent._state.plan_info = make_plan()
    agent._state.modify_goal = True
    agent._state.task_language = "en"

    failing = make_command(
        "replace_in_file",
        {"path": "app.py", "old_text": "x", "new_text": "y"},
        plan_step="edit",
    )
    agent._register_feedback(
        1,
        failing,
        {"ok": False, "action_type": "replace_in_file", "error": "old_text fragment not found in file"},
    )

    agent._seal_run_status()

    assert agent._state.run_status == "blocked"
    assert agent._state.completion_blockers
    assert agent._state.error_history
    assert agent._state.files_changed == []
    assert agent._state.progress < 100


def test_real_changes_still_reach_progress_100(agent_factory):
    agent = agent_factory()
    agent._state.plan_info = make_plan()
    agent._state.create_goal = True
    agent._state.task_language = "en"

    agent._register_feedback(
        1,
        make_command("write_file", {"path": "app.py"}, plan_step="edit"),
        {"ok": True, "data": {"path": "app.py"}},
    )
    agent._register_feedback(
        2,
        make_command("run_shell", {"command": ["python", "-m", "pytest"]}, plan_step="verify"),
        {"ok": True, "data": {"command": ["python", "-m", "pytest"], "returncode": 0}},
    )
    agent._state.completed_plan_steps = set(make_plan().plan)
    agent._refresh_completion_requirements()

    assert agent._state.completion_blockers == []

    agent._register_final_answer(3, "done")
    agent._seal_run_status()

    assert agent._state.progress == 100
    assert agent._state.run_status == "completed"
    assert agent._state.files_changed == ["app.py"]


def test_summary_and_progress_do_not_diverge_when_blocked(agent_factory):
    agent = agent_factory()
    agent._state.plan_info = make_plan()
    agent._state.modify_goal = True
    agent._state.task_language = "en"
    # Loose matching marked every plan step done despite no real work.
    agent._state.completed_plan_steps = set(make_plan().plan)

    agent._register_feedback(
        1,
        make_command("replace_in_file", {"path": "app.py", "old_text": "x", "new_text": "y"}, plan_step="edit"),
        {"ok": False, "action_type": "replace_in_file", "error": "old_text fragment not found in file"},
    )
    agent._seal_run_status()

    summary = agent._state.summary
    assert "blocked" in summary.lower()
    assert f"0/{len(agent._state.plan_info.plan)}" in summary
    assert _full_step_line(agent) not in summary
    assert agent._state.progress < 100


def test_summary_and_progress_do_not_diverge_when_complete(agent_factory):
    agent = agent_factory()
    agent._state.plan_info = make_plan()
    agent._state.create_goal = True
    agent._state.task_language = "en"

    agent._register_feedback(
        1,
        make_command("write_file", {"path": "app.py"}, plan_step="edit"),
        {"ok": True, "data": {"path": "app.py"}},
    )
    agent._register_feedback(
        2,
        make_command("run_shell", {"command": ["python", "-m", "pytest"]}, plan_step="verify"),
        {"ok": True, "data": {"command": ["python", "-m", "pytest"], "returncode": 0}},
    )
    agent._state.completed_plan_steps = set(make_plan().plan)
    agent._refresh_completion_requirements()
    agent._register_final_answer(3, "done")
    agent._seal_run_status()

    summary = agent._state.summary
    assert "blocked" not in summary.lower()
    assert _full_step_line(agent) in summary
    assert agent._state.progress == 100
