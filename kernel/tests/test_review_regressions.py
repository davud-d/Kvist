from __future__ import annotations

import json

import pytest


from kernel.agent.agent import LocalAgent
from kernel.session.change_journal import load_baseline, revert_workspace
from kernel.tests.conftest import last_json_line, make_command

pytestmark = pytest.mark.slow

def _write(agent, path, content):
    return agent._execute_command(make_command("write_file", {"path": path, "content": content}))


# --- Finding 2: last_digest must not advance on a failed write -----------------

@pytest.mark.offline_smoke
def test_failed_second_write_keeps_first_write_revertable(agent_factory, monkeypatch):
    agent = agent_factory()
    ws = agent.executor.workspace
    (ws / "f.txt").write_text("ORIGINAL\n", encoding="utf-8", newline="")

    assert _write(agent, "f.txt", "V1\n")["ok"] is True
    assert (ws / "f.txt").read_text(encoding="utf-8") == "V1\n"

    # Second write fails during the atomic replace.
    real_replace = type(agent.executor)._atomic_replace_text

    def crash(self, target, content, expected):
        if target.name == "f.txt":
            raise OSError("simulated failure during second write")
        return real_replace(self, target, content, expected)

    monkeypatch.setattr(type(agent.executor), "_atomic_replace_text", crash)
    feedback = _write(agent, "f.txt", "V2\n")
    assert feedback["ok"] is False
    assert (ws / "f.txt").read_text(encoding="utf-8") == "V1\n"
    monkeypatch.setattr(type(agent.executor), "_atomic_replace_text", real_replace)

    # revert must recognise V1 as the agent's own committed content and restore
    # the ORIGINAL baseline — not skip it as an external modification.
    result = revert_workspace(ws)
    assert [(r.path, r.action) for r in result] == [("f.txt", "restored")]
    assert (ws / "f.txt").read_text(encoding="utf-8") == "ORIGINAL\n"


# --- Finding 5: revert must not resurrect a user-deleted pre-existing file -----

@pytest.mark.offline_smoke
def test_revert_does_not_resurrect_user_deleted_file(agent_factory):
    agent = agent_factory()
    ws = agent.executor.workspace
    (ws / "keep.txt").write_text("BEFORE\n", encoding="utf-8", newline="")

    assert _write(agent, "keep.txt", "AGENT\n")["ok"] is True
    (ws / "keep.txt").unlink()  # user deletes the file after the agent edited it

    result = revert_workspace(ws)
    assert [(r.path, r.action) for r in result] == [("keep.txt", "skipped")]
    assert not (ws / "keep.txt").exists()
    assert "keep.txt" in load_baseline(ws)


# --- Finding 3: an interrupted revert restore never leaves a partial file ------

def test_interrupted_revert_restore_leaves_no_partial_file(tmp_path, monkeypatch):
    import os as _os
    from kernel.session import change_journal
    from kernel.session.change_journal import content_digest, record_baseline, promote_baseline

    (tmp_path / "big.txt").write_text("ORIGINAL-BASELINE-CONTENT\n" * 50, encoding="utf-8", newline="")
    baseline_text = (tmp_path / "big.txt").read_text(encoding="utf-8")
    agent_text = "AGENT-CONTENT\n" * 50
    record_baseline(
        tmp_path,
        relative_path="big.txt",
        existed=True,
        content=baseline_text,
        pending_digest=content_digest(agent_text),
    )
    (tmp_path / "big.txt").write_text(agent_text, encoding="utf-8", newline="")
    promote_baseline(tmp_path, "big.txt", content_digest(agent_text))

    # Restoration is atomic: fd-relative os.rename where directory fds exist,
    # os.replace otherwise (Windows). Inject a crash into the one it uses.
    swap = "rename" if change_journal._HAS_DIR_FD else "replace"
    real_swap = getattr(_os, swap)

    def crashing_swap(src, dst, *a, **k):
        if _os.path.basename(str(dst)) == "big.txt":
            raise OSError("simulated crash during atomic rename")
        return real_swap(src, dst, *a, **k)

    monkeypatch.setattr(change_journal.os, swap, crashing_swap)
    with pytest.raises(OSError):
        change_journal.revert_workspace(tmp_path)

    # The file must still be the COMPLETE agent content, never a truncated blend.
    assert (tmp_path / "big.txt").read_text(encoding="utf-8") == agent_text
    assert list(tmp_path.glob(".big.txt.*revert*")) == []
    assert not list(tmp_path.glob("*.revert.tmp"))

    # Re-running after the interruption completes cleanly (idempotent recovery).
    monkeypatch.setattr(change_journal.os, swap, real_swap)
    result = change_journal.revert_workspace(tmp_path)
    assert [(r.path, r.action) for r in result] == [("big.txt", "restored")]
    assert (tmp_path / "big.txt").read_text(encoding="utf-8") == baseline_text


