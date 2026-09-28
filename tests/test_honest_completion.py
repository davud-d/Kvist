# -*- coding: utf-8 -*-
"""The runtime may refuse an unbacked success claim, but it never claims
success itself, never refuses a truthful "not done", and never blocks the
model from verifying."""
from __future__ import annotations

import json

import pytest

from parser import CommandValidationError, validate_action_args
from tests.conftest import make_command

_SYNTHETIC_SUCCESS = (
    "all checks passed",
    "verification confirmed",
    "все проверки пройдены",
    "проверка пройдена",
)


def _step(action_type: str, args: dict, plan_step: str = "inspect") -> dict:
    return {
        "thought": "working",
        "plan_step": plan_step,
        "user_message": "Working on the task",
        "actions": [{"type": action_type, "args": args}],
    }


def _script(agent, responses: list[dict]) -> list[str]:
    """Feed responses in order, repeating the last one; return the prompts seen."""
    prompts: list[str] = []

    def fake_call_action_model(prompt: str) -> str:
        prompts.append(prompt)
        index = min(len(prompts) - 1, len(responses) - 1)
        return json.dumps(responses[index], ensure_ascii=False)

    agent._call_action_model = fake_call_action_model
    return prompts


def _planned_agent(agent_factory, **overrides):
    agent = agent_factory(**overrides)
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"
    agent._state.task_language = "en"
    agent._state.response_language = "en"
    return agent


def _fix_agent(agent_factory, **overrides):
    return _planned_agent(agent_factory, **overrides)


def test_parser_accepts_finish_outcome_and_rejects_unknown_values():
    validate_action_args("final_answer", {"message": "Not done: file missing.", "outcome": "incomplete"})
    validate_action_args("report_to_user", {"summary": "s", "result": "r", "outcome": "done"})

    with pytest.raises(CommandValidationError, match="outcome"):
        validate_action_args("final_answer", {"message": "x", "outcome": "success"})


def test_modify_task_with_missing_target_ends_honestly_on_first_final_answer(agent_factory):
    agent = _fix_agent(agent_factory, max_steps=6)
    message = "src/payment.py does not exist in this workspace, so there is nothing to fix."
    prompts = _script(agent, [
        _step("read_file", {"path": "src/payment.py"}),
        _step("final_answer", {"message": message, "outcome": "incomplete"}, "done"),
    ])

    result = agent._run_loop("Fix the bug in src/payment.py", start_step=1)

    assert len(prompts) == 2
    assert result == message
    assert agent._state.run_status == "blocked"
    assert agent._state.progress < 100
    assert agent._state.step_logs[-1]["status"] == "final_answer"
    assert agent._state.step_logs[-1]["outcome"] == "incomplete"
    assert not any(log.get("status") == "blocked_final_answer" for log in agent._state.step_logs)


def test_incomplete_report_skips_gates_and_tells_the_reporter(agent_factory, monkeypatch):
    import agent_runtime

    agent = _fix_agent(agent_factory, max_steps=4)
    contexts: list[dict] = []
    monkeypatch.setattr(
        agent_runtime,
        "_render_report",
        lambda self, context: contexts.append(json.loads(context)) or "Not changed: the user denied the write.",
    )
    _script(agent, [
        _step(
            "report_to_user",
            {"summary": "Not changed", "result": "The user denied the write.", "outcome": "incomplete"},
            "done",
        ),
    ])

    result = agent._run_loop("Fix the bug in src/payment.py", start_step=1)

    assert result == "Not changed: the user denied the write."
    assert contexts and contexts[0]["outcome"] == "incomplete"
    assert agent._state.run_status == "blocked"


def test_done_claim_without_changes_is_refused_then_taken_as_not_done(agent_factory):
    agent = _fix_agent(agent_factory, max_steps=4)
    (agent.executor.workspace / "src").mkdir()
    (agent.executor.workspace / "src" / "payment.py").write_text("x = 1\n", encoding="utf-8")
    prompts = _script(agent, [
        _step("read_file", {"path": "src/payment.py"}),
        _step("final_answer", {"message": "Fixed.", "outcome": "done"}, "done"),
        _step("final_answer", {"message": "Fixed, really."}, "done"),
    ])

    result = agent._run_loop("Fix the bug in src/payment.py", start_step=1)

    blocked = [log for log in agent._state.step_logs if log.get("status") == "blocked_final_answer"]
    assert len(blocked) == 1
    # The refusal must name the honest exit instead of ordering a fix.
    assert "what was not done and why" in blocked[0]["reason"]
    # The second claim, with the blockers still standing, is its answer to
    # them: accepted, never as done, and the core says so under it.
    assert len(prompts) == 3
    assert result.startswith("Fixed, really.")
    assert "the task is not marked as done" in result
    assert agent._state.finished_incomplete
    assert agent._state.run_status == "blocked"


