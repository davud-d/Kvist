from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


from kernel.agent import LocalAgent
from kernel.tests.conftest import last_json_line, requires_posix

pytestmark = pytest.mark.slow

# --- F4: current==intended is desired-state, NOT agent authorship -------------

def test_user_authored_content_matching_intent_is_not_owned_by_revert(agent_factory):
    from kernel.change_journal import content_digest, record_baseline, revert_workspace

    agent = agent_factory()
    ws = agent.executor.workspace
    (ws / "f.txt").write_text("A\n", encoding="utf-8", newline="")
    # Agent recorded intent to write B (write-ahead) but died BEFORE writing.
    record_baseline(ws, relative_path="f.txt", existed=True, content="A\n",
                    pending_digest=content_digest("B\n"))
    # The USER then writes exactly B.
    (ws / "f.txt").write_text("B\n", encoding="utf-8", newline="")

    # Resume must not replay, and must NOT claim ownership of B.
    agent._save_checkpoint("do it", 1, pending_action={
        "step": 1, "action_kind": "file", "path": "f.txt",
        "pre_digest": content_digest("A\n"), "intended_digest": content_digest("B\n"),
    })
    agent._load_checkpoint()
    start = agent._reconcile_pending_action(agent._pending_action)
    assert start == 2  # desired state present -> advance, no replay

    # /revert must NOT destroy the user's B.
    result = revert_workspace(ws)
    actions = {r.path: r.action for r in result}
    assert actions.get("f.txt") == "skipped"
    assert (ws / "f.txt").read_text(encoding="utf-8") == "B\n"


# --- F6: hardlink escape through a lexically-local journal path ----------------

@requires_posix
def test_changes_and_revert_reject_hardlink_to_outside_inode(tmp_path):
    from kernel.change_journal import build_diff_report, journal_path, revert_workspace

    workspace = tmp_path / "ws"
    workspace.mkdir()
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("ULTRA_SECRET_XYZ\n", encoding="utf-8", newline="")
    link = workspace / "f.txt"
    os.link(secret, link)  # hardlink inside the workspace to an outside inode

    journal = journal_path(workspace)
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(
        json.dumps({"version": 1, "baseline": {
            "f.txt": {"existed": True, "content": "REPLACEMENT\n",
                      "last_digest": __import__("hashlib").sha256(b"ULTRA_SECRET_XYZ\n").hexdigest()},
        }}),
        encoding="utf-8",
    )

    report = build_diff_report(workspace)
    assert "ULTRA_SECRET_XYZ" not in report

    reverted = revert_workspace(workspace)
    assert all(r.action == "skipped" for r in reverted)
    assert secret.read_text(encoding="utf-8") == "ULTRA_SECRET_XYZ\n"


# --- F7: .kvist rejection must be case-insensitive -----------------------------

def test_journal_path_violation_rejects_kvist_case_insensitively(tmp_path):
    from kernel.change_journal import journal_path_violation

    for variant in (".kvist", ".KVIST", ".Kvist", ".kViSt"):
        reason = journal_path_violation(tmp_path, f"{variant}/sessions/default.json")
        assert reason is not None, f"{variant} must be rejected"


# --- F8: promotion must be bound to the transaction digest --------------------

def test_reconcile_never_promotes_a_mismatched_journal_digest(agent_factory):
    from kernel.change_journal import content_digest, load_baseline, record_baseline

    agent = agent_factory()
    ws = agent.executor.workspace
    (ws / "g.txt").write_text("B\n", encoding="utf-8", newline="")  # current == intended B
    # Journal pending belongs to a DIFFERENT digest C.
    record_baseline(ws, relative_path="g.txt", existed=True, content="A\n",
                    pending_digest=content_digest("C\n"))

    agent._save_checkpoint("do it", 1, pending_action={
        "step": 1, "action_kind": "file", "path": "g.txt",
        "pre_digest": content_digest("A\n"), "intended_digest": content_digest("B\n"),
    })
    agent._load_checkpoint()
    agent._reconcile_pending_action(agent._pending_action)

    # C must never be promoted to last_digest (it is a different transaction).
    entry = load_baseline(ws)["g.txt"]
    assert entry.get("last_digest") != content_digest("C\n")


# --- F1: non-reconcilable side effects are never replayed ---------------------

