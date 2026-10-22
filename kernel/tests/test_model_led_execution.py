"""Regression scenarios for model-owned workflow and completion.

The runtime still enforces authorization and resource limits. Task keywords,
filenames, repetition and verification bookkeeping must never dictate work.
"""
from __future__ import annotations

import json

import pytest

from kernel.tests.conftest import make_command


def _call(name, **arguments):
    return "<tool_call>" + json.dumps({"name": name, "arguments": arguments}) + "</tool_call>"


def _script(agent, responses):
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"
    prompts = []
    answers = iter(responses)

    def respond(prompt):
        prompts.append(prompt)
        return next(answers)

    agent._call_action_model = respond
    return prompts


def test_build_after_inspection_can_finish_without_source_edits(agent_factory):
    agent = agent_factory(max_steps=10)
    workspace = agent.executor.workspace
    (workspace / "build.py").write_text("# existing build entry\n", encoding="utf-8")
    (workspace / "README.md").write_text("Run the build.\n", encoding="utf-8")
    real_execute = agent._execute_command

    def build(command):
        if command.action.type != "run_shell":
            return real_execute(command)
        (workspace / "app.bin").write_bytes(b"binary artifact")
        return {"ok": True, "action_type": "run_shell", "data": {
            "command": command.action.args["command"], "returncode": 0,
            "stdout": "Built app.bin", "stderr": "",
        }}

    agent._execute_command = build
    prompts = _script(agent, [
        _call("read_file", path="README.md"),
        _call("read_file", path="build.py"),
        _call("run_shell", command=["python", "build.py"]),
        "Built app.bin successfully.",
    ])

    assert agent._run_loop("Build the binary", 1) == "Built app.bin successfully."
    assert len(prompts) == 4
    assert agent._state.files_changed == []
    assert agent._state.run_status == "completed"
    assert (workspace / "app.bin").read_bytes() == b"binary artifact"
    assert sorted(p.name for p in workspace.iterdir() if p.name != ".kvist") == ["README.md", "app.bin", "build.py"]
    assert all("Allowed next actions" not in prompt for prompt in prompts)


def test_create_can_ask_a_question_after_two_reads(agent_factory):
    agent = agent_factory(max_steps=8)
    for name in ("a.txt", "b.txt"):
        (agent.executor.workspace / name).write_text("context", encoding="utf-8")
    real_execute = agent._execute_command
    asked = []

    def execute(command):
        if command.action.type == "ask_user":
            asked.append(command.action.args["question"])
            return {"ok": True, "data": {"answer": "Use JSON"}}
        return real_execute(command)

    agent._execute_command = execute
    _script(agent, [_call("read_file", path="a.txt"), _call("read_file", path="b.txt"),
                    _call("ask_user", question="Which output format?"), "The format is JSON."])

    assert agent._run_loop("Create an export; ask about the format first", 1) == "The format is JSON."
    assert asked == ["Which output format?"]


def test_same_test_command_runs_after_each_new_edit(agent_factory):
    agent = agent_factory(max_steps=30, repeat_action_limit=2)
    (agent.executor.workspace / "app.py").write_text("value = 0\n", encoding="utf-8")
    real_execute = agent._execute_command
    runs = []

    def execute(command):
        if command.action.type == "run_shell":
            runs.append(command.action.args["command"])
            return {"ok": True, "data": {"command": runs[-1], "returncode": 0}}
        return real_execute(command)

    agent._execute_command = execute
    responses = [_call("read_file", path="app.py")]
    for value in range(1, 7):
        responses += [_call("replace_in_file", path="app.py", old_text=f"value = {value-1}", new_text=f"value = {value}"),
                      _call("run_shell", command=["npm", "test"])]
    responses.append("Finished the requested changes.")
    _script(agent, responses)

    assert agent._run_loop("Update app.py", 1) == "Finished the requested changes."
    assert runs == [["npm", "test"]] * 6
    assert (agent.executor.workspace / "app.py").read_text() == "value = 6\n"
    assert agent._state.stagnation_count == 0


def test_revising_one_file_does_not_exhaust_a_write_quota(agent_factory):
    agent = agent_factory(max_steps=20, repeat_action_limit=2)
    responses = []
    for value in range(7):
        responses += [_call("write_file", path="generated.json", content=json.dumps({"value": value})),
                      _call("read_file", path="generated.json")]
    responses.append("Generated the final JSON.")
    _script(agent, responses)

    agent._run_loop("Generate JSON", 1)

    assert json.loads((agent.executor.workspace / "generated.json").read_text()) == {"value": 6}
    assert agent._state.run_status == "completed"