# --- Finding 4: /changes must obey the workspace boundary ----------------------

def test_changes_cannot_disclose_files_outside_the_workspace(tmp_path):
    import json
    from kernel.session.change_journal import build_diff_report, journal_path, revert_workspace

    secret = tmp_path.parent / "outside-secret.txt"
    secret.write_text("TOP-SECRET-MARKER-9F3\n", encoding="utf-8", newline="")

    journal = journal_path(tmp_path)
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(
        json.dumps(
            {
                "version": 1,
                "baseline": {
                    "../outside-secret.txt": {"existed": False, "content": None},
                    "/etc/hostname": {"existed": False, "content": None},
                },
            }
        ),
        encoding="utf-8",
    )

    report = build_diff_report(tmp_path)
    assert "TOP-SECRET-MARKER-9F3" not in report

    # And revert must not touch the external file either.
    reverted = revert_workspace(tmp_path)
    assert all(item.action == "skipped" for item in reverted)
    assert secret.read_text(encoding="utf-8") == "TOP-SECRET-MARKER-9F3\n"


# --- Finding 1: crash-consistent action lifecycle -----------------------------

def _seed_pending(agent, *, path, pre_digest, intended_digest, step=1, goal="do it"):
    """Persist a checkpoint whose pending_action marks an in-flight write."""
    agent._save_checkpoint(
        goal,
        step,
        pending_action={
            "step": step,
            "path": path,
            "pre_digest": pre_digest,
            "intended_digest": intended_digest,
        },
    )


def test_reconcile_agent_committed_advances_with_ownership(agent_factory):
    from kernel.session.change_journal import content_digest, load_baseline, promote_baseline, record_baseline
    agent = agent_factory()
    ws = agent.executor.workspace
    record_baseline(ws, relative_path="x.txt", existed=False, content=None,
                    pending_digest=content_digest("hello\n"))
    (ws / "x.txt").write_text("hello\n", encoding="utf-8", newline="")
    promote_baseline(ws, "x.txt", content_digest("hello\n"))  # executor promoted in-process

    _seed_pending(agent, path="x.txt", pre_digest=None,
                  intended_digest=content_digest("hello\n"), step=3)
    assert agent._load_checkpoint() is not None
    start = agent._reconcile_pending_action(agent._pending_action)
    assert start == 4  # resume PAST the step, never replay it
    assert "x.txt" in agent._state.files_changed
    assert "x.txt" in agent._state.pending_verification_paths  # must be re-verified
    # Provenance was already proven in-process, so ownership stands.
    assert load_baseline(ws)["x.txt"]["last_digest"] == content_digest("hello\n")


