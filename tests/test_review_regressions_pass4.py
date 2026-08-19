from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


from agent import LocalAgent
from tests.conftest import make_command, requires_posix

pytestmark = pytest.mark.slow

# --- F2: fs write succeeds but journal promotion fails -> not ordinary success

@pytest.mark.offline_smoke
def test_write_with_failed_promotion_is_not_reported_as_success(agent_factory, monkeypatch):
    import executor as executor_module

    agent = agent_factory()
    ws = agent.executor.workspace
    (ws / "f.txt").write_text("A\n", encoding="utf-8")

    # Force compare-and-promote to fail (as if the journal pending belonged to a
    # different transaction). The filesystem write still lands.
    monkeypatch.setattr(executor_module, "promote_baseline",
                        lambda *a, **k: False)

    feedback = agent._execute_command(
        make_command("write_file", {"path": "f.txt", "content": "B\n"})
    )
    # The bytes are on disk...
    assert (ws / "f.txt").read_text(encoding="utf-8") == "B\n"
    # ...but the action must NOT be reported as an ordinary success.
    assert feedback["ok"] is False
    # And /revert must not have been granted ownership of B.
    from change_journal import load_baseline
    entry = load_baseline(ws)["f.txt"]
    from change_journal import content_digest
    assert entry.get("last_digest") != content_digest("B\n")


# --- F6: a different action cannot finalize another action's pending txn -------

def test_promotion_is_bound_to_action_identity(tmp_path):
    from change_journal import content_digest, load_baseline, promote_baseline, record_baseline

    digest_b = content_digest("B\n")
    # Action A records a pending write of B.
    record_baseline(tmp_path, relative_path="f.txt", existed=True, content="A\n",
                    pending_digest=digest_b, run_id="run-1", action_id="action-A")
    (tmp_path / "f.txt").write_text("B\n", encoding="utf-8")

    # Action B (same run, different action) also intends B and tries to promote.
    ok = promote_baseline(tmp_path, "f.txt", digest_b,
                          run_id="run-1", action_id="action-B")
    assert ok is False
    assert load_baseline(tmp_path)["f.txt"].get("last_digest") != digest_b

    # A stale pending from a previous run must also never be promoted.
    assert promote_baseline(tmp_path, "f.txt", digest_b,
                            run_id="run-OTHER", action_id="action-A") is False

    # The owning action promotes correctly.
    assert promote_baseline(tmp_path, "f.txt", digest_b,
                            run_id="run-1", action_id="action-A") is True
    assert load_baseline(tmp_path)["f.txt"]["last_digest"] == digest_b


# --- F7: a corrupt write-ahead journal must block agent-owned mutations --------

@pytest.mark.offline_smoke
@pytest.mark.parametrize("action,args", [
    ("write_file", {"path": "f.txt", "content": "NEW\n"}),
    ("append_file", {"path": "f.txt", "content": "MORE\n"}),
    ("replace_in_file", {"path": "f.txt", "old_text": "ORIG", "new_text": "X"}),
])
def test_corrupt_journal_blocks_mutation(agent_factory, action, args):
    from change_journal import journal_path

    agent = agent_factory()
    ws = agent.executor.workspace
    (ws / "f.txt").write_text("ORIG\n", encoding="utf-8")

    j = journal_path(ws)
    j.parent.mkdir(parents=True, exist_ok=True)
    j.write_text("{corrupt", encoding="utf-8")
    j.with_suffix(".json.bak").write_text("{corrupt", encoding="utf-8")

    feedback = agent._execute_command(make_command(action, args))
    # Write-ahead recovery could not be recorded, so the mutation is refused and
    # the file is left untouched.
    assert feedback["ok"] is False
    assert (ws / "f.txt").read_text(encoding="utf-8") == "ORIG\n"


def test_corrupt_journal_is_reported_not_treated_as_empty(tmp_path):
    from change_journal import build_diff_report, journal_path

    j = journal_path(tmp_path)
    j.parent.mkdir(parents=True, exist_ok=True)
    j.write_text("{corrupt", encoding="utf-8")
    j.with_suffix(".json.bak").write_text("{corrupt", encoding="utf-8")

    report = build_diff_report(tmp_path)
    assert "corrupt" in report.lower()


# --- F3: committed reconciliation reconstructs the full logical success state --

def _semantic_state(agent):
    s = agent._state
    return {
        "files_changed": sorted(s.files_changed),
        "pending_verification_paths": sorted(s.pending_verification_paths),
        "completed_plan_steps": sorted(s.completed_plan_steps),
        "progress": s.progress,
    }


