# -*- coding: utf-8 -*-
from __future__ import annotations

import json

import pytest

from agent_logging import get_journal_path
from bug_report import (
    MAX_EVENT_FIELD_CHARS,
    build_bug_report,
    save_bug_report,
)
from durable_json import append_private_bytes


def _write_journal_events(agent, events: list[dict]) -> None:
    path = get_journal_path(agent, agent.current_session)
    lines = b"".join(
        json.dumps({"seq": index, "turn": 1, "ts": 0.0, "event": event}).encode("utf-8")
        + b"\n"
        for index, event in enumerate(events, start=1)
    )
    append_private_bytes(path, lines, boundary=agent.executor.workspace)


def test_report_includes_environment_and_agent_state(agent_factory):
    agent = agent_factory(provider="deepseek", fast_model="deepseek-chat")
    report = build_bug_report(agent, frontend="terminal", routed_role="coder")

    assert "== Environment ==" in report
    assert "Kvist version:" in report
    assert "OS:" in report
    assert "Python:" in report
    assert "== Agent state ==" in report
    assert "Frontend: terminal" in report
    assert "Provider: deepseek" in report
    assert "Role: coder" in report
    assert f"Session: {agent.current_session}" in report


def test_user_text_is_placed_first(agent_factory):
    agent = agent_factory()
    report = build_bug_report(agent, frontend="terminal", user_text="  agent deleted my file  ")

    assert report.startswith("== User report ==\nagent deleted my file")


def test_recent_events_are_included_and_capped_at_50(agent_factory):
    agent = agent_factory()
    events = [
        {"type": "action_result", "output": f"line {i}"} for i in range(60)
    ]
    _write_journal_events(agent, events)

    report = build_bug_report(agent, frontend="terminal")

    assert "== Last 50 session events" in report
    # Only the newest 50 of 60 survive (read_history keeps the tail).
    assert '"output": "line 59"' in report
    assert '"output": "line 0"' not in report


def test_no_events_recorded_is_reported_honestly(agent_factory):
    agent = agent_factory()
    report = build_bug_report(agent, frontend="terminal")

    assert "== Last 0 session events" in report
    assert "(no events recorded)" in report


def test_tool_output_is_truncated_per_event(agent_factory):
    agent = agent_factory()
    long_output = "x" * 5000
    _write_journal_events(agent, [{"type": "action_result", "output": long_output}])

    report = build_bug_report(agent, frontend="terminal")

    # The raw 5000-char string must never appear whole in the report.
    assert long_output not in report
    assert "x" * MAX_EVENT_FIELD_CHARS in report
    assert "TRUNCATED" in report


@pytest.mark.parametrize(
    "secret_value",
    [
        "sk-test-1234567890abcdef",
        "Bearer abcdEFGH12345678ijklmnop",
        "api_key=abcdEFGH12345678ijklmnop",
        "token: abcdEFGH12345678ijklmnop",
    ],
)
def test_known_secret_patterns_are_redacted(agent_factory, secret_value):
    agent = agent_factory()
    _write_journal_events(
        agent, [{"type": "action_result", "output": f"leaked credential {secret_value} in output"}]
    )

    report = build_bug_report(agent, frontend="terminal")

    assert secret_value not in report
    assert "[REDACTED]" in report


def test_report_never_reflects_environment_variables(agent_factory, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realenvsecret1234567890")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-anotherenvsecret1234567")
    agent = agent_factory()

    report = build_bug_report(agent, frontend="terminal")

    assert "realenvsecret" not in report
    assert "anotherenvsecret" not in report


def test_report_does_not_read_dotenv_files(agent_factory, tmp_path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-test-dotenvsecret1234567890\n", encoding="utf-8")
    agent = agent_factory()

    report = build_bug_report(agent, frontend="terminal")

    assert "dotenvsecret" not in report
    assert ".env" not in report


def test_save_bug_report_writes_under_home_and_returns_path(agent_factory, tmp_path):
    agent = agent_factory()
    home = tmp_path / "home"
    home.mkdir()

    path = save_bug_report(agent, frontend="terminal", user_text="crash on save", home=home)

    assert path.parent == home / ".kvist" / "bug-reports"
    assert path.name.startswith("bug-")
    assert path.name.endswith(f"-{agent.current_session}.txt")
    content = path.read_text(encoding="utf-8")
    assert "crash on save" in content


def test_save_bug_report_secret_never_reaches_disk(agent_factory, tmp_path):
    agent = agent_factory()
    home = tmp_path / "home"
    home.mkdir()
    fake_key = "sk-test-1234567890abcdef"
    _write_journal_events(agent, [{"type": "action_result", "output": f"key={fake_key}"}])

    path = save_bug_report(agent, frontend="terminal", home=home)

    on_disk = path.read_bytes()
    assert fake_key.encode("utf-8") not in on_disk
    assert b"[REDACTED]" in on_disk
