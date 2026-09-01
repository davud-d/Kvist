# -*- coding: utf-8 -*-
"""Regression tests for the run_001 failure.

run_001.json recorded a FIX task in which the agent replaced a 381-line
main.py with a 31-line invented stub, read its own hallucination back as
verification, ignored a `@@ -1,381 +1,31 @@` diff, and reported success.

Every test here pins one link of that chain. They all fail on the code as it
was before the P0 patches.
"""
from __future__ import annotations

import json

import pytest

from agent_state import PlanInfo
from executor import ExecutorError, SafeExecutor
from protocol import Action, AgentCommand

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
    (workspace / "main.py").write_text(text, encoding="utf-8")
    return text


def _fix_plan() -> PlanInfo:
    return PlanInfo(
        goal="Исправить смену раскладки после вставки текста",
        complexity="complex",
        plan=[
            "найти код вставки текста",
            "прочитать найденный файл",
            "внести минимальную правку",
            "проверить результат",
        ],
        success_criteria=["раскладка не меняется после вставки"],
        risks=[],
    )


def _command(action_type: str, args: dict, plan_step: str = "edit") -> AgentCommand:
    return AgentCommand(
        thought="step under test",
        plan_step=plan_step,
        user_message="работаю",
        action=Action(type=action_type, args=args),
    )


def _unified_diff(removed: int, added: int) -> str:
    return (
        "diff --git a/main.py b/main.py\n"
        "index ebfd7df..be09556 100644\n"
        "--- a/main.py\n"
        "+++ b/main.py\n"
        f"@@ -1,{removed} +1,{added} @@\n"
        + "".join(f"-line_{i} = {i}\n" for i in range(removed))
        + "".join(f"+stub_{i} = {i}\n" for i in range(added))
    )


# ── P0.1 destructive overwrite guard ────────────────────────────────

def test_write_file_rejects_mass_deletion(tmp_path):
    """The exact write from run_001 step 9 must not reach the filesystem."""
    executor = SafeExecutor(tmp_path)
    original = _write_source(executor.workspace)

    with pytest.raises(ExecutorError) as excinfo:
        executor.execute(_command("write_file", {"path": "main.py", "content": HALLUCINATED_STUB}))

    message = str(excinfo.value)
    assert "381" in message
    assert "replace_in_file" in message
    assert (executor.workspace / "main.py").read_text(encoding="utf-8") == original


def test_destructive_guard_ignores_write_approval_flag(tmp_path, monkeypatch):
    """The guard is a machine check, not a HITL prompt. Disabling the gate
    must not disable the guard."""
    monkeypatch.setenv("AGENT_WRITE_APPROVAL", "0")
    executor = SafeExecutor(tmp_path)
    assert executor.write_approval_enabled is False
    _write_source(executor.workspace)

    with pytest.raises(ExecutorError):
        executor.execute(_command("write_file", {"path": "main.py", "content": HALLUCINATED_STUB}))


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
    """Losing under half the lines is a legitimate refactor, not destruction."""
    executor = SafeExecutor(tmp_path)
    _write_source(executor.workspace, lines=100)
    result = executor.execute(_command("write_file", {"path": "main.py", "content": _source(60)}))
    assert result.ok


def test_replace_in_file_cannot_mass_delete(tmp_path):
    """replace_in_file with a high count is the other route to the same damage."""
    executor = SafeExecutor(tmp_path)
    block = "def handler():\n    pass\n\n"
    original = block * 60  # 180 lines
    (executor.workspace / "main.py").write_text(original, encoding="utf-8")

    with pytest.raises(ExecutorError):
        executor.execute(_command("replace_in_file", {
            "path": "main.py",
            "old_text": block,
            "new_text": "",
            "count": 1000,
        }))
    assert (executor.workspace / "main.py").read_text(encoding="utf-8") == original


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


# ── P0.2 read-only escape from the corrective block ─────────────────

