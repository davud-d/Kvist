from __future__ import annotations

import json
from pathlib import Path

import pytest

from kernel.executor import ExecutionResult
from kernel.tests.conftest import make_command


def test_execute_command_wraps_executor_success_and_error(agent_factory):
    agent = agent_factory()
    command = make_command("read_file", {"path": "x"})
    agent.executor.execute = lambda cmd: ExecutionResult(True, "read_file", {"path": "x"})
    assert agent._execute_command(command) == {"ok": True, "action_type": "read_file", "data": {"path": "x"}}

    agent.executor.execute = lambda cmd: ExecutionResult(False, "run_shell", {"error": "bad"})
    feedback = agent._execute_command(make_command("run_shell", {"command": ["python", "--version"]}))
    assert feedback["ok"] is False
    assert feedback["normalized_error"] == "bad"


def test_execute_command_redacts_tool_output_before_model_feedback(agent_factory):
    agent = agent_factory()
    synthetic = "sk-proj-" + "r" * 24
    command = make_command("read_file", {"path": "fixture.txt"})
    agent.executor.execute = lambda cmd: ExecutionResult(
        True,
        "read_file",
        {"path": "fixture.txt", "content": f"api_key={synthetic}"},
    )

    feedback = agent._execute_command(command)
    detail = agent._feedback_to_detail(command, feedback)

    assert synthetic not in str(feedback)
    assert synthetic not in detail
    assert "[REDACTED]" in detail


def test_runtime_feedback_updates_read_write_web_and_verification_state(agent_factory):
    agent = agent_factory()

    agent._register_feedback(1, make_command("read_file", {"path": "ui.py"}), {"ok": True, "data": {"path": "ui.py"}})
    assert agent._state.files_read == ["ui.py"]

    agent._state.web_facts = ["external fact"]
    agent._register_feedback(2, make_command("write_file", {"path": "app.py"}, plan_step="edit"), {"ok": True, "data": {"path": "app.py"}})
    assert agent._state.files_changed == ["app.py"]
    assert agent._state.applied_facts[-1]["fact"] == "external fact"

    agent._register_feedback(3, make_command("run_shell", {"command": ["python", "-m", "pytest"]}, plan_step="verify"), {"ok": True, "data": {"command": ["python", "-m", "pytest"], "returncode": 0}})
    assert "run_shell: python -m pytest" in agent._state.verification_evidence

    agent._register_feedback(4, make_command("web_search", {"query": "q"}), {"ok": True, "data": {"provider": "mock", "query": "q", "results": [{"title": "A", "url": "https://docs.python.org/3/"}]}})
    assert agent._state.web_sources[-1] == "https://docs.python.org/3/"


def test_runtime_error_feedback_and_repeat_blocking(agent_factory, tmp_path):
    from kernel.agent_runtime import _keep_result_in_view

    agent = agent_factory(repeat_action_limit=2)
    command = make_command("read_file", {"path": "missing.py"})

    assert agent._is_repeat_blocked(command) is False
    assert agent._is_repeat_blocked(command) is False
    _keep_result_in_view(agent, agent._action_signature(command), "missing.py result")
    assert agent._is_repeat_blocked(command) is True
    assert agent._state.stagnation_count == 1

    feedback = agent._build_error_feedback("read_file", "invalid api key")
    assert "API key is invalid" in feedback["normalized_error"]


def test_reread_is_allowed_once_its_content_has_left_the_prompt(agent_factory):
    from kernel.agent_runtime import _keep_result_in_view

    agent = agent_factory(repeat_action_limit=2, detailed_feedback_limit=2)
    read = make_command("read_file", {"path": "src/billing.py"})
    for _ in range(2):
        assert agent._is_repeat_blocked(read) is False
    _keep_result_in_view(agent, agent._action_signature(read), "file content")
    for path in ("a", "b"):
        listing = make_command("list_dir", {"path": path})
        _keep_result_in_view(agent, agent._action_signature(listing), f"{path} listing")

    assert agent._is_repeat_blocked(read) is False
    assert agent._state.stagnation_count == 0


def test_legitimate_long_inspection_does_not_trigger_hard_stagnation_stop(agent_factory):
    agent = agent_factory()
    for step in range(1, 20):
        path = f"module_{step}.py"
        agent._register_feedback(step, make_command("read_file", {"path": path}),
                                 {"ok": True, "data": {"path": path}})
    assert agent._state.stagnation_count == 0
    assert agent._build_early_stop_message(20) is None


def test_repeated_side_effect_action_stays_blocked_out_of_view(agent_factory):
    agent = agent_factory(repeat_action_limit=2)
    status = make_command("git_status", {})

    assert agent._is_repeat_blocked(status) is False
    assert agent._is_repeat_blocked(status) is False
    assert agent._is_repeat_blocked(status) is True


def test_a_repeated_result_replaces_its_own_copy_instead_of_evicting_a_read(agent_factory):
    from kernel.agent_runtime import _keep_result_in_view

    agent = agent_factory(detailed_feedback_limit=3)
    _keep_result_in_view(agent, '{"read": "billing.py"}', "file content")
    for index in range(5):
        _keep_result_in_view(agent, '{"git_status": {}}', f"status {index}")
    _keep_result_in_view(agent, '{"list_dir": "."}', "listing")

    assert agent._state.recent_result_details == ["file content", "status 4", "listing"]


def test_prompt_names_reads_whose_content_left_the_prompt(agent_factory):
    from kernel.agent_context import _read_files_out_of_view
    from kernel.agent_runtime import _keep_result_in_view

    agent = agent_factory(detailed_feedback_limit=2)
    agent._state.files_read = ["src/billing.py", "src/invoice.py"]
    for command in (
        make_command("read_file", {"path": "src/billing.py"}),
        make_command("read_multiple_files", {"paths": ["src/invoice.py"]}),
        make_command("git_status", {}),
    ):
        _keep_result_in_view(agent, agent._action_signature(command), "result")

    assert _read_files_out_of_view(agent._state) == ["src/billing.py"]


def test_reads_are_checkpointed_once_per_batch_writes_at_once(agent_factory):
    agent = agent_factory(max_steps=10)
    agent._state.selected_action_model = "mock"
    for name in ("a.py", "b.py", "c.py"):
        (agent.executor.workspace / name).write_text("x = 1\n", encoding="utf-8")
    reads = [{"type": "read_file", "args": {"path": name}, "description": "read"} for name in ("a.py", "b.py", "c.py")]
    responses = [
        {"thought": "t", "plan_step": "inspect", "user_message": "m", "actions": reads},
        {"thought": "t", "plan_step": "edit", "user_message": "m", "actions": [
            {"type": "write_file", "args": {"path": "d.py", "content": "y = 2\n"}, "description": "write"}]},
        {"thought": "t", "plan_step": "done", "user_message": "m", "actions": [
            {"type": "final_answer", "args": {"message": "stop", "outcome": "incomplete"}}]},
    ]
    agent._call_action_model = lambda prompt: json.dumps(responses.pop(0))
    saves: list[bool] = []
    original = agent._save_checkpoint

    def counting_save(goal, step, pending_action=None):
        saves.append(pending_action is not None)
        return original(goal, step, pending_action=pending_action)

    agent._save_checkpoint = counting_save

    agent._run_loop("look around", start_step=1)

    # batch of three reads: one save; the write: its write-ahead record, its
    # post-step save, and the batch save.
    assert saves[:4] == [False, True, False, False]
    assert agent._state.finished_incomplete
    assert agent._peek_checkpoint() is not None


def test_refused_finish_tells_the_model_why(agent_factory):
    from kernel.agent_runtime import _finish_with_final_answer

    agent = agent_factory(max_steps=1)
    agent._state.modify_goal = True

    outcome = _finish_with_final_answer(agent, 3, make_command("final_answer", {"message": "done"}, "done"))

    assert not outcome.finished
    assert agent._state.pending_instruction.startswith("Your finish was not accepted as done.")
    assert "no files were changed" in agent._state.pending_instruction


def test_prompt_carries_no_standing_governance(agent_factory):
    agent = agent_factory(max_steps=1)
    agent._state.modify_goal = True
    agent._state.required_artifacts = ["src/billing.py"]
    agent._refresh_completion_requirements()

    prompt = agent._build_runtime_prompt("почини инсталлятор", 3)

    for gone in ("Completion blockers", "Current progress", "Required artifacts", "Compressed state",
                 "Completion status", "Forbidden next actions", "Plan goal", "Success criteria"):
        assert gone not in prompt


def test_progress_comes_from_the_phase_of_the_calls_and_never_walks_back(agent_factory):
    agent = agent_factory(max_steps=1)

    agent._update_progress(make_command("read_file", {"path": "a.py"}, plan_step="inspect"), True)
    assert agent._state.progress == 15

    agent._update_progress(make_command("replace_in_file", {"path": "a.py"}, plan_step="edit"), True)
    assert agent._state.progress == 55

    agent._update_progress(make_command("read_file", {"path": "a.py"}, plan_step="inspect"), True)
    assert agent._state.progress == 55

    agent._update_progress(make_command("run_shell", {"command": ["true"]}, plan_step="verify"), False)
    assert agent._state.progress == 55


def test_completion_requirements_and_final_answer(agent_factory):
    agent = agent_factory()
    agent._state.create_goal = True

    agent._refresh_completion_requirements(force_enforcement=True)
    assert agent._state.completion_blockers
    assert "write_file" in agent._state.required_next_actions
    # Read-only inspection is deliberately allowed through a corrective block
    # (corrective_read_budget), so the model can read a file before patching it.
    # Completion is still blocked.
    assert agent._required_followup_error(make_command("read_file", {"path": "x"})) is None
    assert agent._required_followup_error(make_command("final_answer", {"message": "done"}))

    agent._state.files_changed = ["app.py"]
    agent._state.verification_evidence = ["run_shell: python -m pytest"]
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


def test_run_loop_create_task_finishes_only_on_the_models_final_answer(agent_factory):
    agent = agent_factory(max_steps=4)
    agent._state.response_language = "ru"
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"

    responses = [
        {
            "thought": "Создаю hello.py",
            "plan_step": "edit",
            "progress": 70,
            "user_message": "Создаю файл",
            "action": {
                "type": "write_file",
                "args": {"path": "hello.py", "content": "print('Hello')\n"},
            },
        },
        {
            "thought": "Проверяю hello.py",
            "plan_step": "verify",
            "progress": 90,
            "user_message": "Проверяю файл",
            "action": {
                "type": "read_file",
                "args": {"path": "hello.py"},
            },
        },
        {
            "thought": "Готово",
            "plan_step": "done",
            "user_message": "Готово",
            "action": {
                "type": "final_answer",
                "args": {"message": "Создал hello.py и прочитал его обратно."},
            },
        },
    ]
    call_count = {"value": 0}
    prompts: list[str] = []

    def fake_call_action_model(prompt: str) -> str:
        index = call_count["value"]
        if index >= len(responses):
            raise AssertionError("run_loop requested an extra action after the final answer")
        call_count["value"] += 1
        prompts.append(prompt)
        return json.dumps(responses[index], ensure_ascii=False)

    agent._call_action_model = fake_call_action_model

    result = agent._run_loop("Создай hello.py", start_step=1)

    # Readiness is only a hint: the run waits for the model to finish it.
    assert call_count["value"] == 3
    assert "The task looks complete" in prompts[-1]
    assert result == "Создал hello.py и прочитал его обратно."
    assert agent._state.step_logs[-1]["status"] == "final_answer"
    assert agent._state.run_status == "completed"
    assert (agent.executor.workspace / "hello.py").exists()
    assert not (agent.executor.workspace / "task_report.txt").exists()