_SIDE_EFFECT_CRASH = r'''
import json, os, sys
from pathlib import Path
from kernel.agent import LocalAgent
from kernel.agent_state import AgentConfig
from kernel.executor import SafeExecutor
from kernel.tests.conftest import FakeLLMClient, NullUI

ws = Path(sys.argv[1])
counter = ws / "counter.txt"
agent = LocalAgent(FakeLLMClient(), SafeExecutor(ws),
                   AgentConfig(persist_logs=False, api_request_interval=0, max_steps=6),
                   ui=NullUI())
agent.new_session("work")
agent._run_id = "run-1"
# A completed prior step leaves a base checkpoint, so /resume has somewhere to
# come back to. Without the fix, resume would re-run step 2's run_shell.
agent._save_checkpoint("run the command", 1)

def fake_model(prompt):
    return json.dumps({
        "thought": "increment", "plan_step": "edit", "progress": 50,
        "user_message": "run",
        "action": {"type": "run_shell", "args": {"command": ["true"]}},
    })
agent._call_action_model = fake_model

# Deterministic external side effect: run_shell "executes" by bumping a counter,
# then the process dies before the post-action checkpoint.
real_execute = agent._execute_command
def side_effecting_execute(command):
    if command.action.type == "run_shell":
        n = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(n + 1))
        os._exit(42)  # die right after the side effect, before post-step save
    return real_execute(command)
agent._execute_command = side_effecting_execute

agent._state.current_agent_role = "coder"
agent.executor.set_active_role("coder")
agent._run_loop("run the command", start_step=2)
print("NO_CRASH")
'''

_SIDE_EFFECT_RESUME = r'''
import json, sys
from pathlib import Path
from kernel.agent import LocalAgent
from kernel.agent_state import AgentConfig
from kernel.executor import SafeExecutor
from kernel.tests.conftest import FakeLLMClient, NullUI

ws = Path(sys.argv[1])
agent = LocalAgent(FakeLLMClient(), SafeExecutor(ws),
                   AgentConfig(persist_logs=False, api_request_interval=0, max_steps=4),
                   ui=NullUI())
# If resume replayed run_shell, the counter would reach 2.
result = agent.resume()
print(json.dumps({"result": result, "counter": (ws / "counter.txt").read_text()}))
'''


def _run(script, ws):
    env = os.environ.copy()
    env.update({"AGENT_SANDBOX_MODE": "workspace", "AGENT_SANDBOX_NET": "deny",
                "AGENT_WRITE_APPROVAL": "0"})
    return subprocess.run([sys.executable, "-c", script, str(ws)],
                          cwd=Path(__file__).parents[2], env=env,
                          capture_output=True, text=True, timeout=40)


def test_crashed_run_shell_is_not_replayed_on_resume(tmp_path):
    crashed = _run(_SIDE_EFFECT_CRASH, tmp_path)
    assert crashed.returncode == 42, crashed.stdout + crashed.stderr
    assert (tmp_path / "counter.txt").read_text() == "1"

    resumed = _run(_SIDE_EFFECT_RESUME, tmp_path)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    payload = last_json_line(resumed)
    # The side effect ran exactly once; resume refused to replay it.
    assert payload["counter"] == "1"
    assert "resume" in payload["result"].lower() or "возобновл" in payload["result"].lower()


# --- F2: a run is durable before any tool work --------------------------------
#
# A message that arrives while a checkpoint exists continues that task (it is saved as
# a follow-up); only a message with no checkpoint starts a new run. Both are durable
# before the first step, so a process that dies in setup loses neither.

_CRASH_IN_SETUP = r'''
import json, os, sys
from pathlib import Path
from kernel.agent import LocalAgent
from kernel.agent_state import AgentConfig
from kernel.executor import SafeExecutor
from kernel.tests.conftest import FakeLLMClient, NullUI

ws = Path(sys.argv[1])
message = sys.argv[2]
interrupted_task_exists = sys.argv[3] == "1"
agent = LocalAgent(FakeLLMClient(), SafeExecutor(ws),
                   AgentConfig(persist_logs=False, api_request_interval=0, max_steps=6),
                   ui=NullUI())
agent.new_session("work")
if interrupted_task_exists:
    agent._run_id = "old-run"
    agent._save_checkpoint("OLD TASK — do the old thing", 5)

# Die during setup, before any tool action.
class Boom(Exception): pass
def exploding_setup():
    raise Boom("process died before the first step")
agent._select_action_model_for_run = exploding_setup

try:
    agent.run(message, routed_role="coder")
except Boom:
    pass
data = agent._peek_checkpoint()
print(json.dumps({"goal": data and data.get("goal")}))
'''


def _crash_in_setup(ws, message, interrupted_task_exists):
    env = os.environ.copy()
    env.update({"AGENT_SANDBOX_MODE": "workspace", "AGENT_SANDBOX_NET": "deny",
                "AGENT_WRITE_APPROVAL": "0"})
    return subprocess.run(
        [sys.executable, "-c", _CRASH_IN_SETUP, str(ws), message, "1" if interrupted_task_exists else "0"],
        cwd=Path(__file__).parents[2], env=env, capture_output=True, text=True, timeout=40,
    )