def test_read_file_allowed_during_corrective_block(agent_factory):
    """run_001 steps 6-8: three read_file attempts, three refusals.

    The runtime demanded a write while the system prompt demanded a prior
    read. That contradiction is what produced the blind overwrite.
    """
    agent = agent_factory()
    agent._state.plan_info = _fix_plan()
    agent._set_required_next_actions(
        {"write_file", "append_file", "replace_in_file"},
        "this looks like a FIX task but no files were changed",
    )

    read_cmd = _command("read_file", {"path": "main.py"}, plan_step="inspect")
    assert agent._required_followup_error(read_cmd) is None


def test_search_code_allowed_during_corrective_block(agent_factory):
    agent = agent_factory()
    agent._set_required_next_actions({"write_file"}, "make a change")
    cmd = _command("search_code", {"query": "insert_text"}, plan_step="inspect")
    assert agent._required_followup_error(cmd) is None


def test_corrective_read_budget_is_finite(agent_factory):
    """The escape hatch must not become an inspect loop."""
    agent = agent_factory()
    agent._set_required_next_actions({"write_file"}, "make a change")
    read_cmd = _command("read_file", {"path": "main.py"}, plan_step="inspect")

    allowed = sum(1 for _ in range(10) if agent._required_followup_error(read_cmd) is None)
    assert allowed == 3
    assert agent._required_followup_error(read_cmd) is not None


def test_corrective_read_budget_refills_on_new_block(agent_factory):
    agent = agent_factory()
    agent._set_required_next_actions({"write_file"}, "make a change")
    read_cmd = _command("read_file", {"path": "main.py"}, plan_step="inspect")
    for _ in range(3):
        agent._required_followup_error(read_cmd)
    assert agent._required_followup_error(read_cmd) is not None

    agent._set_required_next_actions({"write_file"}, "still need a change")
    assert agent._required_followup_error(read_cmd) is None


def test_non_read_actions_still_blocked(agent_factory):
    """The hatch is for inspection only. Completion stays blocked."""
    agent = agent_factory()
    agent._set_required_next_actions({"write_file"}, "make a change")
    final_cmd = _command("final_answer", {"message": "готово"}, plan_step="done")
    assert agent._required_followup_error(final_cmd) is not None


# ── P0.3 blocked actions must not consume the repeat budget ─────────

def test_blocked_action_does_not_consume_repeat_budget(agent_factory):
    """run_001 steps 14-16: verification reads were refused as repeats,
    because the three refusals at steps 6-8 had already spent the budget."""
    agent = agent_factory()
    agent._state.plan_info = _fix_plan()

    read_cmd = _command("read_file", {"path": "main.py"}, plan_step="inspect")
    signature = agent._action_signature(read_cmd)

    for expected in (1, 2, 3, 4, 5, 6):
        assert agent._register_blocked_action(read_cmd) == expected

    # The refusal counter moved, the repeat budget did not.
    assert agent._state.blocked_actions[signature] == 6
    assert agent._state.seen_actions.get(signature, 0) == 0

    # repeat_action_limit defaults to 4, so a pre-patch run would block here.
    assert agent._is_repeat_blocked(read_cmd) is False


def test_blocked_action_bookkeeping_is_wired_into_the_loop(agent_factory):
    """The extracted helper must actually be the one the run loop calls.

    git_diff is neither the required action nor a read-only inspection, so it
    lands in the corrective branch on every step instead of being executed.
    """
    agent = agent_factory(max_steps=3)
    agent._state.plan_info = _fix_plan()
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"
    agent._set_required_next_actions({"write_file"}, "make a change first")

    def fake_call_action_model(prompt: str) -> str:
        return json.dumps({
            "thought": "verifying before changing anything",
            "plan_step": "verify",
            "progress": 85,
            "user_message": "проверяю",
            "action": {"type": "git_diff", "args": {"path": "main.py"}},
        }, ensure_ascii=False)

    agent._call_action_model = fake_call_action_model
    agent._run_loop("исправь смену раскладки после вставки текста", start_step=1)

    blocked_logs = [
        log for log in agent._state.step_logs
        if log.get("status") == "blocked_for_corrective_action"
    ]
    assert blocked_logs, "the corrective guard never fired"

    git_diff_signatures = [
        signature for signature in agent._state.blocked_actions
        if "git_diff" in signature
    ]
    assert git_diff_signatures, "blocked git_diff was not recorded"
    for signature in git_diff_signatures:
        assert agent._state.seen_actions.get(signature, 0) == 0