def test_run_loop_does_not_finish_when_first_action_is_report_artifact(agent_factory):
    agent = agent_factory(max_steps=5)
    agent._state.response_language = "ru"
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"

    responses = [
        {
            "thought": "Сначала запишу отчёт",
            "plan_step": "edit",
            "progress": 40,
            "user_message": "Готовлю отчёт",
            "action": {
                "type": "write_file",
                "args": {"path": "task_report.txt", "content": "done\n"},
            },
        },
        {
            "thought": "Теперь создаю hello.py",
            "plan_step": "edit",
            "progress": 70,
            "user_message": "Создаю файл",
            "action": {
                "type": "write_file",
                "args": {"path": "hello.py", "content": "print('Hello')\n"},
            },
        },
        {
            "thought": "Проверяю hello.py",
            "plan_step": "verify",
            "progress": 90,
            "user_message": "Проверяю файл",
            "action": {
                "type": "read_file",
                "args": {"path": "hello.py"},
            },
        },
        {
            "thought": "Готово",
            "plan_step": "done",
            "user_message": "Готово",
            "action": {
                "type": "final_answer",
                "args": {"message": "Создал hello.py и проверил его."},
            },
        },
    ]
    call_count = {"value": 0}

    def fake_call_action_model(prompt: str) -> str:
        index = call_count["value"]
        if index >= len(responses):
            raise AssertionError("run_loop requested an extra action after the final answer")
        call_count["value"] += 1
        return json.dumps(responses[index], ensure_ascii=False)

    agent._call_action_model = fake_call_action_model

    result = agent._run_loop("Создай hello.py", start_step=1)

    assert call_count["value"] == 4
    assert "hello.py" in result
    assert agent._state.step_logs[-1]["status"] == "final_answer"
    assert any(log["status"] == "error" for log in agent._state.step_logs[:-1])
    assert not (agent.executor.workspace / "task_report.txt").exists()
    assert (agent.executor.workspace / "hello.py").exists()


def _read_responses(count: int) -> list[dict]:
    return [
        {
            "thought": f"Inspecting file {index}",
            "plan_step": "inspect",
            "progress": index * 10,
            "user_message": f"Inspecting file {index}",
            "action": {
                "type": "read_file",
                "args": {"path": f"file-{index}.txt"},
            },
        }
        for index in range(1, count + 1)
    ]


def test_run_loop_preserves_checkpoint_when_step_limit_interrupts_work(
    agent_factory,
):
    agent = agent_factory(max_steps=5)
    responses = iter(_read_responses(5))
    agent._call_action_model = lambda prompt: json.dumps(next(responses))
    agent._execute_command = lambda command: {
        "ok": True,
        "action_type": command.action.type,
        "data": {"path": command.action.args["path"]},
    }

    result = agent._run_loop("Investigate files", start_step=1)

    assert "step limit" in result
    restarted = agent_factory(max_steps=5)
    checkpoint = restarted._load_checkpoint()
    assert checkpoint == ("Investigate files", 5)
    assert len(restarted._state.files_read) == 5


def test_run_loop_records_blocked_action_breadcrumb(agent_factory):
    agent = agent_factory(max_steps=1)
    agent._state.create_goal = True
    # keep_explicit_restriction needs both set so the loop-start refresh
    # preserves required_next_actions instead of clearing it.
    agent._state.required_next_actions = ["write_file"]
    agent._state.pending_instruction = "Write the required file."

    response = {
        "thought": "look around first",
        "plan_step": "inspect",
        "progress": 10,
        "user_message": "listing",
        "action": {"type": "list_dir", "args": {"path": "."}},
    }
    agent._call_action_model = lambda prompt: json.dumps(response)

    agent._run_loop("Build the thing", start_step=1)

    # The corrective-guard block never reaches feedback, so without the
    # breadcrumb a resumed run would forget the refusal entirely.
    assert any("was blocked" in event for event in agent._state.recent_events)


def test_run_loop_checkpoints_before_the_fifth_step(agent_factory):
    agent = agent_factory(max_steps=6)
    responses = iter(_read_responses(5))
    calls = {"count": 0}

    def interrupt_after_two_steps(prompt: str) -> str:
        _ = prompt
        calls["count"] += 1
        if calls["count"] == 3:
            raise KeyboardInterrupt
        return json.dumps(next(responses))

    agent._call_action_model = interrupt_after_two_steps
    agent._execute_command = lambda command: {
        "ok": True,
        "action_type": command.action.type,
        "data": {"path": command.action.args["path"]},
    }

    result = agent._run_loop("Investigate files", start_step=1)

    # A stop is reported, not raised: the turn ends inside the loop.
    assert result == agent._build_cancellation_message()
    # Per-step checkpointing must capture step 2 even though the old
    # every-5-steps cadence would have saved nothing before step 5.
    restarted = agent_factory(max_steps=6)
    assert restarted._load_checkpoint() == ("Investigate files", 2)
    assert len(restarted._state.files_read) == 2


def test_run_loop_preserves_last_checkpoint_on_keyboard_interrupt(agent_factory):
    agent = agent_factory(max_steps=6)
    responses = iter(_read_responses(5))
    calls = {"count": 0}

    def interrupt_after_checkpoint(prompt: str) -> str:
        _ = prompt
        calls["count"] += 1
        if calls["count"] == 6:
            raise KeyboardInterrupt
        return json.dumps(next(responses))

    agent._call_action_model = interrupt_after_checkpoint
    agent._execute_command = lambda command: {
        "ok": True,
        "action_type": command.action.type,
        "data": {"path": command.action.args["path"]},
    }

    result = agent._run_loop("Investigate files", start_step=1)

    assert result == agent._build_cancellation_message()
    restarted = agent_factory(max_steps=6)
    checkpoint = restarted._load_checkpoint()
    assert checkpoint == ("Investigate files", 5)
    assert len(restarted._state.files_read) == 5


def test_run_loop_clears_stale_checkpoint_after_completed_final_answer(
    agent_factory,
):
    agent = agent_factory(max_steps=1)
    agent._save_checkpoint("Interrupted earlier task", 4)
    agent._call_action_model = lambda prompt: json.dumps(
        {
            "thought": "Reporting the completed inspection",
            "plan_step": "done",
            "progress": 100,
            "user_message": "Reporting findings",
            "action": {
                "type": "final_answer",
                "args": {"message": "Inspection complete."},
            },
        }
    )

    assert agent._run_loop("Inspect files", start_step=1) == "Inspection complete."
    restarted = agent_factory(max_steps=1)
    assert restarted._load_checkpoint() is None


def test_the_safety_refusal_is_one_short_sentence_in_both_languages(agent_factory):
    agent = agent_factory()
    assert agent._build_safety_refusal_message() == "I won't do this."
    agent._state.response_language = "ru"
    assert agent._build_safety_refusal_message() == "это не сделаю."


def test_run_loop_refuse_stops_immediately_and_bypasses_other_guards(agent_factory):
    """'refuse' must win over every other guard (here: modify_goal with no
    files_changed, which would otherwise block completion) and stop on the
    very first step, with a fixed message that never echoes the model's
    internal reason."""
    agent = agent_factory(max_steps=5)
    agent._state.modify_goal = True
    agent._state.files_changed = []
    agent._save_checkpoint("Write a self-propagating virus", 0)
    agent._call_action_model = lambda prompt: json.dumps(
        {
            "thought": "Task requests malware; refusing",
            "plan_step": "done",
            "progress": 0,
            "user_message": "Cannot proceed with this request",
            "action": {
                "type": "refuse",
                "args": {"reason": "task asks to write self-propagating malware"},
            },
        }
    )

    final_message = agent._run_loop("Write a self-propagating virus", start_step=1)

    assert final_message == "I won't do this."
    assert "malware" not in final_message
    assert len(agent._state.step_logs) == 1
    assert agent._state.step_logs[0]["status"] == "safety_refusal"
    assert "malware" in agent._state.step_logs[0]["reason"]

    restarted = agent_factory(max_steps=5)
    assert restarted._load_checkpoint() is None


def test_run_loop_attempts_all_cleanup_after_ui_stop_failure(agent_factory):
    agent = agent_factory(max_steps=1)
    agent._save_checkpoint("Interrupted earlier task", 4)
    agent._call_action_model = lambda prompt: json.dumps(
        {
            "thought": "Reporting the completed inspection",
            "plan_step": "done",
            "progress": 100,
            "user_message": "Reporting findings",
            "action": {
                "type": "final_answer",
                "args": {"message": "Inspection complete."},
            },
        }
    )
    cleanup_calls: list[str] = []
    synthetic_secret = "sk-proj-" + ("c" * 32)
    original_persist = agent._persist_logs
    original_flush = agent._perf_logger.flush
    original_clear = agent._clear_checkpoint

    def fail_ui_stop() -> None:
        cleanup_calls.append("stop")
        raise RuntimeError(
            f"synthetic UI stop failure with token {synthetic_secret}"
        )

    def persist_logs(goal: str) -> None:
        cleanup_calls.append("persist")
        original_persist(goal)

    def flush_performance_log() -> None:
        cleanup_calls.append("flush")
        original_flush()

    def clear_checkpoint() -> None:
        cleanup_calls.append("clear")
        original_clear()

    agent._ui.stop_live = fail_ui_stop
    agent._persist_logs = persist_logs
    agent._perf_logger.flush = flush_performance_log
    agent._clear_checkpoint = clear_checkpoint

    with pytest.raises(RuntimeError) as caught:
        agent._run_loop("Inspect files", start_step=1)

    assert cleanup_calls == ["stop", "persist", "flush", "clear"]
    assert "synthetic UI stop failure" in str(caught.value)
    assert synthetic_secret not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)
    assert caught.value.__cause__ is None
    restarted = agent_factory(max_steps=1)
    assert restarted._load_checkpoint() is None