def test_a_message_during_an_interrupted_task_is_saved_with_it_before_the_first_step(tmp_path):
    crashed = _crash_in_setup(tmp_path, "NEW TASK — also do this", interrupted_task_exists=True)
    assert crashed.returncode == 0, crashed.stdout + crashed.stderr
    payload = last_json_line(crashed)
    assert payload["goal"].startswith("OLD TASK")

    # Durable before any step ran: a fresh process finds the message among the task's follow-ups.
    resumed = _resume_in_fresh_process(tmp_path)
    payload = last_json_line(resumed)
    assert payload["result_none"] is False
    assert payload["resumed_goal"].startswith("OLD TASK")
    assert payload["task_updates"] == ["NEW TASK — also do this"]


def test_a_new_run_is_durable_before_its_first_step(tmp_path):
    crashed = _crash_in_setup(tmp_path, "NEW TASK — nothing was interrupted", interrupted_task_exists=False)
    assert crashed.returncode == 0, crashed.stdout + crashed.stderr
    payload = last_json_line(crashed)
    # No step ran, yet the run is recoverable: begin_run wrote it before setup died.
    assert payload["goal"] is not None
    assert payload["goal"].startswith("NEW TASK")

    resumed = _resume_in_fresh_process(tmp_path)
    payload = last_json_line(resumed)
    assert payload["result_none"] is False
    assert payload["resumed_goal"].startswith("NEW TASK")


def _resume_in_fresh_process(tmp_path):
    resume_script = (
        "import json,sys\n"
        "from pathlib import Path\n"
        "from kernel.agent import LocalAgent\n"
        "from kernel.agent_state import AgentConfig\n"
        "from kernel.executor import SafeExecutor\n"
        "from kernel.tests.conftest import FakeLLMClient, NullUI\n"
        "a=LocalAgent(FakeLLMClient(), SafeExecutor(Path(sys.argv[1])),"
        "AgentConfig(persist_logs=False, api_request_interval=0, max_steps=2), ui=NullUI())\n"
        "a._call_action_model=lambda p: json.dumps({'thought':'t','plan_step':'done',"
        "'progress':100,'user_message':'m','action':{'type':'final_answer','args':{'message':'done'}}})\n"
        "res=a.resume()\n"
        "print(json.dumps({'resumed_goal': a._state.user_goal, 'result_none': res is None,"
        " 'task_updates': a._state.task_updates}))\n"
    )
    env = os.environ.copy()
    env.update({"AGENT_SANDBOX_MODE": "workspace", "AGENT_SANDBOX_NET": "deny",
                "AGENT_WRITE_APPROVAL": "0"})
    return subprocess.run([sys.executable, "-c", resume_script, str(tmp_path)],
                          cwd=Path(__file__).parents[2], env=env,
                          capture_output=True, text=True, timeout=40)


# --- F3: recovered write must require fresh verification ----------------------

def test_recovered_write_requires_reverification_before_completion(agent_factory):
    from kernel.change_journal import content_digest, record_baseline
    agent = agent_factory()
    ws = agent.executor.workspace

    # Pre-existing verification evidence from earlier in the run.
    agent._state.verification_evidence = ["earlier: pytest passed"]
    agent._state.create_goal = True

    record_baseline(ws, relative_path="feature.py", existed=False, content=None,
                    pending_digest=content_digest("code\n"))
    (ws / "feature.py").write_text("code\n", encoding="utf-8", newline="")
    agent._save_checkpoint("build feature", 1, pending_action={
        "step": 1, "action_kind": "file", "path": "feature.py",
        "pre_digest": None, "intended_digest": content_digest("code\n"),
    })
    agent._load_checkpoint()
    agent._reconcile_pending_action(agent._pending_action)

    # The recovered mutation re-enters pending_verification_paths, so completion
    # is blocked despite the stale verification evidence.
    agent._refresh_completion_requirements()
    assert "feature.py" in agent._state.pending_verification_paths
    assert agent._state.completion_ready is False
    assert agent._state.completion_blockers


# --- F5: crash after replace before promote, direct /revert -------------------

def test_direct_revert_after_crash_before_promote_is_conservative(agent_factory):
    from kernel.change_journal import content_digest, record_baseline, revert_workspace
    agent = agent_factory()
    ws = agent.executor.workspace
    (ws / "v.txt").write_text("V1\n", encoding="utf-8", newline="")
    # Simulate: agent committed V2 to disk, crashed BEFORE promote.
    record_baseline(ws, relative_path="v.txt", existed=True, content="V1\n",
                    pending_digest=content_digest("V2\n"))
    (ws / "v.txt").write_text("V2\n", encoding="utf-8", newline="")  # replace succeeded, promote did NOT

    # Direct /revert (no /resume first): provenance unproven -> conservative skip,
    # deterministic, never destroys V2.
    result = revert_workspace(ws)
    assert [(r.path, r.action) for r in result] == [("v.txt", "skipped")]
    assert (ws / "v.txt").read_text(encoding="utf-8") == "V2\n"
