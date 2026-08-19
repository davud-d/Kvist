from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


from session_store import SessionStore, run_session_op

pytestmark = pytest.mark.slow

def test_status_reports_active_session_and_checkpoint(agent_factory):
    agent = agent_factory()
    agent.new_session("feature")
    agent._save_checkpoint("do the thing", 4)

    payload = run_session_op(agent.executor.workspace, "status")

    assert payload["ok"] is True
    assert payload["active_session"] == "feature"
    assert "feature" in payload["sessions"]
    assert payload["checkpoint"] == {"goal": "do the thing", "step": 4, "progress": 0}


def test_create_activates_a_fresh_empty_session(tmp_path):
    created = run_session_op(tmp_path, "create", "fresh")

    assert created["active_session"] == "fresh"
    assert created["sessions"] == ["fresh"]
    assert created["checkpoint"] is None

    with pytest.raises(ValueError, match="already exists"):
        run_session_op(tmp_path, "create", "fresh")


def test_switch_requires_an_existing_readable_target(tmp_path):
    run_session_op(tmp_path, "create", "alpha")
    run_session_op(tmp_path, "create", "beta")

    switched = run_session_op(tmp_path, "switch", "alpha")
    assert switched["active_session"] == "alpha"

    with pytest.raises(ValueError, match="Unknown session"):
        run_session_op(tmp_path, "switch", "ghost")
    assert run_session_op(tmp_path, "status")["active_session"] == "alpha"


def test_delete_refuses_the_active_session(tmp_path):
    run_session_op(tmp_path, "create", "alpha")
    run_session_op(tmp_path, "create", "beta")

    with pytest.raises(ValueError, match="active session"):
        run_session_op(tmp_path, "delete", "beta")

    removed = run_session_op(tmp_path, "delete", "alpha")
    assert removed["sessions"] == ["beta"]
    session_file = tmp_path / ".kvist" / "sessions" / "alpha.json"
    assert not session_file.exists()
    assert not session_file.with_suffix(".json.bak").exists()


def test_reset_empties_the_active_session_in_place(agent_factory):
    agent = agent_factory()
    agent.new_session("feature")
    agent._session_history = [{"goal": "history"}]
    agent._save_checkpoint("task", 2)

    payload = run_session_op(agent.executor.workspace, "reset")

    assert payload["active_session"] == "feature"
    assert payload["checkpoint"] is None
    document = json.loads(
        (agent.executor.workspace / ".kvist" / "sessions" / "feature.json").read_text(
            encoding="utf-8"
        )
    )
    assert document["session_history"] == []
    assert document["checkpoint"] is None


def test_store_shares_semantics_with_agent_state(agent_factory):
    agent = agent_factory()
    agent.new_session("shared")
    agent._session_history = [{"goal": "agent side"}]
    agent.save_session()

    store = SessionStore(agent.executor.workspace)
    assert store.current_session == "shared"

    restored = agent_factory()
    assert restored.current_session == "shared"
    assert restored._session_history == [{"goal": "agent side"}]


def test_session_op_cli_round_trip_in_subprocess(tmp_path):
    environment = os.environ.copy()
    environment.update(
        {
            "AGENT_SANDBOX_MODE": "workspace",
            "AGENT_SANDBOX_NET": "deny",
            "AGENT_WRITE_APPROVAL": "0",
        }
    )
    root = Path(__file__).parents[1]

    def cli(*arguments: str) -> dict:
        completed = subprocess.run(
            [
                sys.executable,
                str(root / "main.py"),
                "--workspace",
                str(tmp_path),
                *arguments,
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        assert completed.returncode == (0 if payload["ok"] else 1)
        return payload

    created = cli("--session-op", "create", "--session-name", "cli-session")
    assert created["active_session"] == "cli-session"

    status = cli("--session-op", "status")
    assert status["active_session"] == "cli-session"
    assert status["checkpoint"] is None

    failure = cli("--session-op", "switch", "--session-name", "ghost")
    assert failure["ok"] is False
    assert "Unknown session" in failure["error"]
