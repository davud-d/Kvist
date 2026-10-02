# -*- coding: utf-8 -*-
from __future__ import annotations

import json

import pytest

from kernel.action_result import MAX_OUTPUT_CHARS, build_action_result
from kernel.event_stream_ui import EventStreamUI
from kernel.executor import ExecutionResult
from kernel.frontend_protocol import ActionResultEvent, FrontendChannel, RouteEvent
from kernel.role_router import RoleRouteResult
from terminal_banner import TerminalBannerUI
from kernel.tests.conftest import FakeLLMClient, NullUI, make_command

SYNTHETIC_KEY = "sk-proj-" + "q" * 24


class RecordingUI(NullUI):
    def __init__(self):
        self.results: list[dict] = []
        self.routes: list[tuple[str, str]] = []

    def print_action_result(self, result):
        self.results.append(result)

    def print_route(self, role, source):
        self.routes.append((role, source))


def _ok(action_type: str, data: dict) -> dict:
    return {"ok": True, "action_type": action_type, "data": data}


def _build(action_type: str, args: dict, feedback: dict) -> dict:
    return build_action_result(3, action_type, args, feedback)


# --- build_action_result: one case per action type, from real executor shapes ---


def test_write_file_result_reports_lines_path_and_first_line():
    result = _build(
        "write_file",
        {"path": "a.py", "content": "x\ny\nz\n"},
        _ok("write_file", {"path": "a.py", "bytes_written": 6, "first_line": 1}),
    )
    assert result == {
        "step": 3,
        "action_type": "write_file",
        "ok": True,
        "summary": "Wrote 3 lines",
        "output": "",
        "exit_code": None,
        "path": "a.py",
        "first_line": 1,
        "call_id": "",
        "batch_id": "",
    }


def test_result_names_its_call_and_batch():
    from kernel.action_result import build_action_result

    result = build_action_result(
        2, "read_file", {"path": "a.py"}, _ok("read_file", {"content": "x"}), call_id="call_1", batch_id="b1"
    )

    assert (result["call_id"], result["batch_id"]) == ("call_1", "b1")


def test_a_skipped_write_says_so_instead_of_failed():
    from kernel.action_result import SKIPPED_ERROR, build_action_result

    result = build_action_result(
        4, "write_file", {"path": "b.py", "content": "x"}, {"ok": False, "error": SKIPPED_ERROR}
    )

    assert result["summary"] == "Skipped: an earlier write failed"


def test_append_and_replace_results():
    appended = _build(
        "append_file",
        {"path": "a.py", "content": "one line\n"},
        _ok("append_file", {"path": "a.py", "bytes_appended": 9, "first_line": 8}),
    )
    assert appended["summary"] == "Appended 1 line"
    assert appended["first_line"] == 8

    replaced = _build(
        "replace_in_file",
        {"path": "a.py", "old_text": "a", "new_text": "b"},
        _ok(
            "replace_in_file",
            {"path": "a.py", "occurrences_found": 2, "occurrences_replaced": 2, "first_line": 5},
        ),
    )
    assert replaced["summary"] == "Replaced 2 occurrences"
    assert replaced["first_line"] == 5


def test_failed_write_keeps_path_but_has_no_first_line():
    result = _build(
        "replace_in_file",
        {"path": "a.py", "old_text": "a", "new_text": "b"},
        {"ok": False, "action_type": "replace_in_file", "error": "old_text fragment not found in file"},
    )
    assert result["ok"] is False
    assert result["summary"] == "Failed: old_text fragment not found in file"
    assert result["output"] == "old_text fragment not found in file"
    assert result["path"] == "a.py"
    assert result["first_line"] is None


def test_run_shell_result_carries_exit_code_and_streams():
    result = _build(
        "run_shell",
        {"command": ["pytest"]},
        {
            "ok": False,
            "action_type": "run_shell",
            "error": "boom",
            "data": {"returncode": 2, "stdout": "collected 3\n", "stderr": "1 failed\n", "timed_out": False},
        },
    )
    assert result["summary"] == "Command exited with code 2"
    assert result["exit_code"] == 2
    assert result["output"] == "collected 3\n1 failed"
    assert result["path"] is None