def test_run_loop_cleanup_does_not_mask_keyboard_interrupt(agent_factory):
    agent = agent_factory(max_steps=1)
    cleanup_calls: list[str] = []
    synthetic_secret = "sk-proj-" + ("i" * 32)

    def interrupt_model(prompt: str) -> str:
        _ = prompt
        raise KeyboardInterrupt

    def fail_ui_stop() -> None:
        cleanup_calls.append("stop")
        raise RuntimeError(
            f"synthetic UI stop failure with token {synthetic_secret}"
        )

    agent._call_action_model = interrupt_model
    agent._ui.stop_live = fail_ui_stop
    agent._persist_logs = lambda goal: cleanup_calls.append("persist")
    agent._perf_logger.flush = lambda: cleanup_calls.append("flush")

    # A stop is a normal outcome now, so there is no primary failure for the
    # cleanup report to attach to: a failing cleanup surfaces on its own,
    # still redacted, and only after every other cleanup was attempted.
    with pytest.raises(RuntimeError) as caught:
        agent._run_loop("Investigate files", start_step=1)

    assert cleanup_calls == ["stop", "persist", "flush"]
    reported = str(caught.value)
    assert "synthetic UI stop failure" in reported
    assert synthetic_secret not in reported
    assert "[REDACTED]" in reported


def test_response_text_is_kept_as_the_step_note(agent_factory):
    from kernel.agent_runtime import _keep_step_summary

    agent = agent_factory(max_steps=1)
    note = "billing.py:40 already rounds half-up; the reported cause is ruled out"

    _keep_step_summary(agent, 7, note)

    assert agent._state.rolling_summaries == {7: note}


def test_response_text_shared_by_a_batch_is_kept_once(agent_factory):
    from kernel.agent_runtime import _keep_step_summary

    agent = agent_factory(max_steps=1)
    note = "invoice.py never applies the discount"

    for step in (11, 12, 13):
        _keep_step_summary(agent, step, note)
    _keep_step_summary(agent, 14, "")

    assert agent._state.rolling_summaries == {11: note}


# ── P0: api_timeout ──────────────────────────────────────────────────

def test_api_timeout_default_value():
    """P0: AgentConfig has api_timeout with default 60.0."""
    from kernel.agent_state import AgentConfig
    config = AgentConfig()
    assert hasattr(config, "api_timeout")
    assert config.api_timeout == 60.0


# ── P1: modify_goal ──────────────────────────────────────────────────

def test_is_modify_goal_detects_fix_and_edit_intents():
    """P1: _is_modify_goal returns True for fix/modify/change/edit patterns."""
    from kernel.agent_runtime import _is_modify_goal
    assert _is_modify_goal("Add markdown rendering to ui.py")
    assert _is_modify_goal("Fix the bug in main.py")
    assert _is_modify_goal("fix: error in login")
    assert _is_modify_goal("Modify the parser")
    assert _is_modify_goal("Change the API endpoint")
    assert _is_modify_goal("Update dependencies")
    assert _is_modify_goal("Edit the config file")
    assert _is_modify_goal("Refactor the handler")
    assert _is_modify_goal("Improve performance")
    assert _is_modify_goal("Добавь Markdown-рендеринг финального ответа в UI")
    assert _is_modify_goal("Исправь ошибку в main.py")
    assert _is_modify_goal("Изменить настройки")
    assert not _is_modify_goal("Check if main.py exists")
    assert not _is_modify_goal("Create a new file")


def test_modify_goal_blocks_final_answer_without_changes(agent_factory):
    """P1: modify_goal + empty files_changed → final_answer is blocked."""
    agent = agent_factory()
    agent._state.modify_goal = True
    agent._state.files_changed = []

    agent._refresh_completion_requirements(force_enforcement=True)
    assert agent._state.completion_blockers
    assert any("Completion blocked" in b for b in agent._state.completion_blockers)


def test_task_017_find_files_final_answer_blocked(agent_factory):
    """P1 task_017: find_files → final_answer must be blocked for modify tasks."""
    from kernel.agent_runtime import _is_modify_goal, _WRITE_ACTIONS

    agent = agent_factory(max_steps=4)
    agent._state.modify_goal = True
    agent._state.task_language = "en"
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"

    # Step 1: find_files (read-only)
    # Step 2: premature final_answer without changes → should be blocked
    # Step 3: model should be forced to take corrective action
    responses = [
        {
            "thought": "Finding the buggy files",
            "plan_step": "inspect",
            "progress": 15,
            "user_message": "Searching for files",
            "action": {
                "type": "find_files",
                "args": {"pattern": "*.py"},
            },
        },
        {
            "thought": "I found the files, now reporting",
            "plan_step": "done",
            "progress": 100,
            "user_message": "Task is done",
            "action": {
                "type": "final_answer",
                "args": {"message": "Found the files, task complete"},
            },
        },
        {
            "thought": "I need to actually fix the file",
            "plan_step": "edit",
            "progress": 50,
            "user_message": "Fixing the file",
            "action": {
                "type": "write_file",
                "args": {"path": "router.py", "content": "fixed"},
            },
        },
    ]
    call_count = {"value": 0}

    def fake_call_action_model(prompt: str) -> str:
        index = call_count["value"]
        if index >= len(responses):
            raise AssertionError("task_017: should have blocked final_answer, not requested more")
        call_count["value"] += 1
        return json.dumps(responses[index], ensure_ascii=False)

    agent._call_action_model = fake_call_action_model

    # Simulate executor: find_files succeeds but doesn't change any files
    def fake_execute(cmd):
        if cmd.action.type == "find_files":
            return {"ok": True, "action_type": "find_files", "data": {"pattern": "*.py", "matches": ["main.py", "router.py"]}}
        if cmd.action.type == "final_answer":
            return {"ok": True, "action_type": "final_answer", "data": {"message": cmd.action.args.get("message", "")}}
        return {"ok": False, "action_type": cmd.action.type, "error": "unexpected"}
    agent._execute_command = fake_execute

    result = agent._run_loop("Fix the router", start_step=1)

    # Verify that step 2 (final_answer) was blocked
    blocked_final = [log for log in agent._state.step_logs if log.get("status") == "blocked_final_answer"]
    assert len(blocked_final) >= 1, f"Expected blocked_final_answer in step_logs, got: {[l['status'] for l in agent._state.step_logs]}"
    assert "Completion blocked" in blocked_final[0].get("reason", "")


def test_task_017_live_scenario_russian_modify_blocked(agent_factory):
    """P1: Live scenario — Russian modify goal, find_files+read_file then
    final_answer must be blocked when files_changed is empty.

    Exact task_017 prompt:
      В файле empty_list.py исправь функцию average, чтобы она обрабатывала
      пустой список и возвращала 0 вместо ошибки деления на ноль.

    State: files_read=["empty_list.py"], files_changed=[]
    Model action: final_answer
    Expected: final_answer blocked, completion not allowed.
    """
    from kernel.agent_runtime import _is_modify_goal, _is_verify_only_goal

    TASK_017_GOAL = (
        "В файле empty_list.py исправь функцию average, "
        "чтобы она обрабатывала пустой список и возвращала 0 "
        "вместо ошибки деления на ноль."
    )

    # --- Checkpoint 2: _is_modify_goal must return True for this Russian string ---
    assert _is_modify_goal(TASK_017_GOAL), (
        "_is_modify_goal must be True for 'исправь' marker"
    )
    assert not _is_verify_only_goal(TASK_017_GOAL), (
        "_is_verify_only_goal must be False for a FIX/MODIFY task"
    )

    agent = agent_factory(max_steps=5)
    agent._state.task_language = "ru"
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"

    # Live scenario: model reads the buggy file, then tries to end early
    responses = [
        # Step 1: find_files to locate the target
        {
            "thought": "Ищу файл для исправления",
            "plan_step": "inspect",
            "progress": 10,
            "user_message": "Searching for the target file",
            "action": {
                "type": "find_files",
                "args": {"pattern": "*.py"},
            },
        },
        # Step 2: read the file content
        {
            "thought": "Читаю содержимое empty_list.py",
            "plan_step": "inspect",
            "progress": 20,
            "user_message": "Reading empty_list.py for analysis",
            "action": {
                "type": "read_file",
                "args": {"path": "empty_list.py"},
            },
        },
        # Step 3: premature final_answer — THIS MUST BE BLOCKED
        {
            "thought": "Файл прочитан, задача выполнена",
            "plan_step": "done",
            "progress": 100,
            "user_message": "Task is complete, all checks passed",
            "action": {
                "type": "final_answer",
                "args": {
                    "message": "Task completed successfully. All checks passed."
                },
            },
        },
        # Step 4: model should now attempt a corrective write action
        {
            "thought": "Исправляю функцию average для обработки пустого списка",
            "plan_step": "edit",
            "progress": 60,
            "user_message": "Fixing the average function to handle empty list",
            "action": {
                "type": "replace_in_file",
                "args": {
                    "path": "empty_list.py",
                    "old_text": "return total / len(numbers)",
                    "new_text": "if len(numbers) == 0:\n        return 0\n    return total / len(numbers)",
                },
            },
        },
    ]
    call_count = {"value": 0}

    def fake_call_action_model(prompt: str) -> str:
        index = call_count["value"]
        if index >= len(responses):
            raise AssertionError(
                f"task_017 live: should have blocked final_answer at step 3, "
                f"not required step {index + 1}"
            )
        call_count["value"] += 1
        return json.dumps(responses[index], ensure_ascii=False)

    agent._call_action_model = fake_call_action_model

    def fake_execute(cmd):
        if cmd.action.type == "find_files":
            return {
                "ok": True,
                "action_type": "find_files",
                "data": {
                    "pattern": "*.py",
                    "matches": ["empty_list.py"],
                },
            }
        if cmd.action.type == "read_file":
            return {
                "ok": True,
                "action_type": "read_file",
                "data": {
                    "path": cmd.action.args.get("path", ""),
                    "content": "def average(numbers):\n    total = 0\n    for n in numbers:\n        total += n\n    return total / len(numbers)\n",
                },
            }
        if cmd.action.type == "replace_in_file":
            return {
                "ok": True,
                "action_type": "replace_in_file",
                "data": {
                    "path": cmd.action.args.get("path", ""),
                    "occurrences_replaced": 1,
                },
            }
        if cmd.action.type == "final_answer":
            return {
                "ok": True,
                "action_type": "final_answer",
                "data": {"message": cmd.action.args.get("message", "")},
            }
        return {"ok": False, "action_type": cmd.action.type, "error": "unexpected"}
    agent._execute_command = fake_execute

    result = agent._run_loop(TASK_017_GOAL, start_step=1)

    # --- Checkpoint 1: modify_goal must be set in state ---
    assert agent._state.modify_goal is True, (
        "modify_goal must be True in RunState for this task"
    )

    # --- Checkpoint 3 & 4: final_answer at step 3 must be blocked ---
    blocked_final = [
        log for log in agent._state.step_logs
        if log.get("status") == "blocked_final_answer"
    ]
    assert len(blocked_final) >= 1, (
        f"Expected blocked_final_answer in step_logs, "
        f"got statuses: {[l['status'] for l in agent._state.step_logs]}"
    )
    reason = blocked_final[0].get("reason", "")
    assert "Completion blocked" in reason, (
        f"Blocker message must contain 'Completion blocked', got: {reason!r}"
    )
    assert "FIX/MODIFY" in reason or "no files were changed" in reason, (
        f"Blocker must mention FIX/MODIFY task nature, got: {reason!r}"
    )

    # After blocking, files_changed should still be empty (model was forced
    # to take corrective action but we didn't reach it in this test)
    # The important thing: completion was NOT allowed
    final_steps = [
        log for log in agent._state.step_logs
        if log.get("status") == "final_answer"
    ]
    assert len(final_steps) == 0, (
        "final_answer must not be accepted — task should not complete"
    )