def test_reconcile_desired_present_advances_without_ownership(agent_factory):
    from kernel.session.change_journal import content_digest, load_baseline, record_baseline
    agent = agent_factory()
    ws = agent.executor.workspace
    # Write-ahead pending recorded, file holds intended content, but NO promote
    # ran (crash between replace and promote) -> ownership unproven.
    record_baseline(ws, relative_path="x.txt", existed=False, content=None,
                    pending_digest=content_digest("hello\n"))
    (ws / "x.txt").write_text("hello\n", encoding="utf-8", newline="")

    _seed_pending(agent, path="x.txt", pre_digest=None,
                  intended_digest=content_digest("hello\n"), step=3)
    agent._load_checkpoint()
    start = agent._reconcile_pending_action(agent._pending_action)
    assert start == 4  # advance, do not replay
    assert "x.txt" in agent._state.pending_verification_paths
    # Ownership must NOT be granted: reconciliation never promotes.
    assert load_baseline(ws)["x.txt"].get("last_digest") is None


def test_reconcile_not_executed_retries_same_step(agent_factory):
    from kernel.session.change_journal import content_digest, record_baseline
    agent = agent_factory()
    ws = agent.executor.workspace
    (ws / "y.txt").write_text("BEFORE\n", encoding="utf-8", newline="")
    record_baseline(ws, relative_path="y.txt", existed=True, content="BEFORE\n",
                    pending_digest=content_digest("AFTER\n"))
    # The mutation never reached disk: file still holds the pre-content.
    _seed_pending(agent, path="y.txt", pre_digest=content_digest("BEFORE\n"),
                  intended_digest=content_digest("AFTER\n"), step=2)
    agent._load_checkpoint()
    assert agent._reconcile_pending_action(agent._pending_action) == 2


def test_reconcile_ambiguous_fails_safely(agent_factory):
    from kernel.session.change_journal import content_digest
    agent = agent_factory()
    ws = agent.executor.workspace
    (ws / "z.txt").write_text("SOMETHING-ELSE\n", encoding="utf-8", newline="")
    _seed_pending(agent, path="z.txt", pre_digest=content_digest("BEFORE\n"),
                  intended_digest=content_digest("AFTER\n"), step=1)
    agent._load_checkpoint()
    assert agent._reconcile_pending_action(agent._pending_action) is None


def test_resume_aborts_safely_on_ambiguous_pending(agent_factory):
    from kernel.session.change_journal import content_digest
    agent = agent_factory()
    ws = agent.executor.workspace
    (ws / "z.txt").write_text("MYSTERY\n", encoding="utf-8", newline="")
    _seed_pending(agent, path="z.txt", pre_digest=content_digest("BEFORE\n"),
                  intended_digest=content_digest("AFTER\n"), step=1)

    message = agent.resume()

    assert message is not None
    assert "resume" in message.lower() or "возобновл" in message.lower()
    # The workspace is untouched and the checkpoint is preserved for recovery.
    assert (ws / "z.txt").read_text(encoding="utf-8") == "MYSTERY\n"
    assert agent._peek_checkpoint() is not None


# --- Finding 1: real process-boundary crash test ------------------------------

_CRASH_SCRIPT = r'''
import json, os, sys
from pathlib import Path
from kernel.agent.agent import LocalAgent
from kernel.agent.agent_state import AgentConfig
from kernel.sandbox.executor import SafeExecutor
from kernel.tests.conftest import FakeLLMClient, NullUI

ws = Path(sys.argv[1])
agent = LocalAgent(FakeLLMClient(), SafeExecutor(ws),
                   AgentConfig(persist_logs=False, api_request_interval=0, max_steps=6),
                   ui=NullUI())
agent.new_session("work")
# A stale checkpoint from an OLDER task must not resurface once the new task's
# first mutation lands.
agent._save_checkpoint("OLD TASK A", 5)

def fake_model(prompt):
    return json.dumps({
        "thought": "append", "plan_step": "edit", "progress": 50,
        "user_message": "writing",
        "action": {"type": "append_file", "args": {"path": "out.txt", "content": "hello\n"}},
    })
agent._call_action_model = fake_model

real_save = agent._save_checkpoint
def crashing_save(goal, step, pending_action=None):
    target = ws / "out.txt"
    # Die at the post-action checkpoint (pending_action is None) once the append
    # is on disk — BEFORE the completed checkpoint is persisted.
    if pending_action is None and target.is_file() and target.read_text() == "hello\n":
        os._exit(42)
    real_save(goal, step, pending_action=pending_action)
agent._save_checkpoint = crashing_save

agent._state.current_agent_role = "coder"
agent.executor.set_active_role("coder")
agent._run_loop("append hello to out.txt", start_step=1)
print("NO_CRASH")
'''

