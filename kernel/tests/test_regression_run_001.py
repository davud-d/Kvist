# -*- coding: utf-8 -*-
"""Regression coverage for run_001 with model-owned completion.

The agent can stop before an unnecessary rewrite. When a rewrite is requested,
normal approval and the recovery journal protect existing files; line counts
alone do not decide whether the requested change is legitimate.
"""
from __future__ import annotations

import json

import pytest

from kernel.sandbox.executor import ExecutorError, SafeExecutor
from kernel.protocol.protocol import Action, AgentCommand

# The stub the agent invented in run_001, trimmed to the shape that matters.
HALLUCINATED_STUB = (
    "import sys\n"
    "import pyperclip\n"
    "from pynput import keyboard\n"
    "\n"
    "class LayoutIndicator:\n"
    "    def __init__(self):\n"
    "        self.current_layout = 'en'\n"
    "\n"
    "    def handle_paste(self):\n"
    "        text = pyperclip.paste()\n"
    "        if text:\n"
    "            self.update_indicator()\n"
)


def _source(lines: int = 381) -> str:
    return "\n".join(f"line_{i} = {i}" for i in range(lines)) + "\n"


def _write_source(workspace, lines: int = 381) -> str:
    text = _source(lines)
    # Bytes, not write_text: on Windows text mode writes CRLF, and the journal keeps the file's real endings.
    (workspace / "main.py").write_bytes(text.encode("utf-8"))
    return text


def _command(action_type: str, args: dict, plan_step: str = "edit") -> AgentCommand:
    return AgentCommand(plan_step=plan_step, action=Action(type=action_type, args=args), text="работаю")


@pytest.mark.parametrize("approved", [False, True])
def test_large_rewrite_uses_normal_approval_and_keeps_recovery(tmp_path, approved):
    from kernel.session.change_journal import load_baseline, revert_workspace

    executor = SafeExecutor(tmp_path)
    original = _write_source(tmp_path)
    executor.write_approval_enabled = True
    decisions = []

    def confirm(**request):
        decisions.append(request)
        return approved

    executor._approvals.confirm = confirm
    command = _command("write_file", {"path": "main.py", "content": HALLUCINATED_STUB})
    if not approved:
        with pytest.raises(ExecutorError, match="denied by user"):
            executor.execute(command)
        assert (tmp_path / "main.py").read_text() == original
        assert load_baseline(tmp_path) == {}
    else:
        assert executor.execute(command).ok
        assert (tmp_path / "main.py").read_text() == HALLUCINATED_STUB
        assert load_baseline(tmp_path)["main.py"]["content"] == original
        revert_workspace(tmp_path)
        assert (tmp_path / "main.py").read_text() == original
    assert len(decisions) == 1
    assert "main.py" in decisions[0]["body"]


def test_write_file_allows_new_file(tmp_path):
    executor = SafeExecutor(tmp_path)
    result = executor.execute(_command("write_file", {"path": "fresh.py", "content": "x = 1\n"}))
    assert result.ok
    assert (executor.workspace / "fresh.py").exists()


def test_write_file_allows_small_file_rewrite(tmp_path):
    executor = SafeExecutor(tmp_path)
    (executor.workspace / "tiny.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    result = executor.execute(_command("write_file", {"path": "tiny.py", "content": "a = 1\n"}))
    assert result.ok


def test_write_file_allows_partial_shrink(tmp_path):
    """An approved reduction is allowed regardless of its line-count ratio."""
    executor = SafeExecutor(tmp_path)
    _write_source(executor.workspace, lines=100)
    result = executor.execute(_command("write_file", {"path": "main.py", "content": _source(60)}))
    assert result.ok


def test_large_targeted_deletion_is_allowed_with_recovery(tmp_path):
    from kernel.session.change_journal import revert_workspace

    executor = SafeExecutor(tmp_path)
    block = "def unused():\n    pass\n\n"
    original = block * 60 + "VALUE = 42\n"
    (tmp_path / "main.py").write_text(original, encoding="utf-8", newline="\n")

    assert executor.execute(_command("replace_in_file", {
        "path": "main.py", "old_text": block, "new_text": "", "count": 1000,
    })).ok

    assert (tmp_path / "main.py").read_text() == "VALUE = 42\n"
    revert_workspace(tmp_path)
    assert (tmp_path / "main.py").read_text() == original


def test_replace_in_file_allows_targeted_patch(tmp_path):
    executor = SafeExecutor(tmp_path)
    _write_source(executor.workspace, lines=200)
    result = executor.execute(_command("replace_in_file", {
        "path": "main.py",
        "old_text": "line_7 = 7",
        "new_text": "line_7 = 42",
        "count": 1,
    }))
    assert result.ok
    assert "line_7 = 42" in (executor.workspace / "main.py").read_text(encoding="utf-8")


# ── Golden replay of the whole failure ──────────────────────────────

def test_run_001_replay_never_destroys_main_py(agent_factory):
    """Feed the runtime the run_001 action sequence verbatim.

    The model reports the missing implementation before the unnecessary rewrite.
    """
    agent = agent_factory(max_steps=20)
    original = _write_source(agent.executor.workspace)
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"

    actions = [
        {"type": "find_files", "args": {"pattern": "*layout*", "path": "."}},
        {"type": "find_files", "args": {"pattern": "*indicator*", "path": "."}},
        {"type": "find_files", "args": {"pattern": "*keyboard*", "path": "."}},
        {"type": "final_answer", "args": {"message": "В проекте нет кода индикатора"}},
        {"type": "replace_in_file", "args": {
            "path": "main.py",
            "old_text": "# TODO: implement keyboard layout indicator",
            "new_text": "# FIXED",
        }},
        {"type": "write_file", "args": {"path": "main.py", "content": HALLUCINATED_STUB}},
        {"type": "read_file", "args": {"path": "main.py"}},
        {"type": "git_diff", "args": {"path": "main.py"}},
        {"type": "final_answer", "args": {"message": "Исправлено: индикатор обновляется после вставки"}},
    ]
    cursor = {"value": 0}

    def fake_call_action_model(prompt: str) -> str:
        index = min(cursor["value"], len(actions) - 1)
        cursor["value"] += 1
        return json.dumps({
            "thought": "replaying run_001",
            "plan_step": "edit",
            "progress": 60,
            "user_message": "работаю",
            "action": actions[index],
        }, ensure_ascii=False)

    agent._call_action_model = fake_call_action_model
    result = agent._run_loop("исправь смену раскладки после вставки текста", start_step=1)

    surviving = (agent.executor.workspace / "main.py").read_text(encoding="utf-8")
    assert surviving == original, "main.py was destroyed during the replay"
    assert "LayoutIndicator" not in surviving
    assert isinstance(result, str) and result