# ── P2: security_policy_violations ───────────────────────────────────

def test_security_policy_violations_tracked(agent_factory):
    """P2: .env errors are recorded in security_policy_violations list."""
    agent = agent_factory()

    cmd = make_command("read_file", {"path": ".env"})
    feedback = {
        "ok": False,
        "action_type": "read_file",
        "raw_error": "⛔ SECURITY POLICY VIOLATION: Attempted access to a .env file is permanently forbidden. Do not retry.",
    }
    agent._register_feedback(1, cmd, feedback)

    assert len(agent._state.security_policy_violations) >= 1
    assert any(".env" in v for v in agent._state.security_policy_violations)
    assert "SECURITY POLICY" in agent._state.pending_instruction


def test_security_policy_shell_env_blocked(agent_factory):
    """P2: Shell commands accessing .env are blocked."""
    agent = agent_factory()

    cmd = make_command("run_shell", {"command": ["cat", ".env"]})
    feedback = {
        "ok": False,
        "action_type": "run_shell",
        "raw_error": "⛔ SECURITY POLICY VIOLATION: Shell command is attempting to access a .env file which is permanently forbidden. Do not retry.",
    }
    agent._register_feedback(1, cmd, feedback)

    assert len(agent._state.security_policy_violations) >= 1
    assert "Do not retry" in agent._state.pending_instruction or "permanently forbidden" in agent._state.pending_instruction


# ── P3: pending_verification_paths ───────────────────────────────────

def test_pending_verification_paths_added_after_write(agent_factory):
    """P3: After write_file, the path is added to pending_verification_paths."""
    agent = agent_factory()

    cmd = make_command("write_file", {"path": "hello.py", "content": "print(1)"}, plan_step="edit")
    feedback = {"ok": True, "action_type": "write_file", "data": {"path": "hello.py"}}
    agent._register_feedback(1, cmd, feedback)

    assert "hello.py" in agent._state.files_changed
    assert "hello.py" in agent._state.pending_verification_paths


def test_verification_clears_pending_paths(agent_factory):
    """P3: git_diff and run_shell (pytest) clear pending_verification_paths; git_status does NOT."""
    agent = agent_factory()
    agent._state.files_changed = ["hello.py"]
    agent._state.pending_verification_paths = ["hello.py"]

    # git_status does NOT clear
    cmd_status = make_command("git_status", {}, plan_step="verify")
    agent._update_verification_evidence(cmd_status, {"returncode": 0, "git_available": True, "fallback_used": False})
    assert "hello.py" in agent._state.pending_verification_paths

    # git_diff DOES clear
    cmd_diff = make_command("git_diff", {}, plan_step="verify")
    agent._state.pending_verification_paths = ["hello.py"]  # reset
    agent._update_verification_evidence(cmd_diff, {"returncode": 0, "git_available": True, "fallback_used": False})
    assert len(agent._state.pending_verification_paths) == 0


def test_run_shell_pytest_clears_pending_paths(agent_factory):
    """P3: run_shell with pytest clears pending_verification_paths."""
    agent = agent_factory()
    agent._state.files_changed = ["hello.py"]
    agent._state.pending_verification_paths = ["hello.py"]

    cmd = make_command("run_shell", {"command": ["python", "-m", "pytest"]}, plan_step="verify")
    agent._update_verification_evidence(cmd, {
        "command": ["python", "-m", "pytest"],
        "returncode": 0,
    })
    assert len(agent._state.pending_verification_paths) == 0
    assert any("run_shell" in v for v in agent._state.verification_evidence)


def test_pending_verification_blocks_final_answer(agent_factory):
    """P3: final_answer is blocked when pending_verification_paths is not empty."""
    agent = agent_factory()
    agent._state.create_goal = True
    agent._state.files_changed = ["app.py"]
    agent._state.pending_verification_paths = ["app.py"]

    agent._refresh_completion_requirements()
    assert agent._state.completion_blockers
    assert any("verification required" in b.lower() for b in agent._state.completion_blockers)


# ── P4: devops run_shell parsing ───────────────────────────────────────

@pytest.mark.offline_smoke
def test_devops_run_shell_parsed_and_executed(agent_factory):
    """DevOps run_shell: parser accepts command=[\"systemctl\",\"status\",\"nginx\"] and executor runs it."""
    from kernel.tests.conftest import commands_from_response

    devops_response = json.dumps({
        "thought": "Проверяю статус systemd сервиса",
        "plan_step": "edit",
        "progress": 60,
        "user_message": "Проверяю статус nginx",
        "action": {
            "type": "run_shell",
            "args": {
                "command": ["systemctl", "status", "nginx", "--no-pager"],
                "timeout": 15,
            },
        },
    }, ensure_ascii=False)

    (command,) = commands_from_response(devops_response)
    assert command.action.type == "run_shell"
    assert isinstance(command.action.args["command"], list)
    assert command.action.args["command"] == ["systemctl", "status", "nginx", "--no-pager"]
    assert all(isinstance(x, str) for x in command.action.args["command"])

    agent = agent_factory()
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"

    executed_commands = []

    def fake_execute(cmd):
        if cmd.action.type == "run_shell":
            executed_commands.append(cmd.action.args["command"])
            return {"ok": True, "action_type": "run_shell", "data": {
                "command": cmd.action.args["command"],
                "stdout": "● nginx.service - nginx - high performance web server\n   Loaded: loaded\n   Active: active (running)",
                "returncode": 0,
            }}
        return {"ok": False, "action_type": cmd.action.type, "error": "unexpected"}

    agent._execute_command = fake_execute
    agent._register_feedback(1, command, fake_execute(command))
    assert len(executed_commands) == 1
    assert executed_commands[0] == ["systemctl", "status", "nginx", "--no-pager"]
    # Verify no parse error in step logs
    parse_errors = [log for log in agent._state.step_logs if log.get("status") == "parse_error"]
    assert len(parse_errors) == 0


def test_devops_run_shell_string_command_rejected(agent_factory):
    """DevOps run_shell: command as string → clear parse error, not generic 'Invalid model response'."""
    from kernel.parser import CommandValidationError
    from kernel.tests.conftest import commands_from_response

    devops_response_bad = json.dumps({
        "thought": "Проверяю статус systemd сервиса",
        "plan_step": "edit",
        "progress": 60,
        "user_message": "Проверяю статус nginx",
        "action": {
            "type": "run_shell",
            "args": {
                "command": "systemctl status nginx --no-pager",
                "timeout": 15,
            },
        },
    }, ensure_ascii=False)

    with pytest.raises(CommandValidationError) as exc_info:
        commands_from_response(devops_response_bad)

    error_msg = str(exc_info.value)
    assert "must be a list of strings" in error_msg, (
        f"Expected 'must be a list of strings' in error, got: {error_msg!r}"
    )
    # Must NOT be a generic message
    assert "Invalid model response" not in error_msg
    assert "Invalid JSON" not in error_msg


# ── P4: reader read-only sqlite3 shell allowlist ────────────────────────


def test_read_only_shell_allows_sqlite3_select(agent_factory):
    """A read-only reader task may query a database with a plain SELECT.

    Regression for h10-sqlite: the model had no read-only way to query
    data/users.db and fell back to read_file on the raw binary, guessing
    the answer instead of reporting it.
    """
    from kernel.agent_runtime import _is_read_only_violation

    agent = agent_factory()
    agent._state.read_only_goal = True
    agent._state.current_agent_role = "reader"
    command = make_command(
        "run_shell",
        {"command": ["sqlite3", "data/users.db", "SELECT COUNT(*) FROM users;"]},
    )

    assert not _is_read_only_violation(agent, command)


@pytest.mark.parametrize(
    "sql_command",
    [
        ["sqlite3", "data/users.db", "INSERT INTO users VALUES (1);"],
        ["sqlite3", "data/users.db", "DROP TABLE users;"],
        ["sqlite3", "data/users.db", "ATTACH DATABASE 'x.db' AS x;"],
        ["sqlite3", "data/users.db", ".import users.csv users"],
        ["sqlite3", "data/users.db", ".shell rm -rf /"],
    ],
    ids=["insert", "drop", "attach", "dot-import", "dot-shell"],
)
def test_read_only_shell_rejects_sqlite3_write_forms(agent_factory, sql_command):
    from kernel.agent_runtime import _is_read_only_violation

    agent = agent_factory()
    agent._state.read_only_goal = True
    agent._state.current_agent_role = "reader"
    command = make_command("run_shell", {"command": sql_command})

    assert _is_read_only_violation(agent, command)


def test_read_only_shell_rejects_sqlite3_multi_statement_with_a_trailing_write(agent_factory):
    """A leading SELECT must not launder a later write in the same argument."""
    from kernel.agent_runtime import _is_read_only_violation

    agent = agent_factory()
    agent._state.read_only_goal = True
    agent._state.current_agent_role = "reader"
    command = make_command(
        "run_shell",
        {
            "command": [
                "sqlite3",
                "data/users.db",
                "SELECT COUNT(*) FROM users; DELETE FROM users;",
            ]
        },
    )

    assert _is_read_only_violation(agent, command)


def test_read_only_shell_still_rejects_python_dash_c(agent_factory):
    """python -c stays out of the read-only allowlist regardless of this change.

    terminal_interpreters.py allows '-c' only as one code argument (see
    tests/test_terminal.py); this confirms the read-only-goal gate never
    grew a bypass for it.
    """
    from kernel.agent_runtime import _is_read_only_violation

    agent = agent_factory()
    agent._state.read_only_goal = True
    agent._state.current_agent_role = "reader"
    command = make_command(
        "run_shell",
        {"command": ["python", "-c", "import os; os.remove('data/users.db')"]},
    )

    assert _is_read_only_violation(agent, command)


# ── P5: devops systemctl allowlist + blocked command handling ──────────

