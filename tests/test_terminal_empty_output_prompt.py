from __future__ import annotations

from agent_model import _load_role_prompt
from tests.conftest import make_command


def test_run_shell_empty_streams_are_explicit_in_model_feedback(agent_factory):
    agent = agent_factory()
    command = make_command("run_shell", {"command": ["test", "1", "=", "1"]})
    feedback = {
        "ok": True,
        "action_type": "run_shell",
        "data": {
            "command": ["test", "1", "=", "1"],
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "untrusted": True,
        },
    }

    detail = agent._feedback_to_detail(command, feedback)

    assert feedback["data"]["stdout"] == ""
    assert feedback["data"]["stderr"] == ""
    assert '"stdout": "no output"' in detail
    assert '"stderr": "no output"' in detail
    assert '<UNTRUSTED_TOOL_OUTPUT source="run_shell">' in detail


def test_delegate_task_empty_terminal_streams_are_explicit_in_model_feedback(agent_factory):
    agent = agent_factory()
    command = make_command(
        "delegate_task",
        {"target_role": "coder", "task": "check", "commands": ["test 1 = 1"]},
    )
    feedback = {
        "ok": True,
        "action_type": "delegate_task",
        "data": {
            "target_role": "coder",
            "task": "check",
            "results": [
                {
                    "command": "test 1 = 1",
                    "ok": True,
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "",
                    "denied": False,
                }
            ],
        },
    }

    detail = agent._feedback_to_detail(command, feedback)

    assert feedback["data"]["results"][0]["stdout"] == ""
    assert feedback["data"]["results"][0]["stderr"] == ""
    assert '"stdout": "no output"' in detail
    assert '"stderr": "no output"' in detail


def test_role_prompts_explain_no_output_marker():
    for role in ("coder", "devops"):
        prompt = _load_role_prompt(role)
        assert '"no output" is a runtime marker for an empty stream' in prompt