def test_run_shell_denied_and_timeout_have_no_exit_code():
    denied = _build(
        "run_shell",
        {"command": ["rm"]},
        {"ok": False, "action_type": "run_shell", "error": "hard deny", "data": {"denied": True, "error": "hard deny"}},
    )
    assert denied["summary"] == "Command denied"
    assert denied["exit_code"] is None
    assert denied["output"] == "hard deny"

    timed_out = _build(
        "run_shell",
        {"command": ["sleep"]},
        {"ok": False, "action_type": "run_shell", "error": "t", "data": {"returncode": -9, "stdout": "", "stderr": "", "timed_out": True}},
    )
    assert timed_out["summary"] == "Command timed out"


@pytest.mark.parametrize(
    ("action_type", "data", "summary"),
    [
        ("read_file", {"path": "a", "content": "a\nb\n", "truncated": False}, "Read 2 lines"),
        ("read_file", {"path": "a", "content": "a", "truncated": True}, "Read 1 line (truncated)"),
        (
            "read_file",
            {"path": "a", "content": "b\n", "start_line": 2, "end_line": 2, "total_lines": 9, "truncated": False},
            "Read lines 2-2 of 9",
        ),
        ("read_multiple_files", {"files": [{}, {}]}, "Read 2 files"),
        ("search_code", {"results": [{}, {}], "truncated": True}, "2 matches (truncated)"),
        ("find_files", {"matches": ["a"], "truncated": True}, "1 match (truncated)"),
        ("list_dir", {"path": "", "items": [{}, {}, {}]}, "3 entries"),
        ("list_tree", {"path": "", "tree": "a\nb", "truncated": False}, "Listed 2 lines"),
        ("find_files", {"matches": ["a"]}, "1 match"),
        ("search_code", {"results": [{}, {}]}, "2 matches"),
        ("web_search", {"results": [{}, {}, {}]}, "3 results"),
        ("fetch_url", {"content": "abcd", "truncated": False}, "Fetched 4 characters"),
        ("git_status", {"git_available": True, "stdout": "## main\n"}, "Working tree clean"),
        ("git_status", {"git_available": True, "stdout": "## main\n M a\n?? b\n"}, "2 changed files"),
        ("git_status", {"git_available": False, "stdout": ""}, "Not a git repository"),
        ("git_diff", {"stdout": ""}, "No changes"),
        ("git_diff", {"stdout": "diff --git a/x b/x\n+1\ndiff --git a/y b/y\n"}, "2 files in diff"),
        ("delegate_task", {"results": []}, "Handoff recorded, nothing run"),
        ("delegate_task", {"results": [{"ok": True}, {"ok": False}]}, "1 of 2 commands succeeded"),
    ],
)
def test_summaries_for_read_only_and_delegating_actions(action_type, data, summary):
    result = _build(action_type, {}, _ok(action_type, data))
    assert result["summary"] == summary
    assert result["exit_code"] is None
    assert result["first_line"] is None


def test_git_diff_output_is_the_diff_text():
    diff = "diff --git a/x b/x\n-old\n+new\n"
    result = _build("git_diff", {}, _ok("git_diff", {"stdout": diff, "stderr": ""}))
    assert result["output"] == diff.rstrip()


def test_delegate_task_output_lists_each_command():
    data = {
        "results": [
            {"command": "ls", "ok": True, "stdout": "a\n", "stderr": ""},
            {"command": "nope", "ok": False, "error": "Empty"},
        ]
    }
    result = _build("delegate_task", {}, _ok("delegate_task", data))
    assert result["output"] == "$ ls\na\n$ nope\nEmpty"


def test_output_is_redacted_before_it_leaves_the_core():
    result = _build(
        "run_shell",
        {"command": ["env"]},
        _ok("run_shell", {"returncode": 0, "stdout": f"api_key={SYNTHETIC_KEY}\n", "stderr": ""}),
    )
    assert SYNTHETIC_KEY not in result["output"]
    assert "[REDACTED]" in result["output"]