_RESUME_SCRIPT = r'''
import json, sys
from pathlib import Path
from kernel.agent.agent import LocalAgent
from kernel.agent.agent_state import AgentConfig
from kernel.sandbox.executor import SafeExecutor
from kernel.tests.conftest import FakeLLMClient, NullUI

ws = Path(sys.argv[1])
agent = LocalAgent(FakeLLMClient(), SafeExecutor(ws),
                   AgentConfig(persist_logs=False, api_request_interval=0, max_steps=4),
                   ui=NullUI())
agent._call_action_model = lambda prompt: json.dumps({
    "thought": "done", "plan_step": "done", "progress": 100,
    "user_message": "done", "action": {"type": "final_answer", "args": {"message": "done"}},
})
agent.resume()
print(json.dumps({
    "session": agent.current_session,
    "content": (ws / "out.txt").read_text(encoding="utf-8"),
}))
'''


def test_crash_after_append_does_not_duplicate_on_resume(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = os.environ.copy()
    env.update({
        "AGENT_SANDBOX_MODE": "workspace",
        "AGENT_SANDBOX_NET": "deny",
        "AGENT_WRITE_APPROVAL": "0",
    })
    root = Path(__file__).parents[2]

    crashed = subprocess.run(
        [sys.executable, "-c", _CRASH_SCRIPT, str(tmp_path)],
        cwd=root, env=env, capture_output=True, text=True, timeout=40,
    )
    # The process must have died in the crash hook, not finished normally.
    assert crashed.returncode == 42, crashed.stdout + crashed.stderr
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hello\n"

    resumed = subprocess.run(
        [sys.executable, "-c", _RESUME_SCRIPT, str(tmp_path)],
        cwd=root, env=env, capture_output=True, text=True, timeout=40,
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    payload = last_json_line(resumed)
    assert payload["session"] == "work"
    # The append must NOT have been replayed: exactly one "hello".
    assert payload["content"] == "hello\n"


# --- Finding 6: /new leaves the session intact when persistence fails ----------

def test_new_session_failure_preserves_session_in_memory_and_on_disk(agent_factory, monkeypatch):
    agent = agent_factory()
    agent.new_session("keeper")
    agent._session_history = [{"goal": "important work"}]
    agent._chat_history = [{"role": "user", "content": "keep me"}]
    agent.save_session()

    def boom(_self, _session):
        raise OSError("simulated storage failure while creating a session")

    monkeypatch.setattr(type(agent), "_create_session", boom)

    with pytest.raises(OSError):
        agent.new_session()

    # In-memory state is untouched...
    assert agent._session_history == [{"goal": "important work"}]
    assert agent._chat_history == [{"role": "user", "content": "keep me"}]
    # ...and so is the persisted session.
    restored = agent_factory()
    assert restored.current_session == "keeper"
    assert restored._session_history == [{"goal": "important work"}]


# --- Finding 7: one stdio transport; both entrypoints agree on hello ----------

def test_launch_and_serve_stdio_emit_identical_session_hello(monkeypatch, tmp_path):
    import io

    from kernel.protocol import event_stdio
    from kernel.agent.agent_state import AgentConfig
    from kernel.protocol.event_session import EventSession
    from kernel.protocol.event_stream_ui import EventStreamUI
    from kernel.sandbox.executor import SafeExecutor
    from kernel.protocol.frontend_protocol import FrontendChannel
    from kernel.tests.conftest import FakeLLMClient, NullUI

    monkeypatch.setenv("AGENT_LLM_PROVIDER", "openai")

    # Seed a checkpoint so has_checkpoint is a meaningful, non-default value.
    seed = LocalAgent(
        FakeLLMClient(), SafeExecutor(tmp_path),
        AgentConfig(persist_logs=False, api_request_interval=0), ui=NullUI(),
    )
    seed.new_session("shared")
    seed._save_checkpoint("interrupted", 2)

    def build_agent(executor, *, ui, **kwargs):
        agent = LocalAgent(
            FakeLLMClient(content="hi"), executor,
            AgentConfig(persist_logs=False, api_request_interval=0, stream=False), ui=ui,
        )
        return agent, agent.config, []

    # Path A: the CLI launch entrypoint.
    out_a = io.StringIO()
    event_stdio.launch(
        str(tmp_path), "hi", build_agent,
        in_stream=io.StringIO(""), out_stream=out_a, routed_role="chat",
    )
    hello_a = json.loads(out_a.getvalue().splitlines()[0])
    # Path A's message continued the interrupted task and finished it, so its checkpoint
    # is gone; path B must meet the same interrupted task, not the leftovers of A.
    seed._save_checkpoint("interrupted", 2)

    # Path B: the builtin-frontend serve_stdio entrypoint over the same core.
    channel = FrontendChannel()
    executor = SafeExecutor(tmp_path)
    agent_b = LocalAgent(
        FakeLLMClient(content="hi"), executor,
        AgentConfig(persist_logs=False, api_request_interval=0, stream=False),
        ui=EventStreamUI(channel),
    )
    session_b = EventSession(channel, agent_b)
    out_b = io.StringIO()
    monkeypatch.setattr(event_stdio.sys, "stdout", out_b)
    monkeypatch.setattr(event_stdio.sys, "stdin", io.StringIO("hi\n"))
    event_stdio.serve_stdio(session_b)
    hello_b = json.loads(out_b.getvalue().splitlines()[0])

    # Both entrypoints report the same session lifecycle fields.
    assert hello_a["session"] == hello_b["session"] == "shared"
    assert hello_a["has_checkpoint"] == hello_b["has_checkpoint"] is True


# --- Finding 8: hello metadata is captured before the worker runs -------------

def test_hello_has_checkpoint_is_captured_before_worker_clears_it(tmp_path):
    import io

    from kernel.protocol import event_stdio
    from kernel.agent.agent_state import AgentConfig
    from kernel.protocol.event_session import EventSession
    from kernel.protocol.event_stream_ui import EventStreamUI
    from kernel.sandbox.executor import SafeExecutor
    from kernel.protocol.frontend_protocol import FrontendChannel
    from kernel.tests.conftest import FakeLLMClient, NullUI

    seed = LocalAgent(
        FakeLLMClient(), SafeExecutor(tmp_path),
        AgentConfig(persist_logs=False, api_request_interval=0), ui=NullUI(),
    )
    seed.new_session("racy")
    seed._save_checkpoint("interrupted", 2)

    channel = FrontendChannel()
    agent = LocalAgent(
        FakeLLMClient(content="done"), SafeExecutor(tmp_path),
        AgentConfig(persist_logs=False, api_request_interval=0, stream=False),
        ui=EventStreamUI(channel),
    )

    # A resume worker that clears the checkpoint the instant it starts. If hello
    # were sampled after submit, it would observe has_checkpoint=False.
    def resume_and_clear():
        agent._clear_checkpoint()
        return "done"

    agent.resume = resume_and_clear

    session = EventSession(channel, agent)
    out = io.StringIO()
    event_stdio.serve_session(
        session, out_stream=out, in_reader=io.StringIO(""), resume=True,
    )

    hello = json.loads(out.getvalue().splitlines()[0])
    assert hello["session"] == "racy"
    assert hello["has_checkpoint"] is True  # captured at launch, before the worker
    assert agent._peek_checkpoint() is None  # worker really did clear it
