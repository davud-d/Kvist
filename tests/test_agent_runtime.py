from __future__ import annotations

from executor import ExecutionResult
from tests.conftest import make_command, make_plan


def test_execute_command_wraps_executor_success_and_error(agent_factory):
    agent = agent_factory()
    command = make_command("read_file", {"path": "x"})
    agent.executor.execute = lambda cmd: ExecutionResult(True, "read_file", {"path": "x"})
    assert agent._execute_command(command) == {"ok": True, "action_type": "read_file", "data": {"path": "x"}}

    agent.executor.execute = lambda cmd: ExecutionResult(False, "run_shell", {"error": "bad"})
    feedback = agent._execute_command(make_command("run_shell", {"command": ["python", "--version"]}))
    assert feedback["ok"] is False
    assert feedback["normalized_error"] == "bad"


def test_runtime_feedback_updates_read_write_web_and_verification_state(agent_factory):
    agent = agent_factory()
    agent._state.plan_info = make_plan()

    agent._register_feedback(1, make_command("read_file", {"path": "ui.py"}), {"ok": True, "data": {"path": "ui.py"}})
    assert agent._state.files_read == ["ui.py"]

    agent._state.web_facts = ["external fact"]
    agent._register_feedback(2, make_command("write_file", {"path": "app.py"}, plan_step="edit", progress=80), {"ok": True, "data": {"path": "app.py"}})
    assert agent._state.files_changed == ["app.py"]
    assert agent._state.applied_facts[-1]["fact"] == "external fact"

    agent._register_feedback(3, make_command("run_shell", {"command": ["python", "-m", "pytest"]}, plan_step="verify", progress=99), {"ok": True, "data": {"command": ["python", "-m", "pytest"], "returncode": 0}})
    assert "run_shell: python -m pytest" in agent._state.verification_evidence

    agent._register_feedback(4, make_command("web_search", {"query": "q"}), {"ok": True, "data": {"provider": "mock", "query": "q", "results": [{"title": "A", "url": "https://docs.python.org/3/"}]}})
    assert agent._state.web_sources[-1] == "https://docs.python.org/3/"


def test_runtime_error_feedback_and_repeat_blocking(agent_factory, tmp_path):
    agent = agent_factory(repeat_action_limit=2)
    command = make_command("read_file", {"path": "missing.py"})

    assert agent._is_repeat_blocked(command) is False
    assert agent._is_repeat_blocked(command) is False
    assert agent._is_repeat_blocked(command) is True
    assert agent._state.stagnation_count == 1

    feedback = agent._build_error_feedback("read_file", "invalid api key")
    assert "API key is invalid" in feedback["normalized_error"]


def test_completion_requirements_and_final_answer(agent_factory):
    agent = agent_factory()
    agent._state.plan_info = make_plan()
    agent._state.create_goal = True

    agent._refresh_completion_requirements(force_enforcement=True)
    assert agent._state.completion_blockers
    assert "write_file" in agent._state.required_next_actions
    assert agent._required_followup_error(make_command("read_file", {"path": "x"}))

    agent._state.files_changed = ["app.py"]
    agent._state.verification_evidence = ["run_shell: python -m pytest"]
    agent._state.completed_plan_steps = set(make_plan().plan)
    agent._refresh_completion_requirements()
    assert agent._state.completion_blockers == []

    agent._register_final_answer(5, "done")
    assert agent._state.progress == 100
    assert agent._state.step_logs[-1]["status"] == "final_answer"


def test_runtime_helpers_format_feedback_and_bounded_lists(agent_factory):
    agent = agent_factory()
    command = make_command("search_code", {"query": "Needle"})
    line = agent._feedback_to_line(command, {"ok": True, "data": {"results": [{"path": "a", "line": 1}], "query": "Needle"}})
    assert line == "ok: found 1 code matches"

    agent._push_unique(agent._state.files_read, "a", limit=2)
    agent._push_unique(agent._state.files_read, "b", limit=2)
    agent._push_unique(agent._state.files_read, "a", limit=2)
    assert agent._state.files_read == ["b", "a"]

    detail = agent._feedback_to_detail(command, {"ok": False, "normalized_error": "bad"})
    assert '"normalized_error": "bad"' in detail
    assert agent._render_feedback("summary", {"ok": False, "error": "bad"}).startswith("Result: ERROR")
