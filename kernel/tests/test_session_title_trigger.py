# -*- coding: utf-8 -*-
"""When session-title generation is (and is not) started, and how /rename
(``agent.rename_session``) blocks it — see session_titler.py and the
``_add_to_session_history`` hook in agent.py."""
from __future__ import annotations

import json


def _session_path(agent, name: str):
    return agent.executor.workspace / ".kvist" / "sessions" / f"{name}.json"


def _read_session(agent, name: str) -> dict:
    return json.loads(_session_path(agent, name).read_text(encoding="utf-8"))


def test_first_completed_turn_starts_title_generation(agent_factory):
    agent = agent_factory()
    calls: list[tuple] = []
    agent._start_title_generation = lambda *args: calls.append(args)

    agent._add_to_session_history("fix the login bug", "coder", "Fixed it.")

    assert calls == [(agent.current_session, "fix the login bug", "Fixed it.")]


def test_second_turn_does_not_start_generation_again(agent_factory):
    agent = agent_factory()
    calls: list[tuple] = []

    def fake_start(session_name, user_goal, agent_reply):
        calls.append((session_name, user_goal, agent_reply))
        # Mirror what the real background worker does on success: store the
        # title, which is what future turns check to skip re-generation.
        agent._store_session_title(session_name, "Fix the login bug")

    agent._start_title_generation = fake_start

    agent._add_to_session_history("fix the login bug", "coder", "Fixed it.")
    agent._add_to_session_history("now also fix the logout bug", "coder", "Fixed that too.")

    assert len(calls) == 1


def test_stopped_turn_does_not_start_generation(agent_factory):
    agent = agent_factory()
    calls: list[tuple] = []
    agent._start_title_generation = lambda *args: calls.append(args)

    agent._add_to_session_history(
        "fix the login bug", "coder", "Stopped.", stopped_by_user=True
    )

    assert calls == []


def test_rename_before_first_turn_blocks_generation(agent_factory):
    agent = agent_factory()
    calls: list[tuple] = []
    agent._start_title_generation = lambda *args: calls.append(args)

    agent.rename_session("My custom title")
    agent._add_to_session_history("fix the login bug", "coder", "Fixed it.")

    assert calls == []
    assert agent._session_title == "My custom title"
    assert _read_session(agent, agent.current_session)["title_manual"] is True


def test_store_session_title_never_overwrites_a_manual_title(agent_factory):
    agent = agent_factory()
    agent.rename_session("My custom title")

    agent._store_session_title(agent.current_session, "Generated title")

    assert _read_session(agent, agent.current_session)["title"] == "My custom title"
    assert agent._session_title == "My custom title"


def test_resumed_session_with_existing_history_never_starts_generation(agent_factory):
    """A session loaded with history already in it (the /resume case) must
    never trigger generation — only a brand-new session's first turn does."""
    agent = agent_factory()
    agent._session_history = [{"goal": "earlier turn", "role": "coder", "result": "ok"}]
    calls: list[tuple] = []
    agent._start_title_generation = lambda *args: calls.append(args)

    agent._add_to_session_history("continue the work", "coder", "Done.")

    assert calls == []


def test_early_request_names_the_session_from_the_goal_alone(agent_factory):
    agent = agent_factory()
    calls: list[tuple] = []
    agent._start_title_generation = lambda *args: calls.append(args)

    agent._request_session_title("fix the login bug")

    assert calls == [(agent.current_session, "fix the login bug", "")]


def test_early_request_is_not_repeated_by_the_end_of_turn_hook(agent_factory):
    agent = agent_factory()
    calls: list[tuple] = []
    agent._start_title_generation = lambda *args: calls.append(args)

    agent._request_session_title("fix the login bug")
    agent._add_to_session_history("fix the login bug", "coder", "Fixed it.")

    assert len(calls) == 1


def test_early_request_respects_a_manual_title(agent_factory):
    agent = agent_factory()
    calls: list[tuple] = []
    agent._start_title_generation = lambda *args: calls.append(args)

    agent.rename_session("Mine")
    agent._request_session_title("fix the login bug")

    assert calls == []