def test_devops_systemctl_status_passes_terminal_validation(agent_factory):
    """Devops run_shell with systemctl status passes terminal allowlist."""
    from kernel.tests.conftest import commands_from_response
    from kernel.terminal import TerminalExecutor

    devops_response = json.dumps({
        "thought": "Проверяю статус nginx",
        "plan_step": "edit",
        "progress": 60,
        "user_message": "Проверяю статус nginx",
        "action": {
            "type": "run_shell",
            "args": {
                "command": ["systemctl", "status", "nginx", "--no-pager"],
                "timeout": 15,
            },
        },
    }, ensure_ascii=False)

    (command,) = commands_from_response(devops_response)
    assert command.action.type == "run_shell"
    assert isinstance(command.action.args["command"], list)

    # Systemctl must now pass terminal validation in dev profile
    agent = agent_factory()
    terminal = TerminalExecutor(agent.executor.workspace, profile="dev")
    # Must not raise TerminalError
    terminal._validate(["systemctl", "status", "nginx", "--no-pager"])


def test_devops_systemctl_dangerous_blocked_by_terminal(agent_factory):
    """Dangerous systemctl subcommands (restart, enable) are blocked by terminal."""
    from kernel.terminal import TerminalExecutor, TerminalError

    agent = agent_factory()
    terminal = TerminalExecutor(agent.executor.workspace, profile="dev")

    with pytest.raises(TerminalError, match="Subcommand"):
        terminal._validate(["systemctl", "restart", "nginx"])

    with pytest.raises(TerminalError, match="Subcommand"):
        terminal._validate(["systemctl", "enable", "nginx"])


def test_devops_blocked_command_produces_honest_feedback(agent_factory):
    """When run_shell is blocked, feedback is an honest error, not a silent success."""
    agent = agent_factory()
    agent._state.current_agent_role = "devops"
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"
    agent._state.task_language = "ru"

    cmd = make_command("run_shell", {"command": ["systemctl", "restart", "nginx"], "timeout": 15})
    feedback = {"ok": False, "action_type": "run_shell", "error": "Subcommand 'restart' is not allowed for 'systemctl'"}
    agent._register_feedback(1, cmd, feedback)

    # Verify that error feedback is recorded honestly
    step = agent._state.step_logs[0]
    assert step["status"] == "error"
    # Verify that final_answer cannot complete when the core operation wasn't done
    agent._state.files_changed = []
    agent._state.read_only_goal = False  # devops run_shell should not be blocked by read-only
    agent._refresh_completion_requirements(force_enforcement=True)
    # The blocked command should be visible as a failure reason
    assert len(agent._state.step_logs) >= 1


def test_devops_status_nginx_no_parse_error(agent_factory):
    """Full cycle: devops 'nginx status' → run_shell parsed → no parse_error."""
    from kernel.tests.conftest import commands_from_response

    devops_response = json.dumps({
        "thought": "Проверяю статус systemd сервиса nginx",
        "plan_step": "edit",
        "progress": 60,
        "user_message": "Проверяю статус nginx",
        "action": {
            "type": "run_shell",
            "args": {
                "command": ["systemctl", "status", "nginx", "--no-pager"],
                "timeout": 15,
            },
        },
    }, ensure_ascii=False)

    (command,) = commands_from_response(devops_response)
    assert command.action.type == "run_shell"
    assert command.action.args["command"] == ["systemctl", "status", "nginx", "--no-pager"]
    assert all(isinstance(x, str) for x in command.action.args["command"])

    agent = agent_factory()
    agent._state.current_agent_role = "devops"
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"
    agent._state.task_language = "ru"

    agent._register_feedback(1, command, {
        "ok": True,
        "action_type": "run_shell",
        "data": {
            "command": ["systemctl", "status", "nginx", "--no-pager"],
            "returncode": 0,
            "stdout": "● nginx.service — активен",
        },
    })

    # Verify no parse_error and the step was recorded as ok
    step = agent._state.step_logs[0]
    assert step["status"] != "parse_error"
    assert step["status"] == "ok"


def test_devops_systemctl_nonzero_exit_includes_stderr_in_error(agent_factory):
    """When systemctl returns nonzero, error text includes stderr, not generic 'Action failed'."""
    from kernel.executor import ExecutionResult

    agent = agent_factory()
    agent._state.current_agent_role = "devops"

    # Simulate systemctl returning "Unit nginx.service could not be found."
    cmd = make_command("run_shell", {"command": ["systemctl", "status", "nginx", "--no-pager"]})
    result = ExecutionResult(ok=False, action_type="run_shell", data={
        "command": ["systemctl", "status", "nginx", "--no-pager"],
        "returncode": 4,
        "stdout": "",
        "stderr": "Unit nginx.service could not be found.",
    })

    agent.executor.execute = lambda c: result
    feedback = agent._execute_command(cmd)

    # Error must include the real stderr, not generic "Action failed"
    assert feedback["ok"] is False
    error_text = agent._feedback_error_for_model(feedback)
    assert "nginx.service could not be found" in error_text, (
        f"Error must contain real systemctl output, got: {error_text!r}"
    )
    assert error_text != "Action failed"
    assert "Action failed" not in error_text


def test_run_loop_feeds_the_response_text_to_the_live_panel(agent_factory):
    # The thinking column's fallback for providers that stream no reasoning.
    agent = agent_factory(max_steps=1)
    shown: list[str] = []
    agent._ui.set_last_thought = shown.append
    agent._call_action_model = lambda prompt: _v2("Сначала смотрю корень", _tool("list_dir", path="."))
    agent.executor.execute = lambda cmd: ExecutionResult(True, "list_dir", {"entries": []})

    agent._run_loop("Investigate files", start_step=1)

    assert shown == ["Сначала смотрю корень"]


def test_unverified_blocker_names_the_cheapest_accepted_action(agent_factory):
    agent = agent_factory()
    agent._state.files_changed = ["main.py"]
    agent._refresh_completion_requirements()

    assert any("read_file on a file you changed" in text for text in agent._state.completion_blockers)


def test_unverified_write_surfaces_a_blocker_label(agent_factory):
    """The panel needs a short reason; the blocker text itself is model-facing."""
    from kernel.agent_runtime import _run_position

    agent = agent_factory()
    agent._state.response_language = "ru"
    agent._state.files_changed = ["main.py"]
    agent._refresh_completion_requirements()

    assert "unverified" in agent._state.completion_blocker_kinds
    assert _run_position(agent)["blocker"] == "ждёт проверки правок"

    agent._state.response_language = "en"
    assert _run_position(agent)["blocker"] == "changes not verified"


def test_no_blocker_label_once_the_change_is_verified(agent_factory):
    from kernel.agent_runtime import _run_position

    agent = agent_factory()
    agent._state.files_changed = ["main.py"]
    agent._state.verification_evidence = ["git_diff: main.py"]
    agent._refresh_completion_requirements()

    assert _run_position(agent)["blocker"] == ""


# ── step budget ──────────────────────────────────────────────────────────────

def _endless_reader(agent):
    """An action model that never finishes, so only the budget can stop it."""
    calls = {"value": 0}

    def fake_call_action_model(prompt: str) -> str:
        calls["value"] += 1
        return json.dumps(
            {
                "thought": "Reading again",
                "plan_step": "inspect",
                "user_message": "Reading",
                "action": {"type": "list_dir", "args": {"path": "."}},
            }
        )

    agent._call_action_model = fake_call_action_model
    return calls


def test_budget_is_a_hard_stop_without_an_interactive_ui(agent_factory):
    """Headless frontends and run_task.py have nobody to ask — keep the cap."""
    agent = agent_factory(max_steps=3)
    calls = _endless_reader(agent)

    result = agent._run_loop("Посмотри файлы", start_step=1)

    assert calls["value"] == 3
    assert not hasattr(agent._ui, "ask_choice")
    assert "лимит" in result or "limit" in result


def test_answering_continue_extends_the_budget(agent_factory):
    agent = agent_factory(max_steps=3)
    calls = _endless_reader(agent)
    asked: list[str] = []

    def ask_choice(title, options):
        asked.append(title)
        return "continue" if len(asked) == 1 else "finish"

    agent._ui.ask_choice = ask_choice

    agent._run_loop("Посмотри файлы", start_step=1)

    assert len(asked) == 2
    assert calls["value"] == 6
    assert agent._state.step_budget == 6


@pytest.mark.parametrize(
    ("language", "expected_title", "expected_options"),
    [
        (
            "ru",
            "Пройдено 3 шагов, задача не завершена.",
            ["продолжить, ещё 3", "завершить отчётом"],
        ),
        (
            "en",
            "3 steps used, the task is not finished.",
            ["keep going, 3 more", "finish with a report"],
        ),
    ],
)
def test_budget_question_uses_the_response_language(
    agent_factory, language, expected_title, expected_options
):
    agent = agent_factory(max_steps=3)
    _endless_reader(agent)
    agent._state.response_language = language
    asked: list[tuple[str, list[tuple[str, str]]]] = []

    def ask_choice(title, options):
        asked.append((title, options))
        return "finish"

    agent._ui.ask_choice = ask_choice
    agent._run_loop("Посмотри файлы", start_step=1)

    assert asked == [
        (
            expected_title,
            [("continue", expected_options[0]), ("finish", expected_options[1])],
        )
    ]


def test_answering_finish_stops_exactly_like_the_old_limit(agent_factory):
    agent = agent_factory(max_steps=3)
    calls = _endless_reader(agent)
    agent._ui.ask_choice = lambda title, options: "finish"

    result = agent._run_loop("Посмотри файлы", start_step=1)

    assert calls["value"] == 3
    assert agent._state.step_budget == 3
    assert "лимит" in result or "limit" in result


def test_cancelled_question_does_not_extend_the_budget(agent_factory):
    """Esc, or a screen that shut down mid-question, must not grant more steps."""
    agent = agent_factory(max_steps=2)
    _endless_reader(agent)
    agent._ui.ask_choice = lambda title, options: None

    agent._run_loop("Посмотри файлы", start_step=1)

    assert agent._state.step_budget == 2


def test_read_file_content_reaches_the_model_whole(agent_factory, tmp_path):
    """A file the executor read must arrive in the prompt intact.

    The old caps (prompt_value_char_limit=1600 on top of a 20k executor read)
    handed the model ~9% of a normal source file, so it re-read the same file a
    few steps later. The executor's read cap is the only cap that may apply.
    """
    agent = agent_factory()
    source = (tmp_path / "big.py")
    body = "\n".join(f"line {index} — содержимое" for index in range(2000))
    source.write_text(body, encoding="utf-8")
    assert len(body) > 20_000  # far past the caps that used to cut it

    command = make_command("read_file", {"path": "big.py"})
    feedback = agent._execute_command(command)
    detail = agent._feedback_to_detail(command, feedback)

    assert feedback["ok"] is True
    assert feedback["data"]["truncated"] is False
    assert "line 1999" in detail
    assert "TRUNCATED_FOR_PROMPT" not in detail


