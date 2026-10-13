# -*- coding: utf-8 -*-
"""Model-declared outcomes are preserved; the runtime never invents success."""
from __future__ import annotations

import json

import pytest

from kernel.parser import CommandValidationError, validate_action_args
from kernel.tests.conftest import make_command

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
    from kernel import agent_runtime

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


def _call(name: str, **arguments) -> str:
    return "<tool_call>" + json.dumps({"name": name, "arguments": arguments}) + "</tool_call>"


def _scripted(agent, responses: list[str]) -> list[str]:
    prompts: list[str] = []

    def fake_call_action_model(prompt: str) -> str:
        prompts.append(prompt)
        return responses[min(len(prompts), len(responses)) - 1]

    agent._call_action_model = fake_call_action_model
    return prompts


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

    assert not agent._state.completion_ready
    assert executed == ["write_file", "read_file", "run_shell", "search_code"]
    assert all(log.get("status") != "error" for log in agent._state.step_logs)
    assert "The task looks complete" not in prompts[-1]
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

    from kernel.agent_runtime import _reject_read_only_violation

    assert _reject_read_only_violation(agent, 1, command)
    error = agent._feedback_error_for_model(_last_feedback(agent))
    assert "read-only" in error
    assert "Unknown error" not in "".join(agent._state.recent_feedback)
    assert any("read-only" in line for line in agent._state.recent_feedback)
