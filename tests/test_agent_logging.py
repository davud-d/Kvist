from __future__ import annotations

import json

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


def test_checkpoint_roundtrip_and_clear(agent_factory):
    agent = agent_factory(persist_logs=True)
    agent._state.plan_info = make_plan()
    agent._state.progress = 42
    agent._state.files_read = ["ui.py"]

    agent._save_checkpoint("goal", 3)
    agent._state.progress = 0
    result = agent._load_checkpoint()

    assert result == ("goal", 3)
    assert agent._state.progress == 42
    assert agent._state.plan_info.goal == "Test goal"
    agent._clear_checkpoint()
    assert not (agent.executor.workspace / agent.config.state_filename).exists()


def test_persist_logs_writes_sanitized_run_payload(agent_factory):
    agent = agent_factory(persist_logs=True)
    agent._state.plan_info = make_plan()
    agent._state.step_logs = [{"secret": "sk-" + "a" * 24}]

    agent._persist_logs("goal")
    log_path = next((agent.executor.workspace / agent.config.logs_dirname).glob("run_*.json"))
    payload = json.loads(log_path.read_text(encoding="utf-8"))

    assert payload["goal"] == "goal"
    assert payload["steps"][0]["secret"] == "[REDACTED]"