def test_long_output_is_cut_in_the_middle_with_a_marker():
    text = "HEAD" + "x" * 20000 + "TAIL"
    result = _build(
        "run_shell", {"command": ["make"]}, _ok("run_shell", {"returncode": 0, "stdout": text, "stderr": ""})
    )
    output = result["output"]
    assert len(output) < MAX_OUTPUT_CHARS + 100
    assert output.startswith("HEAD")
    assert output.endswith("TAIL")
    assert "characters omitted" in output


def test_summary_is_a_single_bounded_line():
    error = "line one\nline two " + "y" * 500
    result = _build("read_file", {}, {"ok": False, "action_type": "read_file", "error": error})
    assert "\n" not in result["summary"]
    assert len(result["summary"]) <= 120


# --- executor reports first_line for real writes ---


def test_executor_reports_first_line_for_each_write(agent_factory):
    executor = agent_factory().executor
    written = executor.execute(make_command("write_file", {"path": "f.txt", "content": "a\nb\nc\n"}))
    assert written.data["first_line"] == 1

    appended = executor.execute(make_command("append_file", {"path": "f.txt", "content": "d\n"}))
    assert appended.data["first_line"] == 4

    replaced = executor.execute(
        make_command("replace_in_file", {"path": "f.txt", "old_text": "c\nd", "new_text": "C\nD"})
    )
    assert replaced.data["first_line"] == 3

    created = executor.execute(make_command("append_file", {"path": "new.txt", "content": "x"}))
    assert created.data["first_line"] == 1


def test_append_to_a_file_without_trailing_newline_starts_on_its_last_line(agent_factory):
    executor = agent_factory().executor
    executor.execute(make_command("write_file", {"path": "f.txt", "content": "a\nb"}))
    appended = executor.execute(make_command("append_file", {"path": "f.txt", "content": "c"}))
    assert appended.data["first_line"] == 2


# --- run_loop emits action_result after each executed action ---


def _script_loop(agent, responses):
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"
    queue = list(responses)
    agent._call_action_model = lambda prompt: json.dumps(queue.pop(0))


def _step(action_type: str, args: dict, plan_step: str = "edit") -> dict:
    return {
        "thought": "t",
        "plan_step": plan_step,
        "user_message": "Working",
        "action": {"type": action_type, "args": args},
    }


def test_run_loop_emits_one_action_result_per_executed_action(agent_factory):
    agent = agent_factory(max_steps=8)
    agent._ui = ui = RecordingUI()
    _script_loop(
        agent,
        [
            _step("write_file", {"path": "hello.txt", "content": "one\ntwo\n"}),
            _step("append_file", {"path": "hello.txt", "content": "three\n"}),
            _step("replace_in_file", {"path": "hello.txt", "old_text": "two", "new_text": "2"}),
            _step("replace_in_file", {"path": "hello.txt", "old_text": "missing", "new_text": "x"}),
            _step("read_file", {"path": "hello.txt"}, plan_step="verify"),
            _step("final_answer", {"message": "done"}, plan_step="done"),
        ],
    )

    agent._run_loop("Edit hello.txt", start_step=1)

    assert [(r["step"], r["action_type"], r["ok"]) for r in ui.results] == [
        (1, "write_file", True),
        (2, "append_file", True),
        (3, "replace_in_file", True),
        (4, "replace_in_file", False),
        (5, "read_file", True),
    ]
    written, appended, replaced, failed, verified = ui.results
    assert verified["summary"] == "Read 3 lines"
    assert (written["summary"], written["path"], written["first_line"]) == ("Wrote 2 lines", "hello.txt", 1)
    assert (appended["summary"], appended["first_line"]) == ("Appended 1 line", 3)
    assert (replaced["summary"], replaced["first_line"]) == ("Replaced 1 occurrence", 2)
    assert failed["first_line"] is None
    assert "not found" in failed["output"]