def test_a_turn_ending_without_calls_on_a_fix_with_no_changes_is_never_done(agent_factory):
    agent = _fix_agent(agent_factory, max_steps=5)
    agent._state.response_language = "ru"
    finals = []
    agent._ui.print_final = lambda message, user_messages=None, outcome="done": finals.append((message, outcome))
    answers = iter(["Исправил баг в src/payment.py.", "Исправил, правда."])
    prompts = []

    def fake_call_action_model(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    agent._call_action_model = fake_call_action_model

    result = agent._run_loop("Исправь баг в src/payment.py", start_step=1)

    assert "Ending the turn as done is not accepted" in prompts[1] or "not accepted as done" in prompts[1]
    assert result.startswith("Исправил, правда.")
    assert "задача не отмечена как выполненная" in result
    assert finals == [(result, "incomplete")]


def _call(name: str, **arguments) -> str:
    return "<tool_call>" + json.dumps({"name": name, "arguments": arguments}) + "</tool_call>"


def _scripted(agent, responses: list[str]) -> list[str]:
    prompts: list[str] = []

    def fake_call_action_model(prompt: str) -> str:
        prompts.append(prompt)
        return responses[min(len(prompts), len(responses)) - 1]

    agent._call_action_model = fake_call_action_model
    return prompts


def test_a_change_task_is_never_done_with_no_file_changed_even_after_a_good_last_call(agent_factory):
    agent = _planned_agent(agent_factory, max_steps=8)
    agent._state.response_language = "ru"
    workspace = agent.executor.workspace
    (workspace / "client.py").write_text("TIMEOUT = 5\n", encoding="utf-8")
    (workspace / "config.py").write_text("RETRIES = 3\n", encoding="utf-8")
    finals = []
    agent._ui.print_final = lambda message, user_messages=None, outcome="done": finals.append(outcome)
    # The write fails in the middle of the batch; the batch still ends on a
    # successful read, so the last call of the run succeeded.
    prompts = _scripted(agent, [
        "Читаю, правлю и сверяю конфиг.\n"
        + _call("read_file", path="client.py")
        + _call("replace_in_file", path="client.py", old_text="TIMEOUT = 50", new_text="TIMEOUT = 15")
        + _call("read_file", path="config.py"),
        "Готово.",
        "Готово.",
    ])

    result = agent._run_loop("Поправь таймаут в client.py", start_step=1)

    calls = [log for log in agent._state.step_logs if "feedback" in log]
    assert [log["feedback"]["ok"] for log in calls] == [True, False, True]
    blocked = [log for log in agent._state.step_logs if log.get("status") == "blocked_final_answer"]
    assert len(blocked) == 1 and "files_changed is empty" in blocked[0]["reason"]
    assert "no_changes" in agent._state.completion_blocker_kinds
    assert len(prompts) == 3
    assert result.startswith("Готово.") and "задача не отмечена как выполненная" in result
    assert finals == ["incomplete"]


def test_a_question_is_answered_as_done_after_a_failed_read(agent_factory):
    agent = _planned_agent(agent_factory, max_steps=6)
    agent._state.response_language = "ru"
    (agent.executor.workspace / "client.py").write_text("TIMEOUT = 5\n", encoding="utf-8")
    prompts = _scripted(agent, [
        _call("read_file", path="src/client.py"),
        _call("read_file", path="client.py"),
        "В `client.py` таймаут равен 5 секундам: `TIMEOUT = 5`. Файла `src/client.py` в проекте нет, поэтому значение берётся только отсюда.",
    ])

    result = agent._run_loop("Какой таймаут в client.py?", start_step=1)

    assert len(prompts) == 3
    assert result == "В `client.py` таймаут равен 5 секундам: `TIMEOUT = 5`. Файла `src/client.py` в проекте нет, поэтому значение берётся только отсюда."
    assert not agent._state.finished_incomplete


def test_every_blocker_text_names_the_honest_exit(agent_factory):
    agent = _fix_agent(agent_factory)
    agent._state.modify_goal = True
    agent._state.create_goal = True
    agent._state.error_history = ["boom"]
    agent._refresh_completion_requirements(force_enforcement=True)

    assert agent._state.completion_blockers
    for blocker in agent._state.completion_blockers:
        assert "what was not done and why" in blocker, blocker
    assert "what was not done and why" in agent._state.pending_instruction


def test_incomplete_finish_passes_the_required_action_gate(agent_factory):
    agent = _fix_agent(agent_factory)
    agent._state.required_next_actions = ["write_file"]

    honest = make_command("final_answer", {"message": "Cannot.", "outcome": "incomplete"}, "done")
    claim = make_command("final_answer", {"message": "Done."}, "done")

    assert agent._required_followup_error(honest) is None
    assert agent._required_followup_error(claim) is not None


def test_verification_still_runs_after_the_task_looks_complete(agent_factory):
    agent = _planned_agent(agent_factory, max_steps=8)
    executed: list[str] = []
    real_execute = agent._execute_command

    def recording_execute(command):
        executed.append(command.action.type)
        if command.action.type == "run_shell":
            return {
                "ok": True,
                "action_type": "run_shell",
                "data": {"command": command.action.args["command"], "returncode": 0, "stdout": "", "stderr": ""},
            }
        return real_execute(command)

    agent._execute_command = recording_execute
    prompts = _script(agent, [
        _step("write_file", {"path": "hello.py", "content": "print('hi')\n"}, "edit"),
        _step("read_file", {"path": "hello.py"}, "verify"),
        _step("run_shell", {"command": ["python3", "-m", "py_compile", "hello.py"]}, "verify"),
        _step("search_code", {"query": "print"}, "verify"),
        _step("final_answer", {"message": "Created hello.py; py_compile passes."}, "done"),
    ])

    result = agent._run_loop("Create hello.py that prints hi", start_step=1)

    assert agent._state.completion_ready
    assert executed == ["write_file", "read_file", "run_shell", "search_code"]
    assert all(log.get("status") != "error" for log in agent._state.step_logs)
    assert "The task looks complete" in prompts[-1]
    assert result == "Created hello.py; py_compile passes."
    assert agent._state.run_status == "completed"


@pytest.mark.parametrize(
    ("goal", "responses"),
    [
        ("Проверь, что hello.py существует", [
            _step("find_files", {"pattern": "hello.py"}),
            _step("list_tree", {}),
            _step("search_code", {"query": "hi"}),
        ]),
        ("Create hello.py that prints hi", [
            _step("write_file", {"path": "hello.py", "content": "print('hi')\n"}, "edit"),
            _step("read_file", {"path": "hello.py"}, "verify"),
            _step("list_tree", {}, "verify"),
        ]),
    ],
)
def test_runtime_never_synthesizes_a_success_answer(agent_factory, goal, responses):
    agent = _planned_agent(agent_factory, max_steps=3)
    (agent.executor.workspace / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    _script(agent, responses)

    result = agent._run_loop(goal, start_step=1)

    assert not hasattr(agent, "_build_auto_completion_message")
    assert not any(log.get("status") == "final_answer" for log in agent._state.step_logs)
    assert agent._state.run_status != "completed"
    assert not any(phrase in result.casefold() for phrase in _SYNTHETIC_SUCCESS)


def _last_feedback(agent) -> dict:
    return next(log["feedback"] for log in reversed(agent._state.step_logs) if "feedback" in log)


def test_read_only_guard_feedback_reaches_the_model_as_its_text(agent_factory):
    agent = agent_factory(max_steps=1)
    agent._state.read_only_goal = True
    agent._state.current_agent_role = "reader"
    command = make_command("write_file", {"path": "out.txt", "content": "x"}, "edit")

    from agent_runtime import _reject_read_only_violation

    assert _reject_read_only_violation(agent, 1, command)
    error = agent._feedback_error_for_model(_last_feedback(agent))
    assert "read-only" in error
    assert "Unknown error" not in "".join(agent._state.recent_feedback)
    assert any("read-only" in line for line in agent._state.recent_feedback)


@pytest.mark.parametrize("ready", [False, True])
def test_report_artifact_guard_feedback_reaches_the_model_as_its_text(agent_factory, monkeypatch, ready):
    agent = agent_factory(max_steps=1)
    monkeypatch.setattr(agent, "_refresh_completion_requirements", lambda **_: None)
    agent._state.completion_ready = ready
    command = make_command("write_file", {"path": "task_report.txt", "content": "x"}, "edit")

    from agent_runtime import _reject_report_artifact

    assert _reject_report_artifact(agent, 1, command)
    error = agent._feedback_error_for_model(_last_feedback(agent))
    assert "report artifact" in error.casefold()
    assert error != "Unknown error"


def test_report_substance_feedback_reaches_the_model_as_its_text(agent_factory):
    agent = agent_factory(max_steps=1)
    agent._state.report_goal = True
    agent._state.read_only_goal = True
    agent._state.verification_evidence = ["read_file: config.txt"]
    agent._state.files_read = ["config.txt"]
    command = make_command("final_answer", {"message": "Done."}, "done")

    from agent_runtime import _finish_with_final_answer

    outcome = _finish_with_final_answer(agent, 1, command)

    assert not outcome.finished
    error = agent._feedback_error_for_model(_last_feedback(agent))
    assert "your final answer" in error