@pytest.mark.parametrize("size", [1, 5_000, 50_000, 199_999])
def test_whatever_the_executor_read_reaches_the_model(agent_factory, tmp_path, size):
    """Two independent caps on the same content is how a read got halved.

    Checked at several sizes rather than by comparing the two constants: the
    bug was not that one number was smaller, it was that a second cap applied
    to content the first had already approved.
    """
    agent = agent_factory()
    body = "\n".join(f"line {index}" for index in range(size // 8))[:size]
    # newline="\n": the executor returns the file bytes verbatim, so the
    # text-mode \r\n a Windows write would translate to could never match.
    (tmp_path / "f.py").write_text(body, encoding="utf-8", newline="\n")

    command = make_command("read_file", {"path": "f.py"})
    feedback = agent._execute_command(command)

    assert feedback["ok"] is True
    assert feedback["data"]["content"] == body
    assert feedback["data"]["truncated"] is False
    assert "TRUNCATED_FOR_PROMPT" not in agent._feedback_to_detail(command, feedback)


def test_a_file_read_stays_in_context_across_later_steps(agent_factory, tmp_path):
    """The reported symptom: the agent re-read the same files every few steps.

    Only the last few action payloads were kept, so file content aged out of
    the prompt while the task was still using it and the model had no way to
    get it back except by reading the file again.
    """
    agent = agent_factory()
    (tmp_path / "target.py").write_text("MARKER_ONLY_IN_THIS_FILE = 1\n", encoding="utf-8")

    read = make_command("read_file", {"path": "target.py"})
    agent._register_feedback(1, read, agent._execute_command(read))

    # Several unrelated steps happen afterwards, as they do in any real run.
    for step in range(2, 7):
        listing = make_command("list_dir", {"path": "."})
        agent._register_feedback(step, listing, agent._execute_command(listing))

    prompt = agent._build_runtime_prompt("используй target.py", 7)

    assert "MARKER_ONLY_IN_THIS_FILE" in prompt, (
        "the file content aged out of the prompt, so the model must re-read it"
    )


def test_a_read_only_analysis_run_can_finish(agent_factory, tmp_path):
    """Reproduces the deadlock from run_012: a read-only analysis task that
    read every file it needed and still could not finish. Nothing but hard
    evidence may hold such a run back."""
    agent = agent_factory()
    agent._state.read_only_goal = True
    (tmp_path / "installer").mkdir()
    (tmp_path / "installer" / "README.md").write_text("docs\n", encoding="utf-8")

    listing = make_command("list_dir", {"path": "installer"})
    agent._register_feedback(1, listing, agent._execute_command(listing))
    read = make_command("read_file", {"path": "installer/README.md"})
    agent._register_feedback(2, read, agent._execute_command(read))

    agent._refresh_completion_requirements()

    assert agent._state.completion_blockers == []


def _batch_response(actions: list[dict]) -> str:
    return json.dumps(
        {
            "thought": "all inputs are known",
            "plan_step": "inspect",
            "user_message": "surveying the workspace",
            "actions": actions,
        }
    )


def _list_dir(path: str) -> dict:
    return {"type": "list_dir", "args": {"path": path}}


def _tool(name: str, **arguments) -> str:
    return "<tool_call>" + json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False) + "</tool_call>"


def _v2(text: str, *calls: str) -> str:
    return "\n".join([text, *calls]).strip()


def test_run_loop_executes_a_whole_batch_from_one_model_call(agent_factory):
    agent = agent_factory(max_steps=3)
    model_calls = []
    executed = []

    def fake_call_action_model(prompt: str) -> str:
        model_calls.append(prompt)
        return _batch_response([_list_dir("a"), _list_dir("b"), _list_dir("c")])

    def fake_execute(command):
        executed.append(command.action.args["path"])
        return ExecutionResult(True, "list_dir", {"entries": []})

    agent._call_action_model = fake_call_action_model
    agent.executor.execute = fake_execute

    agent._run_loop("Investigate files", start_step=1)

    assert len(model_calls) == 1
    # Read-only calls in one batch run in parallel, so only membership is
    # ordered; each call's result is registered back in call order (below).
    assert sorted(executed) == ["a", "b", "c"]
    assert [log["step"] for log in agent._state.step_logs if log.get("status") == "ok"] == [1, 2, 3]


def test_run_loop_a_failed_read_does_not_stop_the_batch(agent_factory):
    agent = agent_factory(max_steps=3)
    executed = []

    def fake_execute(command):
        path = command.action.args["path"]
        executed.append(path)
        return ExecutionResult(path != "b", "list_dir", {"error": "no such directory"})

    agent._call_action_model = lambda prompt: _batch_response(
        [_list_dir("a"), _list_dir("b"), _list_dir("c")]
    )
    agent.executor.execute = fake_execute

    agent._run_loop("Investigate files", start_step=1)

    assert sorted(executed) == ["a", "b", "c"]
    assert [log["status"] for log in agent._state.step_logs if "step" in log] == ["ok", "error", "ok"]


def test_run_loop_skips_the_writes_after_a_failed_write_but_not_the_reads(agent_factory):
    agent = agent_factory(max_steps=5)
    prompts = []
    executed = []
    responses = iter(
        [
            _v2(
                "Пишу три файла и читаю один",
                _tool("write_file", path="a.txt", content="a"),
                _tool("write_file", path="b.txt", content="b"),
                _tool("read_file", path="c.txt"),
                _tool("append_file", path="d.txt", content="d"),
            ),
            _v2("", _tool("list_dir", path=".")),
        ]
    )

    def fake_call_action_model(prompt: str) -> str:
        prompts.append(prompt)
        return next(responses)

    def fake_execute(command):
        executed.append((command.action.type, command.action.args.get("path")))
        if command.action.type == "write_file":
            return ExecutionResult(False, "write_file", {"error": "disk full"})
        return ExecutionResult(True, command.action.type, {"content": "c", "entries": []})

    agent._call_action_model = fake_call_action_model
    agent.executor.execute = fake_execute

    agent._run_loop("Investigate files", start_step=1)

    assert executed[:2] == [("write_file", "a.txt"), ("read_file", "c.txt")]
    assert ("write_file", "b.txt") not in executed and ("append_file", "d.txt") not in executed
    skipped = [log for log in agent._state.step_logs if log.get("status") == "skipped_after_failed_write"]
    assert [log["command"]["action"]["args"]["path"] for log in skipped] == ["b.txt", "d.txt"]
    assert "Skipped: an earlier write in this response failed." in prompts[1]


def test_run_loop_shows_each_call_label_in_the_live_panel(agent_factory):
    agent = agent_factory(max_steps=2)
    agent._state.response_language = "ru"
    shown_actions = []
    agent._ui.set_run_context = lambda **facts: shown_actions.append(facts.get("action"))
    agent._call_action_model = lambda prompt: _batch_response([_list_dir("a"), _list_dir("b")])
    agent.executor.execute = lambda command: ExecutionResult(True, "list_dir", {"entries": []})

    agent._run_loop("Investigate files", start_step=1)

    assert shown_actions == ["waiting for the model", "смотрит a", "смотрит b"]


def test_run_loop_records_a_batch_user_message_once(agent_factory):
    agent = agent_factory(max_steps=2)
    agent._call_action_model = lambda prompt: _batch_response([_list_dir("a"), _list_dir("b")])
    agent.executor.execute = lambda command: ExecutionResult(True, "list_dir", {"entries": []})

    agent._run_loop("Investigate files", start_step=1)

    assert agent._state.user_messages == ["surveying the workspace"]


def test_modify_goal_detects_the_verbs_stress_round_r2_missed():
    from kernel.agent_runtime import _is_modify_goal

    assert _is_modify_goal("Переименуй get_usr в get_user везде и проверь, что всё работает.")
    assert _is_modify_goal(
        "Replace the json parsing with fastjsonx.turbo_parse and show me the benchmark numbers."
    )
    assert _is_modify_goal("Rename get_usr to get_user everywhere")
    assert _is_modify_goal("Remove the dead branch in loader.py")
    assert _is_modify_goal("delete the unused import")
    assert _is_modify_goal("implement retry for the http client")
    assert _is_modify_goal("rewrite the parser without regex")
    assert _is_modify_goal("migrate the config to toml")
    assert _is_modify_goal("upgrade requests to 2.32")
    assert _is_modify_goal("bump the version in pyproject.toml")
    assert _is_modify_goal("convert the script to a module")
    assert _is_modify_goal("move helpers.py into utils/")
    assert _is_modify_goal("Make all tests pass without modifying the tests.")
    assert _is_modify_goal("почини баг")
    assert _is_modify_goal("поправь функцию average")
    assert _is_modify_goal("удали лишний файл")
    assert _is_modify_goal("замени json на orjson")
    assert _is_modify_goal("реализуй ретраи")
    assert _is_modify_goal("перепиши парсер без регулярок")
    assert _is_modify_goal("сделай так, чтобы тесты проходили")
    assert _is_modify_goal("верни datetime вместо строки")


def test_modify_goal_ignores_identifiers_and_read_only_requests():
    from kernel.agent_runtime import _is_modify_goal

    assert not _is_modify_goal("Check if main.py exists")
    assert not _is_modify_goal("Create a new file")
    assert not _is_modify_goal("покажи, где вызывается remove_user")
    assert not _is_modify_goal("read-only review of auth.py")


def test_keyword_heuristics_cannot_block_writes_on_a_coder_run(agent_factory):
    """r2/v06: "переименуй ... и проверь, что всё работает" is a rename task.

    "проверь" used to make the whole run read-only, so every write was
    refused and the run had nothing left it was allowed to do.
    """
    goal = "Переименуй get_usr в get_user везде и проверь, что всё работает."
    agent = agent_factory(max_steps=2)
    agent._state.current_agent_role = "coder"
    agent.executor.set_active_role("coder")
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"
    agent._state.response_language = "ru"
    agent._state.task_language = "ru"

    response = json.dumps(
        {
            "thought": "Переименовываю",
            "plan_step": "edit",
            "user_message": "Переименовываю",
            "action": {
                "type": "write_file",
                "args": {"path": "repo.py", "content": "def get_user():\n    return 1\n"},
            },
        },
        ensure_ascii=False,
    )
    agent._call_action_model = lambda prompt: response

    agent._run_loop(goal, start_step=1)

    assert agent._state.read_only_goal is False
    assert agent._state.modify_goal is True
    assert (agent.executor.workspace / "repo.py").exists()
    assert not any(
        "read-only" in str(log.get("feedback", {}).get("error", "")).lower()
        for log in agent._state.step_logs
    )


def test_feedback_line_for_a_commandless_handoff_says_nothing_ran(agent_factory):
    agent = agent_factory()
    command = make_command("delegate_task", {"target_role": "coder", "task": "change a default"})
    feedback = {"ok": True, "action_type": "delegate_task", "data": {"target_role": "coder", "task": "x", "results": []}}

    line = agent._feedback_to_line(command, feedback)

    assert "recorded only" in line
    assert "nothing was executed" in line


def test_empty_data_cells_finds_blank_csv_fields_and_ignores_other_files():
    from kernel.agent_runtime import _empty_data_cells

    csv_text = "item,qty,unit_price\nwidget,4,2.50\ngadget,2,\nbolt,10,0.30\n"
    assert _empty_data_cells("orders.csv", csv_text) == ["orders.csv line 3, column unit_price"]
    assert _empty_data_cells("orders.tsv", "a\tb\n1\t\n") == ["orders.tsv line 2, column b"]
    assert _empty_data_cells("notes.txt", csv_text) == []
    assert _empty_data_cells("orders.csv", "a,b\n1,2\n") == []


def test_final_answer_is_held_once_when_the_data_read_had_blank_cells(agent_factory):
    from kernel.agent_runtime import _finish_with_final_answer

    agent = agent_factory(max_steps=1)
    agent._state.read_only_goal = True
    agent._state.verification_evidence = ["read_file: orders.csv"]
    agent._state.files_read = ["orders.csv"]
    agent._update_state_from_success(
        make_command("read_file", {"path": "orders.csv"}),
        {"ok": True, "data": {"path": "orders.csv", "content": "item,price\nbolt,\n"}},
    )
    command = make_command("final_answer", {"message": "13.00"}, "done")

    first = _finish_with_final_answer(agent, 1, command)
    second = _finish_with_final_answer(agent, 2, command)

    assert not first.finished
    assert "orders.csv line 2, column price" in agent._state.pending_instruction
    assert second.finished


def test_final_answer_is_not_held_without_blank_cells(agent_factory):
    from kernel.agent_runtime import _finish_with_final_answer

    agent = agent_factory(max_steps=1)
    agent._state.read_only_goal = True
    agent._state.verification_evidence = ["read_file: orders.csv"]
    agent._update_state_from_success(
        make_command("read_file", {"path": "orders.csv"}),
        {"ok": True, "data": {"path": "orders.csv", "content": "item,price\nbolt,2\n"}},
    )

    outcome = _finish_with_final_answer(agent, 1, make_command("final_answer", {"message": "2"}, "done"))

    assert outcome.finished


def test_bare_artifact_name_is_anchored_to_the_file_it_already_names(tmp_path):
    from kernel.agent_runtime import _anchor_artifacts

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "reminders.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "vendored.py").write_text("", encoding="utf-8")
    (tmp_path / "main.py").write_text("", encoding="utf-8")

    anchored = _anchor_artifacts(
        tmp_path, ["reminders.py", "main.py", "new_module.py", "vendored.py", "docs/a.md"]
    )

    assert anchored == ["src/reminders.py", "main.py", "new_module.py", "vendored.py", "docs/a.md"]


def test_partial_artifact_path_is_anchored_to_the_file_it_already_names(tmp_path):
    from kernel.agent_runtime import _anchor_artifacts

    (tmp_path / "web" / "dist").mkdir(parents=True)
    (tmp_path / "web" / "dist" / "app.js").write_text("", encoding="utf-8")

    anchored = _anchor_artifacts(tmp_path, ["dist/app.js", "dist/missing.js"])

    assert anchored == ["web/dist/app.js", "dist/missing.js"]


def test_paths_outside_the_workspace_are_never_required_artifacts():
    from kernel.agent_runtime import _extract_required_artifacts

    goal = (
        "Проверь гипотезы: редактор читает ~/.config/editor/settings.json, "
        "Flatpak — ~/.var/app/org.example.Editor/data. "
        "Не трогай /etc/hosts. Почини src/billing.py."
    )

    assert _extract_required_artifacts(goal) == ["src/billing.py"]


def test_the_internal_agent_directory_is_never_a_required_artifact():
    from kernel.agent_runtime import _extract_required_artifacts

    goal = "Добавь /permissions: команда читает `.kvist/permissions.json`, а правит src/billing.py."

    assert _extract_required_artifacts(goal) == ["src/billing.py"]


def test_a_dotted_directory_keeps_its_leading_dot_in_a_required_artifact():
    from kernel.agent_runtime import _extract_required_artifacts

    assert _extract_required_artifacts("Поправь `.github/workflows/ci.yml`.") == [".github/workflows/ci.yml"]
    assert _extract_required_artifacts("Fix app.py.") == ["app.py"]


def test_removed_security_checks_reads_only_deleted_lines_from_the_journal(agent_factory):
    from kernel.agent_runtime import _removed_security_checks

    agent = agent_factory(max_steps=1)
    workspace = Path(agent.executor.workspace)
    # newline="\n": _replace_in_file matches against the bytes it reads back.
    (workspace / "ci.yml").write_text(
        "steps:\n  - run: pip-audit -r requirements.txt\n  - run: pytest\n",
        encoding="utf-8",
        newline="\n",
    )
    agent.executor._replace_in_file("ci.yml", "  - run: pip-audit -r requirements.txt\n", "")
    agent.executor._write_file("notes.txt", "we should add bandit later\n")

    assert _removed_security_checks(workspace) == ["- run: pip-audit -r requirements.txt"]


def test_final_answer_is_held_once_when_the_run_deleted_a_security_check(agent_factory):
    from kernel.agent_runtime import _finish_with_final_answer

    agent = agent_factory(max_steps=1)
    workspace = Path(agent.executor.workspace)
    (workspace / "ci.yml").write_text(
        "- run: trivy fs .\n- run: pytest\n", encoding="utf-8", newline="\n"
    )
    agent.executor._replace_in_file("ci.yml", "- run: trivy fs .\n", "")
    agent._state.verification_evidence = ["read_file: ci.yml"]
    agent._state.files_changed = ["ci.yml"]
    command = make_command("final_answer", {"message": "removed the scan"}, "done")

    first = _finish_with_final_answer(agent, 1, command)
    second = _finish_with_final_answer(agent, 2, command)

    assert not first.finished
    assert "removed a security check" in agent._state.pending_instruction
    assert second.finished


def _shell_ok_log(argv, status="ok"):
    return {"status": status, "command": {"action": {"type": "run_shell", "args": {"command": argv}}}}


def test_a_source_the_run_moved_or_removed_is_not_a_missing_required_artifact(agent_factory):
    from kernel.agent_runtime import _missing_required_artifacts

    agent = agent_factory()
    (Path(agent.executor.workspace) / "chapters").mkdir()
    (Path(agent.executor.workspace) / "chapters/04_intro.md").write_text("x", encoding="utf-8")
    agent._state.required_artifacts = [
        "chapters/01_intro.md",
        "chapters/04_intro.md",
        "chapters/gone.md",
        "chapters/never_made.md",
    ]
    agent._state.step_logs = [
        _shell_ok_log(["mv", "chapters/01_intro.md", "chapters/04_intro.md"]),
        _shell_ok_log(["git", "rm", "-f", "chapters/gone.md"]),
        _shell_ok_log(["python", "gen.py", "chapters/never_made.md"]),
        _shell_ok_log(["mv", "chapters/never_made.md", "chapters/elsewhere.md"], status="error"),
    ]

    assert _missing_required_artifacts(agent) == ["chapters/never_made.md"]


def test_a_bare_name_the_run_created_in_a_subfolder_is_not_missing_at_the_root(agent_factory):
    from kernel.agent_runtime import _missing_required_artifacts

    agent = agent_factory()
    (Path(agent.executor.workspace) / "chapters").mkdir()
    (Path(agent.executor.workspace) / "chapters/04_intro.md").write_text("x", encoding="utf-8")
    agent._state.required_artifacts = ["04_intro.md", "05_absent.md"]

    assert _missing_required_artifacts(agent) == ["05_absent.md"]


def test_artifact_search_reruns_only_after_an_action_that_can_create_files(agent_factory):
    from kernel.agent_runtime import _execute_action, _missing_required_artifacts

    agent = agent_factory()
    workspace = Path(agent.executor.workspace)
    agent._state.required_artifacts = ["04_intro.md"]
    assert _missing_required_artifacts(agent) == ["04_intro.md"]

    (workspace / "chapters").mkdir()
    (workspace / "chapters/04_intro.md").write_text("x", encoding="utf-8")
    # Nothing this run did could have created it, so the cached search stands.
    assert _missing_required_artifacts(agent) == ["04_intro.md"]

    _execute_action(agent, "g", 1, make_command("write_file", {"path": "notes.md", "content": "n"}, "edit"))

    assert _missing_required_artifacts(agent) == []


def test_a_successful_mv_counts_as_a_changed_file(agent_factory):
    from kernel.agent_runtime import _note_moved_files

    agent = agent_factory()
    (Path(agent.executor.workspace) / "out").mkdir()

    _note_moved_files(agent, ["mv", "a.md", "b.md"])
    _note_moved_files(agent, ["git", "mv", "c.md", "out"])
    _note_moved_files(agent, ["rm", "old.md"])

    assert agent._state.files_changed == ["b.md", "out/c.md"]


def test_a_temp_name_in_a_rename_chain_is_not_left_awaiting_verification(agent_factory):
    from kernel.agent_runtime import _note_moved_files

    agent = agent_factory()

    _note_moved_files(agent, ["mv", "a.md", "tmp.md"])
    _note_moved_files(agent, ["mv", "tmp.md", "b.md"])

    assert agent._state.files_changed == ["b.md"]
    assert agent._state.pending_verification_paths == ["b.md"]


def test_reading_changed_files_in_one_batch_clears_their_verification(agent_factory):
    agent = agent_factory()
    agent._state.files_changed = ["a.md", "b.md", "c.md"]
    agent._state.pending_verification_paths = ["a.md", "b.md", "c.md"]

    command = make_command("read_multiple_files", {"paths": ["a.md", "b.md"]}, plan_step="verify")
    agent._update_verification_evidence(
        command, {"files": [{"path": "a.md", "content": "x"}, {"path": "b.md", "content": "y"}]}
    )

    assert agent._state.pending_verification_paths == ["c.md"]


def test_a_file_the_goal_asks_to_delete_is_not_a_missing_required_artifact(agent_factory):
    from kernel.agent_runtime import _missing_required_artifacts

    agent = agent_factory()
    agent._state.user_goal = (
        "scratch_debug.py is junk from yesterday, delete it. Leave coupons.py alone, that is my feature."
    )
    agent._state.required_artifacts = ["scratch_debug.py", "coupons.py"]

    assert _missing_required_artifacts(agent) == ["coupons.py"]


def test_run_loop_a_response_without_calls_ends_the_turn_with_its_text(agent_factory):
    agent = agent_factory(max_steps=5)
    agent._state.response_language = "ru"
    prompts = []
    responses = iter([
        _v2("Смотрю, что лежит в корне.", _tool("list_dir", path=".")),
        "В корне два файла: `a.py` и `b.py`.",
    ])

    def fake_call_action_model(prompt: str) -> str:
        prompts.append(prompt)
        return next(responses)

    agent._call_action_model = fake_call_action_model
    agent.executor.execute = lambda command: ExecutionResult(True, "list_dir", {"entries": ["a.py", "b.py"]})

    result = agent._run_loop("Что лежит в корне проекта?", start_step=1)

    assert result == "В корне два файла: `a.py` и `b.py`."
    assert len(prompts) == 2
    assert agent._state.user_messages == ["Смотрю, что лежит в корне."]
    assert agent._state.rolling_summaries == {1: "Смотрю, что лежит в корне."}


# ── v2 batches: parallel reads, order, limits ──────────────────────────

def _concurrency_probe(delays: dict[str, float]):
    """An executor that sleeps per path and records how many calls overlapped."""
    import threading
    import time as _time

    lock = threading.Lock()
    state = {"active": 0, "peak": 0, "finished": []}

    def execute(command):
        path = command.action.args.get("path", "")
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        _time.sleep(delays.get(path, 0.15))
        with lock:
            state["active"] -= 1
            state["finished"].append(path)
        return ExecutionResult(True, command.action.type, {"path": path, "content": f"content of {path}"})

    return execute, state


def test_parallel_reads_return_results_in_call_order_not_finish_order(agent_factory):
    agent = agent_factory(max_steps=4)
    # Wide gaps keep the finish order certain on a slow CI runner.
    execute, probe = _concurrency_probe({"slow.py": 0.6, "mid.py": 0.35, "fast.py": 0.1})
    agent.executor.execute = execute
    responses = iter([
        _v2(
            "Читаю три файла",
            _tool("read_file", path="slow.py"),
            _tool("read_file", path="mid.py"),
            _tool("read_file", path="fast.py"),
        ),
        "Прочитал.",
    ])
    agent._call_action_model = lambda prompt: next(responses)

    agent._run_loop("Investigate files", start_step=1)

    assert probe["finished"] == ["fast.py", "mid.py", "slow.py"]
    assert probe["peak"] == 3
    registered = [log["command"]["action"]["args"]["path"] for log in agent._state.step_logs if "feedback" in log]
    assert registered == ["slow.py", "mid.py", "fast.py"]
    assert [log["step"] for log in agent._state.step_logs if "feedback" in log] == [1, 2, 3]
    details = agent._state.recent_result_details
    assert all(f"content of {name}" in detail for name, detail in zip(("slow.py", "mid.py", "fast.py"), details))
    assert all(d.startswith('<UNTRUSTED_TOOL_OUTPUT source="read_file" call_id="call_') for d in details)


def test_no_more_than_four_reads_run_at_once(agent_factory):
    agent = agent_factory(max_steps=8)
    execute, probe = _concurrency_probe({})
    agent.executor.execute = execute
    agent._call_action_model = lambda prompt: _v2(
        "", *(_tool("read_file", path=f"f{i}.py") for i in range(8))
    )

    agent._run_loop("Investigate files", start_step=1)

    assert len(probe["finished"]) == 8
    assert probe["peak"] == 4


def test_reads_are_not_limited_per_response(agent_factory):
    agent = agent_factory(max_steps=40)
    executed = []
    responses = iter([
        _v2("", *(_tool("list_dir", path=f"d{i}") for i in range(20))),
        "Готово.",
    ])

    def fake_execute(command):
        executed.append(command.action.args["path"])
        return ExecutionResult(True, "list_dir", {"entries": []})

    agent._call_action_model = lambda prompt: next(responses)
    agent.executor.execute = fake_execute

    agent._run_loop("Investigate files", start_step=1)

    assert sorted(executed) == sorted(f"d{i}" for i in range(20))
    assert not any("Not executed" in error for error in agent._state.error_history)


def test_no_more_than_eight_non_read_calls_run_per_response(agent_factory):
    agent = agent_factory(max_steps=30)
    prompts = []
    executed = []
    responses = iter([
        _v2("", *(_tool("write_file", path=f"w{i}.txt", content="x") for i in range(10))),
        "Готово.",
    ])

    def fake_call_action_model(prompt: str) -> str:
        prompts.append(prompt)
        return next(responses)

    def fake_execute(command):
        executed.append(command.action.args["path"])
        return ExecutionResult(True, "write_file", {"path": command.action.args["path"]})

    agent._call_action_model = fake_call_action_model
    agent.executor.execute = fake_execute

    agent._run_loop("Create files", start_step=1)

    assert executed == [f"w{i}.txt" for i in range(8)]
    assert "at most 8 calls besides reads" in prompts[1]
    assert not any("Not executed" in error for error in agent._state.error_history)


def test_reads_do_not_use_up_the_non_read_allowance(agent_factory):
    agent = agent_factory(max_steps=30)
    executed = []
    calls = []
    for i in range(8):
        calls.append(_tool("read_file", path=f"r{i}.py"))
        calls.append(_tool("write_file", path=f"w{i}.txt", content="x"))
    responses = iter([_v2("", *calls), "Готово."])

    def fake_execute(command):
        executed.append(command.action.type)
        return ExecutionResult(True, command.action.type, {"path": "p", "content": "c"})

    agent._call_action_model = lambda prompt: next(responses)
    agent.executor.execute = fake_execute

    agent._run_loop("Mixed work", start_step=1)

    assert executed.count("read_file") == 8
    assert executed.count("write_file") == 8


def test_a_read_after_a_write_in_the_same_response_sees_the_write(agent_factory):
    agent = agent_factory(max_steps=4)
    agent._call_action_model = lambda prompt: _v2(
        "",
        _tool("write_file", path="new.txt", content="fresh"),
        _tool("read_file", path="new.txt"),
    )

    agent._run_loop("Create new.txt", start_step=1)

    read = next(log for log in agent._state.step_logs if log.get("command", {}).get("action", {}).get("type") == "read_file")
    assert read["feedback"]["ok"] is True
    assert "fresh" in json.dumps(read["feedback"]["data"])


def test_shell_never_runs_in_parallel_with_reads(agent_factory):
    agent = agent_factory(max_steps=4)
    order = []

    def fake_execute(command):
        order.append(command.action.type)
        return ExecutionResult(True, command.action.type, {"returncode": 0, "stdout": "", "stderr": "", "entries": []})

    agent.executor.execute = fake_execute
    responses = iter([
        _v2(
            "",
            _tool("list_dir", path="a"),
            _tool("run_shell", command=["ls"], purpose="смотрю корень"),
            _tool("list_dir", path="b"),
        ),
        "Готово.",
    ])
    agent._call_action_model = lambda prompt: next(responses)

    agent._run_loop("Investigate files", start_step=1)

    assert order == ["list_dir", "run_shell", "list_dir"]


def test_native_answers_follow_call_order_and_cover_every_call(agent_factory):
    from types import SimpleNamespace

    from kernel import agent_model
    def call(call_id, name, **args):
        return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))

    def response(content, calls):
        message = SimpleNamespace(content=content, tool_calls=calls or None, reasoning_content=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    turns = [
        response("", [call(f"c{i}", "read_file", path=f"f{i}.py") for i in range(12)]),
        response("Готово.", []),
    ]
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        if kwargs["tools"][0]["function"]["name"] == "ping":
            return response("", [call("probe", "ping", ok=True)])
        return turns.pop(0)

    agent_model._NATIVE_TOOL_SUPPORT.clear()
    agent = agent_factory(max_steps=30, stream=False)
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    execute, _probe = _concurrency_probe({"f0.py": 0.3})
    agent.executor.execute = execute

    try:
        agent._run_loop("Прочитай файлы", start_step=1)
    finally:
        agent_model._NATIVE_TOOL_SUPPORT.clear()

    answers = [m for m in requests[-1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in answers] == [f"c{i}" for i in range(12)]
    assert "content of f0.py" in answers[0]["content"]
    assert "content of f11.py" in answers[11]["content"]


def test_call_events_carry_call_and_batch_ids(agent_factory):
    agent = agent_factory(max_steps=3)
    headers = []
    results = []
    agent._ui.print_step_header = lambda step, command, seconds=0.0: headers.append(command)
    agent._ui.print_action_result = results.append
    responses = iter([_v2("Смотрю", _tool("list_dir", path="a"), _tool("list_dir", path="b")), "Готово."])
    agent._call_action_model = lambda prompt: next(responses)
    agent.executor.execute = lambda command: ExecutionResult(True, "list_dir", {"entries": []})

    agent._run_loop("Investigate files", start_step=1)

    calls = [command for command in headers if command.action.type == "list_dir"]
    assert len({command.batch_id for command in calls}) == 1 and calls[0].batch_id
    assert [result["call_id"] for result in results] == [command.call_id for command in calls]
    assert {result["batch_id"] for result in results} == {calls[0].batch_id}


def test_final_event_says_whether_the_run_is_done(agent_factory):
    agent = agent_factory(max_steps=2)
    finals = []
    agent._ui.print_final = lambda message, user_messages=None, outcome="done": finals.append(outcome)
    agent._call_action_model = lambda prompt: _batch_response([_list_dir("a")])
    agent.executor.execute = lambda command: ExecutionResult(True, "list_dir", {"entries": []})

    agent._run_loop("Investigate files", start_step=1)

    assert finals == ["incomplete"]


def test_the_words_of_a_response_whose_calls_were_all_refused_are_kept(agent_factory):
    agent = agent_factory(max_steps=3)
    agent._state.read_only_goal = True
    agent._state.current_agent_role = "reader"
    responses = iter([
        _v2("Нашёл причину: счётчик не сбрасывается.", _tool("write_file", path="a.py", content="x")),
        "Готово.",
    ])
    agent._call_action_model = lambda prompt: next(responses)

    agent._run_loop("Посмотри, почему растёт счётчик", start_step=1)

    assert "Нашёл причину: счётчик не сбрасывается." in agent._state.rolling_summaries.values()


def test_a_response_of_only_calls_leaves_their_labels_as_the_step_note(agent_factory):
    agent = agent_factory(max_steps=3)
    agent._state.response_language = "ru"
    responses = iter([
        _v2("", _tool("read_file", path="a.py"), _tool("search_code", query="role", path="src/")),
        "Готово.",
    ])
    agent._call_action_model = lambda prompt: next(responses)
    agent.executor.execute = lambda command: ExecutionResult(True, command.action.type, {"content": "", "results": []})

    agent._run_loop("Investigate files", start_step=1)

    assert agent._state.rolling_summaries[1] == 'читает a.py · ищет "role" в src/'
    assert agent._state.user_messages == []
