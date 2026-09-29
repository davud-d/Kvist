# -*- coding: utf-8 -*-
from __future__ import annotations

import json

import pytest

from kernel.agent_logging import get_journal_path
from kernel.bug_report import (
    MAX_EVENT_FIELD_CHARS,
    build_bug_report,
    save_bug_report,
)
from kernel.durable_json import append_private_bytes


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


@pytest.mark.parametrize(
    "secret_line",
    [
        "ANYMODEL_KEY=abcd1234efgh5678ijkl",
        "deepseekKey: zyx987ab12cd34ef56gh",
        "X_TOKEN=mnop5678qrst1234uvwx",
    ],
)
def test_vendor_shaped_key_assignments_are_redacted(agent_factory, secret_line):
    agent = agent_factory()
    value = secret_line.split(("=" if "=" in secret_line else ":"), 1)[1].strip()
    _write_journal_events(agent, [{"type": "action_result", "output": secret_line}])

    report = build_bug_report(agent, frontend="terminal")

    assert value not in report
    assert "[REDACTED]" in report


def test_bare_high_entropy_token_is_redacted_even_without_a_key_prefix(agent_factory):
    agent = agent_factory()
    token = "zxq9mwer8tyui2opas3dfgh4jklzx6cvbnm1qwer"
    _write_journal_events(
        agent, [{"type": "action_result", "output": f"the run printed {token} then exited"}]
    )

    report = build_bug_report(agent, frontend="terminal")

    assert token not in report
    assert "[REDACTED]" in report


def test_git_commit_hash_is_not_redacted(agent_factory):
    agent = agent_factory()
    commit_hash = "a94a8fe5ccb19ba61c4c0873d391e987982fbbd"
    _write_journal_events(
        agent, [{"type": "action_result", "output": f"fixed in commit {commit_hash}"}]
    )

    report = build_bug_report(agent, frontend="terminal")

    assert commit_hash in report
    assert "[REDACTED]" not in report


def test_file_path_component_is_not_redacted(agent_factory):
    agent = agent_factory()
    path = "/home/user/.cache/uv/builds-v0/abcdefghij1234567890klmnopqrstuv/bin"
    _write_journal_events(agent, [{"type": "action_result", "output": f"wrote to {path}"}])

    report = build_bug_report(agent, frontend="terminal")

    assert path in report
    assert "[REDACTED]" not in report


def test_letters_only_or_digits_only_long_run_is_not_redacted(agent_factory):
    agent = agent_factory()
    letters_only = "abcdefghijklmnopqrstuvwxyzabcdefgh"
    digits_only = "12345678901234567890123456789012"
    _write_journal_events(
        agent,
        [{"type": "action_result", "output": f"{letters_only} and {digits_only}"}],
    )

    report = build_bug_report(agent, frontend="terminal")

    assert letters_only in report
    assert digits_only in report
    assert "[REDACTED]" not in report


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
