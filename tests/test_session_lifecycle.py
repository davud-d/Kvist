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

pytestmark = pytest.mark.slow

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


def test_sessions_keep_independent_checkpoints_and_histories(agent_factory):
    agent = agent_factory()
    agent.new_session("alpha")
    agent._session_history = [{"goal": "alpha history"}]
    agent._save_checkpoint("alpha task", 3)

    agent.new_session("beta")
    agent._session_history = [{"goal": "beta history"}]
    agent._save_checkpoint("beta task", 7)

    agent.switch_session("alpha")
    assert agent._session_history == [{"goal": "alpha history"}]
    assert agent._load_checkpoint() == ("alpha task", 3)

    agent.switch_session("beta")
    assert agent._session_history == [{"goal": "beta history"}]
    assert agent._load_checkpoint() == ("beta task", 7)


def test_completing_one_session_task_keeps_other_checkpoints(agent_factory):
    agent = agent_factory()
    agent.new_session("alpha")
    agent._save_checkpoint("alpha task", 3)
    agent.new_session("beta")
    agent._save_checkpoint("beta task", 2)

    # The runtime clears the checkpoint of the completed task's own session.
    agent._clear_checkpoint()

    assert agent._load_checkpoint() is None
    agent.switch_session("alpha")
    assert agent._load_checkpoint() == ("alpha task", 3)


def test_switch_to_missing_session_fails_without_creating_it(agent_factory):
    agent = agent_factory()
    agent._session_history = [{"goal": "keep"}]
    agent.save_session()

    with pytest.raises(ValueError, match="Unknown session"):
        agent.switch_session("ghost")

    assert agent.current_session == "default"
    assert agent._session_history == [{"goal": "keep"}]
    assert not _session_path(agent, "ghost").exists()


def test_active_session_cannot_be_deleted(agent_factory):
    agent = agent_factory()
    agent.new_session("busy")

    with pytest.raises(ValueError, match="active session"):
        agent._delete_session("busy")

    assert _session_path(agent, "busy").is_file()
    assert agent.current_session == "busy"


def test_new_resets_only_the_active_session(agent_factory):
    agent = agent_factory()
    agent.new_session("other")
    agent._session_history = [{"goal": "other work"}]
    agent._save_checkpoint("other task", 4)
    agent.new_session("target")
    agent._session_history = [{"goal": "target work"}]
    agent._save_checkpoint("target task", 5)

    agent.new_session()

    assert agent.current_session == "target"
    assert agent._session_history == []
    assert agent._load_checkpoint() is None
    agent.switch_session("other")
    assert agent._session_history == [{"goal": "other work"}]
    assert agent._load_checkpoint() == ("other task", 4)


def test_interrupted_task_survives_switch_and_process_restart(tmp_path):
    environment = os.environ.copy()
    environment.update(
        {
            "AGENT_SANDBOX_MODE": "workspace",
            "AGENT_SANDBOX_NET": "deny",
            "AGENT_WRITE_APPROVAL": "0",
        }
    )
    interrupt_script = """
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
agent.new_session("feature")
agent._state.progress = 40
agent._save_checkpoint("незавершённая задача 🌲", 6)
agent.new_session("hotfix")
agent._save_checkpoint("другая задача", 2)
"""
    resume_script = """
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
started_on = agent.current_session
agent.switch_session("feature")
checkpoint = agent._load_checkpoint()
print(json.dumps({
    "started_on": started_on,
    "goal": checkpoint[0],
    "step": checkpoint[1],
    "progress": agent._state.progress,
}, ensure_ascii=False))
"""

    subprocess.run(
        [sys.executable, "-c", interrupt_script, str(tmp_path)],
        check=True,
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    resumed = subprocess.run(
        [sys.executable, "-c", resume_script, str(tmp_path)],
        check=True,
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )

    payload = json.loads(resumed.stdout)
    assert payload == {
        "started_on": "hotfix",
        "goal": "незавершённая задача 🌲",
        "step": 6,
        "progress": 40,
    }


def test_switching_away_and_back_preserves_checkpoint_exactly(agent_factory):
    agent = agent_factory()
    agent.new_session("alpha")
    agent._state.progress = 55
    agent._state.files_changed = ["app.py"]
    agent._save_checkpoint("finish the feature", 9)

    agent.new_session("beta")
    agent.switch_session("alpha")

    assert agent._load_checkpoint() == ("finish the feature", 9)
    assert agent._state.progress == 55
    assert agent._state.files_changed == ["app.py"]
