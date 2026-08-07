from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import requires_posix

import durable_json
from durable_json import JsonStorageError


def _session_path(agent, name: str):
    return (
        agent.executor.workspace
        / ".kvist"
        / "sessions"
        / f"{name}.json"
    )


def test_empty_named_session_survives_process_restart(agent_factory):
    agent = agent_factory()
    agent.new_session("empty")

    restored = agent_factory()

    assert restored.current_session == "empty"
    assert restored._session_history == []
    assert not hasattr(restored, "_chat_history")


def test_session_survives_real_process_restart(tmp_path):
    environment = os.environ.copy()
    environment.update(
        {
            "AGENT_SANDBOX_MODE": "workspace",
            "AGENT_SANDBOX_NET": "deny",
            "AGENT_WRITE_APPROVAL": "0",
        }
    )
    save_script = """
import sys
from pathlib import Path
from agent import LocalAgent
from agent_state import AgentConfig
from executor import SafeExecutor
from tests.conftest import FakeLLMClient, NullUI

workspace = Path(sys.argv[1])
agent = LocalAgent(
    FakeLLMClient(),
    SafeExecutor(workspace),
    AgentConfig(persist_logs=False, api_request_interval=0),
    ui=NullUI(),
)
agent.new_session("restart")
agent._session_history = [{"goal": "перезапуск 🌲"}]
agent._chat_history = [{"role": "user", "content": "сохранено"}]
agent.save_session()
"""
    load_script = """
import json
import sys
from pathlib import Path
from agent import LocalAgent
from agent_state import AgentConfig
from executor import SafeExecutor
from tests.conftest import FakeLLMClient, NullUI

agent = LocalAgent(
    FakeLLMClient(),
    SafeExecutor(Path(sys.argv[1])),
    AgentConfig(persist_logs=False, api_request_interval=0),
    ui=NullUI(),
)
print(json.dumps({
    "session": agent.current_session,
    "history": agent._session_history,
    "chat": agent._chat_history,
}, ensure_ascii=False))
"""

    subprocess.run(
        [sys.executable, "-c", save_script, str(tmp_path)],
        check=True,
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    loaded = subprocess.run(
        [sys.executable, "-c", load_script, str(tmp_path)],
        check=True,
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )

    payload = json.loads(loaded.stdout)
    assert payload == {
        "session": "restart",
        "history": [{"goal": "перезапуск 🌲"}],
        "chat": [{"role": "user", "content": "сохранено"}],
    }


def test_large_unicode_session_roundtrip_preserves_user_content(agent_factory):
    agent = agent_factory()
    long_content = ("🌲 Привет 世界 — " * 8_000) + "\nfinal line"
    agent._session_history = [
        {
            "goal": f"задача {index}",
            "role": "chat",
            "result": f"результат {index}",
        }
        for index in range(10)
    ]
    agent._chat_history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": long_content},
        {"role": "assistant", "content": "готово"},
    ]
    agent._last_response_language = "ru"
    agent.save_session()

    restored = agent_factory()

    assert restored._session_history == agent._session_history
    assert restored._chat_history[1]["content"] == long_content
    assert restored._last_response_language == "ru"


@requires_posix
def test_missing_storage_directories_are_created_privately(agent_factory):
    agent = agent_factory()
    agent._session_history = [{"goal": "persist"}]

    agent.save_session()

    kvist_dir = agent.executor.workspace / ".kvist"
    sessions_dir = kvist_dir / "sessions"
    state_path = _session_path(agent, "default")
    assert stat.S_IMODE(kvist_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(sessions_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


def test_interrupted_session_replace_preserves_last_complete_state(
    agent_factory,
    monkeypatch,
):
    agent = agent_factory()
    agent._session_history = [{"goal": "last complete state"}]
    agent.save_session()
    state_path = _session_path(agent, "default")
    original_replace = durable_json._replace_path

    def fail_state_replace(source, destination):
        if destination == state_path:
            raise OSError("simulated interrupted replace")
        original_replace(source, destination)

    monkeypatch.setattr(
        durable_json,
        "_replace_path",
        fail_state_replace,
    )
    agent._session_history = [{"goal": "incomplete new state"}]

    with pytest.raises(JsonStorageError, match="atomically replace"):
        agent.save_session()

    monkeypatch.setattr(durable_json, "_replace_path", original_replace)
    restored = agent_factory()
    assert restored._session_history == [{"goal": "last complete state"}]
    assert list(state_path.parent.glob("*.tmp")) == []


def test_corrupt_active_marker_falls_back_to_usable_session(agent_factory):
    agent = agent_factory()
    agent._session_history = [{"goal": "recoverable"}]
    agent.save_session()
    marker = agent.executor.workspace / ".kvist" / "active_session.json"
    marker.write_text("{partial", encoding="utf-8")
    marker.with_suffix(".json.bak").write_text("{partial", encoding="utf-8")

    restored = agent_factory()

    assert restored.current_session == "default"
    assert restored._session_history == [{"goal": "recoverable"}]
    assert any(
        "active-session marker was damaged" in warning
        for warning in restored._storage_warnings
    )


def test_active_marker_symlink_is_a_hard_storage_error(agent_factory):
    agent = agent_factory()
    agent.save_session()
    marker = agent.executor.workspace / ".kvist" / "active_session.json"
    outside = agent.executor.workspace.parent / "outside-active-session.json"
    outside.write_text('{"session": "default"}\n', encoding="utf-8")
    marker.unlink()
    marker.symlink_to(outside)

    with pytest.raises(JsonStorageError, match="symbolic link"):
        agent_factory()

    assert json.loads(outside.read_text(encoding="utf-8")) == {
        "session": "default"
    }


def test_active_session_corruption_is_not_hidden_by_switching_sessions(
    agent_factory,
):
    agent = agent_factory()
    agent._session_history = [{"goal": "default"}]
    agent.save_session()
    agent.new_session("active")
    active_path = _session_path(agent, "active")
    active_path.write_text("{partial", encoding="utf-8")

    with pytest.raises(JsonStorageError, match="corrupt"):
        agent_factory()


def test_unknown_session_fields_are_ignored_without_losing_core_state(
    agent_factory,
):
    agent = agent_factory()
    state_path = _session_path(agent, "default")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "session": "default",
                "session_history": [{"goal": "known"}],
                "chat_history": [],
                "checkpoint": None,
                "future_optional_field": {"safe": True},
            }
        ),
        encoding="utf-8",
    )

    assert agent._load_session() is True
    assert agent._session_history == [{"goal": "known"}]