# ── P1.1 the trailing-period fix ────────────────────────────────────

def test_fragment_not_found_unlocks_read(agent_factory):
    """run_001 step 6 raised this error and the recovery path never fired,
    because the runtime compared against a punctuated variant."""
    agent = agent_factory()
    agent._state.plan_info = _fix_plan()

    cmd = _command("replace_in_file", {
        "path": "main.py",
        "old_text": "# TODO: implement keyboard layout indicator",
        "new_text": "# fixed",
    })
    agent._register_feedback(1, cmd, {
        "ok": False,
        "action_type": "replace_in_file",
        "normalized_error": "old_text fragment not found in file",
    })

    assert "read_file" in agent._state.required_next_actions


# ── P1.3 diff sanity check ──────────────────────────────────────────

def test_mass_deletion_diff_is_not_verification(agent_factory):
    """The `@@ -1,381 +1,31 @@` from run_001 step 17 was recorded as proof."""
    agent = agent_factory()
    agent._state.plan_info = _fix_plan()
    agent._state.modify_goal = True
    agent._push_unique(agent._state.files_changed, "main.py")
    agent._push_unique(agent._state.pending_verification_paths, "main.py")

    cmd = _command("git_diff", {"path": "main.py"}, plan_step="verify")
    agent._update_verification_evidence(cmd, {
        "returncode": 0,
        "command": ["git", "diff", "--", "main.py"],
        "stdout": _unified_diff(removed=381, added=31),
    })

    assert agent._state.verification_evidence == []
    assert agent._state.destructive_diff
    assert "381" in agent._state.destructive_diff
    assert agent._state.pending_verification_paths == ["main.py"]

    agent._refresh_completion_requirements(force_enforcement=True)
    assert any("destruction" in blocker for blocker in agent._state.completion_blockers)
    assert agent._state.completion_ready is False


def test_targeted_diff_still_counts_as_verification(agent_factory):
    agent = agent_factory()
    agent._push_unique(agent._state.files_changed, "main.py")
    agent._push_unique(agent._state.pending_verification_paths, "main.py")

    cmd = _command("git_diff", {"path": "main.py"}, plan_step="verify")
    agent._update_verification_evidence(cmd, {
        "returncode": 0,
        "stdout": (
            "diff --git a/main.py b/main.py\n--- a/main.py\n+++ b/main.py\n"
            "@@ -10,3 +10,4 @@\n-old = 1\n+new = 1\n+extra = 2\n"
        ),
    })

    assert "git_diff" in agent._state.verification_evidence
    assert agent._state.destructive_diff == ""
    assert agent._state.pending_verification_paths == []


def test_clean_diff_clears_destructive_flag(agent_factory):
    """Restoring the file must be a way out of the block."""
    agent = agent_factory()
    agent._state.destructive_diff = "previous destruction"
    cmd = _command("git_diff", {"path": "main.py"}, plan_step="verify")
    agent._update_verification_evidence(cmd, {"returncode": 0, "stdout": ""})
    assert agent._state.destructive_diff == ""


def test_destructive_block_permits_honest_report(agent_factory):
    """Leaving only mutation allowed here would repeat the original trap."""
    agent = agent_factory()
    agent._state.plan_info = _fix_plan()
    agent._state.destructive_diff = "git_diff shows 381 lines removed and 31 added."
    agent._refresh_completion_requirements(force_enforcement=True)

    assert "final_answer" in agent._state.required_next_actions
    assert "read_file" in agent._state.required_next_actions
    final_cmd = _command("final_answer", {"message": "Я удалил файл"}, plan_step="done")
    assert agent._required_followup_error(final_cmd) is None


# ── Golden replay of the whole failure ──────────────────────────────

def test_run_001_replay_never_destroys_main_py(agent_factory):
    """Feed the runtime the run_001 action sequence verbatim.

    main.py must survive and the run must not close with a success claim.
    """
    agent = agent_factory(max_steps=20)
    original = _write_source(agent.executor.workspace)
    agent._state.plan_info = _fix_plan()
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