def test_run_loop_shell_result_is_redacted_and_carries_exit_code(agent_factory):
    agent = agent_factory(max_steps=4)
    agent._ui = ui = RecordingUI()
    agent.executor.execute = lambda cmd: ExecutionResult(
        True,
        "run_shell",
        {"returncode": 0, "stdout": f"token={SYNTHETIC_KEY}\n", "stderr": "", "timed_out": False},
    )
    _script_loop(
        agent,
        [_step("run_shell", {"command": ["env"]}, plan_step="inspect"), _step("final_answer", {"message": "ok"}, "done")],
    )

    agent._run_loop("Inspect the environment", start_step=1)

    (result,) = ui.results
    assert result["exit_code"] == 0
    assert result["summary"] == "Command exited with code 0"
    assert SYNTHETIC_KEY not in result["output"]


def test_run_loop_without_the_optional_method_is_unaffected(agent_factory):
    agent = agent_factory(max_steps=4)
    _script_loop(
        agent,
        [
            _step("write_file", {"path": "a.txt", "content": "x"}),
            _step("read_file", {"path": "a.txt"}, "verify"),
            _step("final_answer", {"message": "done"}, "done"),
        ],
    )
    assert agent._run_loop("Write a.txt", start_step=1) == "done"


def test_classic_terminal_ui_has_no_action_result_or_route_channel():
    assert not hasattr(TerminalBannerUI, "print_action_result")
    assert not hasattr(TerminalBannerUI, "print_route")


# --- EventStreamUI ---


def test_event_stream_ui_emits_action_result_event_redacted():
    channel = FrontendChannel()
    ui = EventStreamUI(channel)
    ui.print_action_result(
        {
            "step": 2,
            "action_type": "run_shell",
            "ok": True,
            "summary": "Command exited with code 0",
            "output": f"password={SYNTHETIC_KEY}",
            "exit_code": 0,
            "path": None,
            "first_line": None,
        }
    )
    (event,) = channel.drain()
    assert isinstance(event, ActionResultEvent)
    payload = event.to_dict()
    assert payload["type"] == "action_result"
    assert SYNTHETIC_KEY not in json.dumps(payload)
    assert payload["exit_code"] == 0
    assert set(payload) == {
        "type", "step", "action_type", "ok", "summary", "output", "exit_code", "path", "first_line",
        "call_id", "batch_id",
    }


def test_event_stream_ui_emits_route_event():
    channel = FrontendChannel()
    EventStreamUI(channel).print_route("coder", "llm")
    (event,) = channel.drain()
    assert isinstance(event, RouteEvent)
    assert event.to_dict() == {"type": "route", "role": "coder", "source": "llm"}


# --- route: one event per turn, with the real source ---


def test_run_emits_a_single_explicit_route_event(agent_factory, monkeypatch):
    agent = agent_factory(stream=False)
    agent._ui = ui = RecordingUI()
    agent.client = FakeLLMClient(content="hi")
    monkeypatch.setattr("kernel.agent_model._load_role_prompt", lambda role: "chat prompt")

    agent.run("hello", routed_role="chat")

    assert ui.routes == [("chat", "explicit")]


def test_run_reports_the_routers_own_source(agent_factory, monkeypatch):
    agent = agent_factory(stream=False)
    agent._ui = ui = RecordingUI()
    agent.client = FakeLLMClient(content="hi")
    monkeypatch.setattr("kernel.agent_model._load_role_prompt", lambda role: "chat prompt")
    monkeypatch.setattr(
        "kernel.agent.route_role_decision_with_llm",
        lambda **kwargs: RoleRouteResult(role="chat", source="llm"),
    )

    agent.run("hello")

    assert ui.routes == [("chat", "llm")]


def test_router_failure_falls_back_to_keyword_source(agent_factory, monkeypatch):
    agent = agent_factory()

    def boom(**kwargs):
        raise RuntimeError("router down")

    monkeypatch.setattr("kernel.agent.route_role_decision_with_llm", boom)
    decision = agent._route_decision("fix the bug in main.py")
    assert decision.source == "keyword_fallback"


def test_bare_continue_after_a_stop_reports_resume_source(agent_factory):
    agent = agent_factory()
    agent._ui = ui = RecordingUI()
    agent._session_history = [{"role": "coder", "stopped_by_user": True, "goal": "x", "result": "y"}]
    agent._route_decision = lambda goal: RoleRouteResult(role="chat", source="llm")

    assert agent._choose_role("continue", None) == "coder"
    assert ui.routes == [("coder", "resume")]
