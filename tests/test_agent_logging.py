from __future__ import annotations

import concurrent.futures
import json
import os
import threading
from pathlib import Path

import pytest

import agent_logging
from agent_logging import get_state_path
from tests.conftest import make_plan


def test_sanitize_for_log_redacts_secrets_and_truncates(agent_factory):
    agent = agent_factory()
    data = {
        "token": "sk-proj-" + "a" * 24,
        "nested": ["ghp_" + "b" * 40],
        "credentials": {"custom": "not-pattern-shaped"},
        "header": "Authorization: Bearer synthetic-value-123456",
        "long": "x" * 400,
    }

    sanitized = agent._sanitize_for_log(data, max_str_len=20)

    assert sanitized["token"] == "[REDACTED]"
    assert sanitized["nested"] == ["[REDACTED]"]
    assert sanitized["credentials"] == "[REDACTED]"
    assert "synthetic-value" not in sanitized["header"]
    assert sanitized["long"].endswith("[TRUNCATED]")


def test_session_path_rejects_path_traversal(agent_factory):
    agent = agent_factory()

    with pytest.raises(ValueError, match="Invalid session name"):
        get_state_path(agent, "../outside")


@pytest.mark.offline_smoke
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


def test_interrupted_save_keeps_the_last_valid_session_readable(
    agent_factory,
    monkeypatch,
):
    agent = agent_factory()
    agent.current_session = "feature-work"
    agent._session_history = [
        {"goal": "first task", "role": "coder", "result": "saved"}
    ]
    agent.save_session()

    # A second Ctrl+C lands inside the durable write's fsync.
    agent._session_history = [
        {"goal": "second task", "role": "coder", "result": "interrupted"}
    ]
    real_fsync = os.fsync

    def interrupt_fsync(descriptor):
        _ = descriptor
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "fsync", interrupt_fsync)
    with pytest.raises(KeyboardInterrupt):
        agent.save_session()
    monkeypatch.setattr(os, "fsync", real_fsync)

    restored = agent_factory()

    assert restored.current_session == "feature-work"
    assert restored._session_history == [
        {"goal": "first task", "role": "coder", "result": "saved"}
    ]


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
    synthetic = "sk-proj-" + "a" * 24
    agent._state.step_logs = [{"secret": synthetic}]
    agent._state.error_history = [f"Authorization: Bearer {synthetic}"]
    agent._state.user_messages = [f"password={synthetic}"]

    agent._persist_logs(f"goal api_key={synthetic}")
    log_path = next((agent.executor.workspace / agent.config.logs_dirname).glob("run_*.json"))
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    serialized = log_path.read_text(encoding="utf-8")

    assert synthetic not in serialized
    assert "[REDACTED]" in payload["goal"]
    assert "[REDACTED]" in payload["errors"][0]
    assert "[REDACTED]" in payload["user_messages"][0]
    assert payload["steps"][0]["secret"] == "[REDACTED]"


def test_session_persistence_redacts_credentials_before_disk(agent_factory):
    agent = agent_factory()
    synthetic = "sk-proj-" + "s" * 24
    agent._session_history = [
        {"goal": f"use token={synthetic}", "result": "fixture"}
    ]
    agent._chat_history = [
        {"role": "user", "content": f"Authorization: Bearer {synthetic}"}
    ]

    agent.save_session()

    state_path = get_state_path(agent)
    serialized = state_path.read_text(encoding="utf-8")
    restored = agent_logging.read_session_document(agent)
    assert synthetic not in serialized
    assert synthetic not in state_path.with_suffix(".json.bak").read_text(
        encoding="utf-8"
    )
    assert "[REDACTED]" in restored["session_history"][0]["goal"]
    assert "[REDACTED]" in restored["chat_history"][0]["content"]


def test_reading_legacy_session_scrubs_primary_and_recovery_copy(agent_factory):
    agent = agent_factory()
    synthetic = "sk-proj-" + "l" * 24
    state_path = get_state_path(agent)
    legacy = {
        "version": 1,
        "session": "default",
        "session_history": [{"goal": f"token={synthetic}"}],
        "chat_history": [],
    }
    agent_logging._write_json_object(
        state_path,
        legacy,
        boundary=agent.executor.workspace,
    )

    restored = agent_logging.read_session_document(agent)

    assert synthetic not in state_path.read_text(encoding="utf-8")
    assert synthetic not in state_path.with_suffix(".json.bak").read_text(
        encoding="utf-8"
    )
    assert "[REDACTED]" in restored["session_history"][0]["goal"]


def test_concurrent_run_log_allocation_never_overwrites(agent_factory):
    agent = agent_factory(persist_logs=True)
    agent._state.plan_info = make_plan()
    start = threading.Barrier(8)
    errors: list[BaseException] = []

    def persist() -> None:
        try:
            start.wait()
            agent._persist_logs("concurrent goal")
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=persist) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    paths = sorted(
        (agent.executor.workspace / agent.config.logs_dirname).glob(
            "run_*.json"
        )
    )
    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert len(paths) == 8
    assert len({path.name for path in paths}) == 8


def test_concurrent_session_writes_do_not_share_a_temp_file(tmp_path, monkeypatch):
    state_path = tmp_path / "session.json"
    replace_barrier = threading.Barrier(2)
    original_replace = Path.replace

    def synchronized_replace(source, target):
        if source.name == "session.json.tmp":
            replace_barrier.wait(timeout=5)
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", synchronized_replace)

    def write(writer):
        try:
            agent_logging._write_json_object(
                state_path,
                {"writer": writer, "payload": "x" * 1_000},
            )
        except Exception as error:
            return error
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        errors = list(pool.map(write, range(2)))

    assert errors == [None, None]
    assert json.loads(state_path.read_text(encoding="utf-8"))["writer"] in {0, 1}
    assert list(tmp_path.glob("*.tmp")) == []


def test_session_reader_rejects_symlink_escape(agent_factory):
    agent = agent_factory()
    state_path = get_state_path(agent)
    state_path.parent.mkdir(parents=True)
    outside = agent.executor.workspace.parent / "outside-session.json"
    outside.write_text(
        json.dumps(
            {
                "version": 1,
                "session_history": [{"result": "outside-marker"}],
            }
        ),
        encoding="utf-8",
    )
    state_path.symlink_to(outside)

    with pytest.raises(RuntimeError, match="symbolic link"):
        agent._load_session()


def test_corrupt_session_is_not_silently_treated_as_empty(agent_factory):
    agent = agent_factory()
    state_path = get_state_path(agent)
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="corrupt"):
        agent._load_session()


def test_corrupt_session_recovers_previous_valid_backup(agent_factory):
    agent = agent_factory()
    state_path = get_state_path(agent)
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{broken", encoding="utf-8")
    state_path.with_suffix(".json.bak").write_text(
        json.dumps(
            {
                "version": 1,
                "session": "default",
                "session_history": [{"goal": "recovered"}],
                "chat_history": [],
                "checkpoint": None,
            }
        ),
        encoding="utf-8",
    )

    assert agent._load_session() is True
    assert agent._session_history == [{"goal": "recovered"}]
    assert json.loads(state_path.read_text(encoding="utf-8"))["version"] == 1


def test_future_session_version_fails_with_actionable_error(agent_factory):
    agent = agent_factory()
    state_path = get_state_path(agent)
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 999,
                "session": "default",
                "session_history": [],
                "chat_history": [],
                "checkpoint": None,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="(?i)unsupported session version 999"):
        agent._load_session()
