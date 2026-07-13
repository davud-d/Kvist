from __future__ import annotations

import json

import pytest

from agent_logging import get_state_path
from tests.conftest import make_plan


def test_sanitize_for_log_redacts_secrets_and_truncates(agent_factory):
    agent = agent_factory()
    data = {
        "token": "sk-" + "a" * 24,
        "nested": ["ghp_" + "b" * 40],
        "long": "x" * 400,
    }

    sanitized = agent._sanitize_for_log(data, max_str_len=20)

    assert sanitized["token"] == "[REDACTED]"
    assert sanitized["nested"] == ["[REDACTED]"]
    assert sanitized["long"].endswith("[TRUNCATED]")


def test_session_path_rejects_path_traversal(agent_factory):
    agent = agent_factory()

    with pytest.raises(ValueError, match="Invalid session name"):
        get_state_path(agent, "../outside")


def test_checkpoint_roundtrip_and_clear(agent_factory):
    agent = agent_factory(persist_logs=True)
    agent._state.plan_info = make_plan()
    agent._state.progress = 42
    agent._state.files_read = ["ui.py"]
    agent._session_history = [{"goal": "earlier", "role": "chat", "result": "done"}]
    agent._chat_history = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "hello"},
    ]

    agent._save_checkpoint("goal", 3)
    agent._state.progress = 0
    result = agent._load_checkpoint()

    assert result == ("goal", 3)
    assert agent._state.progress == 42
    assert agent._state.plan_info.goal == "Test goal"
    agent._clear_checkpoint()

    state_path = agent.executor.workspace / ".kvist" / "sessions" / "default.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["checkpoint"] is None
    assert payload["session_history"] == agent._session_history
    assert payload["chat_history"] == agent._chat_history
    assert agent._load_checkpoint() is None


def test_save_session_restores_active_session_and_full_chat_history(agent_factory):
    agent = agent_factory()
    agent.current_session = "feature-work"
    agent._session_history = [
        {"goal": "implement feature", "role": "coder", "result": "complete"}
    ]
    agent._chat_history = [
        {"role": "system", "content": "prompt"},
        *[
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"message-{index}"}
            for index in range(14)
        ],
    ]
    agent.save_session()

    restored = agent_factory()

    assert restored.current_session == "feature-work"
    assert restored._session_history == agent._session_history
    assert restored._chat_history == agent._chat_history
    assert len(restored._chat_history) == 15


def test_load_checkpoint_supports_legacy_flat_session_file(agent_factory):
    agent = agent_factory()
    agent.current_session = "legacy"
    state_path = agent.executor.workspace / ".kvist" / "sessions" / "legacy.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "goal": "legacy goal",
                "step": 4,
                "progress": 25,
                "session_history": [
                    {"goal": "older", "role": "chat", "result": "ok"}
                ],
                "chat_history": [{"role": "system", "content": "legacy prompt"}],
            }
        ),
        encoding="utf-8",
    )

    assert agent._load_checkpoint() == ("legacy goal", 4)
    assert agent._state.progress == 25
    assert agent._session_history[0]["goal"] == "older"
    assert agent._chat_history[0]["content"] == "legacy prompt"


def test_persist_logs_writes_sanitized_run_payload(agent_factory):
    agent = agent_factory(persist_logs=True)
    agent._state.plan_info = make_plan()
    agent._state.step_logs = [{"secret": "sk-" + "a" * 24}]

    agent._persist_logs("goal")
    log_path = next((agent.executor.workspace / agent.config.logs_dirname).glob("run_*.json"))
    payload = json.loads(log_path.read_text(encoding="utf-8"))

    assert payload["goal"] == "goal"
    assert payload["steps"][0]["secret"] == "[REDACTED]"
