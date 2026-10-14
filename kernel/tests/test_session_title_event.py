# -*- coding: utf-8 -*-
"""The session_title event: EventStreamUI -> JournalingUI -> agent (UI_API.md §7)."""
from __future__ import annotations

import json

from kernel import agent as agent_module
from kernel.event_stream_ui import EventStreamUI
from kernel.frontend_protocol import FrontendChannel, SessionTitleEvent
from kernel.session_events import JournalingUI, SessionJournal
from terminal_banner import TerminalBannerUI
from kernel.tests.conftest import NullUI


def test_event_stream_ui_emits_session_title_event():
    channel = FrontendChannel()
    EventStreamUI(channel).print_session_title("chat-1", "Fix the login bug")
    (event,) = channel.drain()
    assert isinstance(event, SessionTitleEvent)
    assert event.to_dict() == {
        "type": "session_title",
        "session": "chat-1",
        "title": "Fix the login bug",
    }


def test_session_title_event_carries_no_secret_leak_risk():
    # Sanity check on the payload shape only (no redaction is needed here: the
    # title itself is already the cleaned/fallback text, never raw tool output).
    payload = SessionTitleEvent("chat-1", "Add caching").to_dict()
    assert set(payload) == {"type", "session", "title"}
    json.dumps(payload)  # must be plain JSON-serializable


def test_classic_terminal_ui_names_the_window_from_the_title(monkeypatch):
    import window_title

    written: list[str] = []
    monkeypatch.setattr(window_title, "_write", written.append)
    monkeypatch.setattr(window_title, "_last_written", None)

    TerminalBannerUI.print_session_title(object(), "chat-1", "Add caching")

    assert written == ["\x1b]2;Kvist | Add caching\x07"]


class _RecordingUI(NullUI):
    def __init__(self):
        self.titles: list[tuple[str, str]] = []

    def print_session_title(self, session, title):
        self.titles.append((session, title))


def test_journaling_ui_forwards_session_title_live_but_never_journals_it(tmp_path):
    real_ui = _RecordingUI()
    journal = SessionJournal(tmp_path, lambda: tmp_path / "s.events.jsonl", lambda m: None)
    ui = JournalingUI(real_ui, journal)

    # No turn is open (title generation runs strictly after its turn ended):
    # the live UI still receives the event even though the journal drops it.
    ui.print_session_title("chat-1", "Add caching")

    assert real_ui.titles == [("chat-1", "Add caching")]
    assert not (tmp_path / "s.events.jsonl").exists()


def test_journaling_ui_without_the_optional_method_is_unaffected():
    journal = SessionJournal(None, lambda: None, lambda m: None)  # type: ignore[arg-type]
    ui = JournalingUI(NullUI(), journal)
    ui.print_session_title("chat-1", "Add caching")  # must not raise


def test_generate_and_store_title_notifies_the_ui(agent_factory, monkeypatch):
    agent = agent_factory()
    # A real call only ever happens after the turn's history entry (and thus
    # the session document) already exists — see _add_to_session_history.
    agent._session_history.append({"goal": "fix the login bug", "role": "chat", "result": "r"})
    agent.save_session()
    recorded: list[tuple[str, str]] = []
    agent._ui.print_session_title = lambda session, title: recorded.append((session, title))
    monkeypatch.setattr(
        agent_module, "generate_session_title", lambda *a, **k: "Fix the login bug"
    )

    agent._generate_and_store_title(agent.current_session, "fix the login bug", "Fixed it.")

    assert recorded == [(agent.current_session, "Fix the login bug")]
    assert agent._session_title == "Fix the login bug"