@pytest.mark.parametrize("path", ["src/status.json", "fixtures/result.json", "docs/status.md", "task_report.txt"])
def test_file_names_do_not_decide_what_the_agent_may_write(agent_factory, path):
    agent = agent_factory(max_steps=4)
    _script(agent, [_call("write_file", path=path, content="requested content"), "Saved."])

    assert agent._run_loop("Create the requested application files", 1) == "Saved."
    assert (agent.executor.workspace / path).read_text() == "requested content"


def test_replacing_an_old_filename_reference_does_not_create_that_file(agent_factory):
    agent = agent_factory(max_steps=6)
    (agent.executor.workspace / "README.md").write_text("Use old_config.json\n", encoding="utf-8")
    _script(agent, [_call("read_file", path="README.md"),
                    _call("replace_in_file", path="README.md", old_text="old_config.json", new_text="config.json"),
                    "Updated the reference."])

    assert agent._run_loop("Replace references to old_config.json with config.json in README.md", 1) == "Updated the reference."
    assert agent._state.run_status == "completed"
    assert not (agent.executor.workspace / "old_config.json").exists()
    assert not (agent.executor.workspace / "config.json").exists()


def test_no_change_can_be_reported_from_inspection_without_a_token_command(agent_factory):
    agent = agent_factory(max_steps=4)
    (agent.executor.workspace / "app.py").write_text("TIMEOUT = 30\n", encoding="utf-8")
    answer = "TIMEOUT is already 30; no change is needed."
    _script(agent, [_call("read_file", path="app.py"), json.dumps({"plan_step": "done", "action": {
        "type": "final_answer", "args": {"message": answer, "outcome": "no_change_needed"},
    }})])

    assert agent._run_loop("Change TIMEOUT to 30 in app.py", 1) == answer
    assert agent._state.no_change_declared
    assert agent._state.last_shell_returncode is None
    assert agent._state.run_status == "completed"


@pytest.mark.parametrize("outcome", ["done", "incomplete", "no_change_needed"])
def test_reporter_preserves_the_models_declared_outcome(agent_factory, monkeypatch, outcome):
    from kernel.agent import agent_runtime
    agent = agent_factory(max_steps=4)
    contexts = []
    monkeypatch.setattr(agent_runtime, "_render_report", lambda self, context: contexts.append(json.loads(context)) or "Reported.")
    _script(agent, [json.dumps({"plan_step": "done", "action": {"type": "report_to_user", "args": {
        "summary": "Result", "result": "No source edits", "outcome": outcome,
    }}})])

    assert agent._run_loop("Build the binary", 1) == "Reported."
    assert contexts[0]["outcome"] == ("incomplete" if outcome == "incomplete" else "done")
    assert agent._state.run_status == ("blocked" if outcome == "incomplete" else "completed")


def test_old_checkpoint_does_not_restore_workflow_restrictions(agent_factory):
    agent = agent_factory()
    agent._state.required_next_actions = ["write_file"]
    agent._state.required_artifacts = ["obsolete.json"]
    agent._state.completion_blockers = ["Missing edit"]
    agent._state.pending_instruction = "You must write a file next."
    agent._save_checkpoint("Build the binary", 2)
    restarted = agent_factory(max_steps=4)
    assert restarted._load_checkpoint() == ("Build the binary", 2)
    prompts = _script(restarted, ["The build is already available."])

    assert restarted._run_loop("Build the binary", 3) == "The build is already available."
    assert restarted._state.run_status == "completed"
    assert restarted._state.required_next_actions == []
    assert "must write a file" not in prompts[0]


def test_repeated_successful_calls_still_stop_at_the_step_budget(agent_factory):
    agent = agent_factory(max_steps=5, repeat_action_limit=1)
    _script(agent, [_call("list_dir", path=".")] * 5)

    result = agent._run_loop("Inspect the workspace", 1)

    assert "step limit" in result
    assert len(agent._state.step_logs) == 5
    assert all(log["status"] == "ok" for log in agent._state.step_logs)
    assert agent._state.run_status == "blocked"


def test_short_factual_answer_is_not_replaced_with_a_failure(agent_factory):
    agent = agent_factory(max_steps=2)
    _script(agent, ["The timeout is 30 seconds."])

    assert agent._run_loop("Explain the timeout", 1) == "The timeout is 30 seconds."
    assert agent._state.run_status == "completed"