def test_committed_reconciliation_matches_normal_success_state(agent_factory):
    from tests.conftest import make_plan, make_command

    # --- Normal uninterrupted successful write ---
    normal = agent_factory()
    normal._state.plan_info = make_plan()
    normal._state.create_goal = True
    (normal.executor.workspace / "feature.py").write_text("", encoding="utf-8")
    cmd = make_command("write_file", {"path": "feature.py", "content": "code\n"},
                       plan_step="edit implementation", progress=60)
    normal.executor.begin_action_transaction("run-x", "act-1")
    fb = normal._execute_command(cmd)
    normal.executor.end_action_transaction()
    assert fb["ok"] is True
    normal._register_feedback(1, cmd, fb)
    normal_state = _semantic_state(normal)

    # --- Same write, but crash before post-action checkpoint, then reconcile ---
    from change_journal import content_digest, record_baseline, promote_baseline
    recov = agent_factory()
    recov._state.plan_info = make_plan()
    recov._state.create_goal = True
    ws = recov.executor.workspace
    record_baseline(ws, relative_path="feature.py", existed=False, content=None,
                    pending_digest=content_digest("code\n"), run_id="run-x", action_id="act-1")
    (ws / "feature.py").write_text("code\n", encoding="utf-8")
    promote_baseline(ws, "feature.py", content_digest("code\n"), run_id="run-x", action_id="act-1")
    recov._save_checkpoint("build feature", 1, pending_action={
        "step": 1, "run_id": "run-x", "action_id": "act-1", "action_kind": "file",
        "action_type": "write_file", "plan_step": "edit implementation", "progress": 60,
        "path": "feature.py", "pre_digest": None, "intended_digest": content_digest("code\n"),
    })
    recov._load_checkpoint()
    recov._state.plan_info = make_plan()  # plan restored as in a real resume
    recov._state.create_goal = True
    assert recov._reconcile_pending_action(recov._pending_action) == 2
    recovered_state = _semantic_state(recov)

    # Semantic runtime state must match across all reconstructed fields.
    assert recovered_state == normal_state


# --- F1: durable NEW run activation is recoverable, not lost ------------------

def test_begin_run_persists_new_run_and_supersedes_old(agent_factory):
    agent = agent_factory()
    agent.new_session("work")
    agent._run_id = "old"
    agent._save_checkpoint("OLD TASK", 5)

    # Durable NEW activation (what run() does before planning).
    agent._run_id = "new"
    agent._begin_run("NEW TASK", "new")

    ck = agent._peek_checkpoint()
    assert ck is not None and ck["goal"] == "NEW TASK"  # NEW durable, OLD gone
    # A reload sees phase=planning so resume will re-plan rather than revive OLD.
    fresh = agent_factory()
    assert fresh.current_session == "work"
    fresh._load_checkpoint()
    assert fresh._resumed_phase == "planning"
    assert fresh._run_id == "new"


# --- F4: TOCTOU — parent swapped to an outside symlink between validation/use --

@requires_posix
def test_changes_does_not_follow_parent_swapped_to_symlink(tmp_path):
    import change_journal
    from change_journal import build_diff_report, journal_path

    ws = tmp_path / "ws"
    (ws / "safe").mkdir(parents=True)
    (ws / "safe" / "file.txt").write_text("REPLACEMENT\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("OUTSIDE_SECRET_MARKER\n", encoding="utf-8")

    j = journal_path(ws)
    j.parent.mkdir(parents=True, exist_ok=True)
    j.write_text(json.dumps({"version": 1, "baseline": {
        "safe/file.txt": {"existed": True, "content": "BASE\n"}}}), encoding="utf-8")

    # Swap workspace/safe to a symlink pointing outside, exactly at the boundary
    # between path validation and the final open.
    real_open = os.open
    swapped = {"done": False}

    def hooking_open(path, flags, *a, **k):
        # After the workspace dir is opened but before the child is opened,
        # replace 'safe' with a symlink to the outside directory.
        if not swapped["done"] and k.get("dir_fd") is not None and path == "safe":
            swapped["done"] = True
            (ws / "safe" / "file.txt").unlink()
            (ws / "safe").rmdir()
            (ws / "safe").symlink_to(outside)
        return real_open(path, flags, *a, **k)

    import builtins  # noqa: F401 - keep import local
    orig = change_journal.os.open
    try:
        change_journal.os.open = hooking_open
        report = build_diff_report(ws)
    finally:
        change_journal.os.open = orig

    assert "OUTSIDE_SECRET_MARKER" not in report


# --- F5: legitimate internal hardlinks vs external hardlinks ------------------

@requires_posix
def test_internal_hardlinks_allowed_external_rejected(tmp_path):
    from change_journal import _SafeChild, JournalPathBindingError

    ws = tmp_path / "ws"
    ws.mkdir()
    # Two hardlinks fully inside the workspace -> allowed (executor policy).
    (ws / "a.txt").write_text("SHARED\n", encoding="utf-8")
    os.link(ws / "a.txt", ws / "b.txt")
    with _SafeChild(ws, "a.txt") as child:
        assert child.read_bytes() == b"SHARED\n"

    # A link whose inode also has an alias OUTSIDE the workspace -> rejected.
    outside = tmp_path / "outside.txt"
    outside.write_text("EXTERNAL\n", encoding="utf-8")
    os.link(outside, ws / "ext.txt")
    with _SafeChild(ws, "ext.txt") as child:
        with pytest.raises(JournalPathBindingError):
            child.read_bytes()
