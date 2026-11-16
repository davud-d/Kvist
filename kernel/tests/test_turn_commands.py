# -*- coding: utf-8 -*-
"""Commands that are turns (``/council``) over ``--frontend stdio-multi``: the real agent, a scripted model.

A turn command streams what a goal streams, then ``turn_end``, then ``command_result``; it is journaled as an
ordinary turn; an empty question is refused without a turn; ``cancel`` stops its members.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from kernel.agent.agent import LocalAgent
from kernel.agent.agent_state import AgentConfig
from kernel.bestof.turns import parse_turn_goal
from kernel.protocol import event_stdio
from kernel.protocol.core_manifest import build_manifest
from kernel.protocol.event_session import EventSession
from kernel.protocol.event_stream_ui import EventStreamUI
from kernel.protocol.frontend_protocol import FrontendChannel
from kernel.protocol.slash_commands import STDIO_ONLY_COMMANDS
from kernel.sandbox.executor import SafeExecutor
from kernel.session.agent_logging import get_journal_path
from kernel.session.session_events import read_history
from kernel.tests.conftest import NullPerfLogger
from kernel.tests.test_council import DECISIONS, QUESTION, CouncilClient, hold_all, verdict_text, wait_until
from kernel.tests.test_event_stdio_multi import _Collector, _ScriptedReader


@pytest.fixture(autouse=True)
def _text_protocol(monkeypatch):
    monkeypatch.setenv("AGENT_NATIVE_TOOLS", "0")


class Served:
    def __init__(self, tmp_path: Path, client) -> None:
        channel = FrontendChannel()
        config = AgentConfig(persist_logs=False, api_request_interval=0, retry_base_delay=0, max_steps=8, stream=False)
        self.agent = LocalAgent(client, SafeExecutor(tmp_path), config, ui=EventStreamUI(channel))
        self.agent._perf_logger = NullPerfLogger()
        self.workspace = tmp_path
        self.reader = _ScriptedReader()
        self.out = _Collector()
        self.thread = threading.Thread(
            target=event_stdio.serve_session_multi,
            kwargs={"session": EventSession(channel, self.agent), "out_stream": self.out, "in_reader": self.reader},
            daemon=True,
        )
        self.thread.start()

    def command(self, name: str, *args: str) -> None:
        self.reader.push(json.dumps({"type": "command", "name": name, "args": list(args)}))

    def types(self) -> list[str]:
        """The event types in order, without ``task_state`` (running/completed notices around the turn)."""
        return [event["type"] for event in self.out.events() if event["type"] != "task_state"]

    def result(self, name: str = "council") -> dict:
        return self.out.wait_for(lambda event: event["type"] == "command_result" and event["name"] == name, 20)

    def journal(self) -> list[dict]:
        page = read_history(get_journal_path(self.agent, self.agent.current_session), self.workspace)
        return [row["event"] for row in page["events"]]

    def finish(self) -> None:
        self.reader.eof()
        self.thread.join(5)
        assert not self.thread.is_alive()


@pytest.fixture
def served(tmp_path):
    started: list[Served] = []

    def start(client=None) -> Served:
        started.append(Served(tmp_path, client or CouncilClient()))
        return started[-1]

    yield start
    for item in started:
        if item.thread.is_alive():
            item.finish()


COUNCIL_EVENTS = ["council_started", "council_card", "council_card", "council_card", "council_card",
                  "council_objection", "council_objection", "council_objection", "council_objection",
                  "council_verdict"]


def _council_part(types: list[str]) -> list[str]:
    return [kind for kind in types if kind.startswith("council_")]


def test_the_command_message_runs_a_turn_then_turn_end_then_command_result(served):
    stdio = served()

    stdio.command("council", *QUESTION.split())
    result = stdio.result()
    stdio.finish()

    types = stdio.types()
    assert _council_part(types) == COUNCIL_EVENTS
    assert types[-3:] == ["final", "turn_end", "command_result"]
    assert result["ok"] is True
    assert result["effects"]["chosen"] == "security" and result["effects"]["decision"] == DECISIONS["security"]
    started = next(event for event in stdio.out.events() if event["type"] == "council_started")
    assert started["question"] == QUESTION
    final = next(event for event in stdio.out.events() if event["type"] == "final")
    assert final["outcome"] == "done" and final["message"].startswith("## council")


def test_a_goal_line_that_starts_with_the_command_is_the_same_turn(served):
    stdio = served()

    stdio.reader.push(f"/council {QUESTION}")
    result = stdio.result()
    stdio.finish()

    types = stdio.types()
    assert _council_part(types) == COUNCIL_EVENTS and types[-2:] == ["turn_end", "command_result"]
    assert result["ok"] is True


def test_a_json_goal_with_the_command_is_the_same_turn(served):
    stdio = served()

    stdio.reader.push(json.dumps({"type": "goal", "text": f"/Council {QUESTION}\nSecond line."}))
    result = stdio.result()
    stdio.finish()

    assert result["ok"] is True
    assert next(e for e in stdio.out.events() if e["type"] == "council_started")["question"] == \
        f"{QUESTION}\nSecond line."


@pytest.mark.parametrize("send", [
    lambda stdio: stdio.command("council"),
    lambda stdio: stdio.command("council", "  ", ""),
    lambda stdio: stdio.reader.push("/council"),
    lambda stdio: stdio.reader.push("/council    "),
])
def test_without_a_question_the_answer_is_a_refusal_and_there_is_no_turn(served, send):
    client = CouncilClient()
    stdio = served(client)

    send(stdio)
    result = stdio.result()

    assert result["ok"] is False and "/council <question>" in result["message"] and "/council <вопрос>" in result["message"]
    stdio.finish()
    assert stdio.types() == ["hello", "manifest", "command_result"]
    assert client.requests == []


def test_a_refusal_leaves_the_process_ready_for_the_real_question(served):
    client = CouncilClient()
    stdio = served(client)

    stdio.command("council")
    stdio.result()
    stdio.command("council", QUESTION)
    stdio.out.wait_for(lambda event: event["type"] == "turn_end", 20)
    stdio.finish()

    assert [event for event in stdio.out.events() if event["type"] == "command_result"][-1]["ok"] is True
    assert client.requests


def test_the_core_lists_the_command_in_hello_and_in_the_slash_menu_as_its_own(served):
    stdio = served()
    stdio.finish()

    hello = stdio.out.events()[0]
    assert "council" in hello["commands"] and "resume" in hello["commands"]
    manifest = next(event for event in stdio.out.events() if event["type"] == "manifest")
    entry = next(item for item in manifest["slash_commands"] if item["name"] == "council")
    assert entry["via"] == "core" and entry["description"]


def test_the_command_is_in_the_menu_but_not_claimed_by_a_core_that_does_not_serve_it():
    assert any(command.name == "council" for command in STDIO_ONLY_COMMANDS)
    entry = next(item for item in build_manifest(commands=["models"])["slash_commands"] if item["name"] == "council")
    assert "via" not in entry


def test_the_turn_is_journaled_like_any_turn_from_the_typed_text_to_turn_end(served):
    stdio = served()

    stdio.command("council", QUESTION)
    stdio.result()
    stdio.finish()

    events = stdio.journal()
    kinds = [event["type"] for event in events]
    assert events[0] == {"type": "user", "text": f"/council {QUESTION}"}
    assert _council_part(kinds) == COUNCIL_EVENTS
    assert kinds.index("council_started") < kinds.index("council_verdict") < kinds.index("final") < kinds.index("turn_end")
    assert kinds[-1] == "turn_end" and kinds.count("turn_end") == 1
    assert "stopped_by_user" not in events[-1]
    # The next ordinary message knows what the council decided.
    history = stdio.agent._session_history
    assert history and history[-1]["goal"] == f"/council {QUESTION}" and DECISIONS["security"] in history[-1]["result"]


def test_a_refused_command_writes_nothing_to_the_journal(served):
    stdio = served()

    stdio.command("council")
    stdio.result()
    stdio.finish()

    assert stdio.journal() == []


def test_a_second_council_while_one_runs_is_refused(served):
    hold = hold_all()
    client = CouncilClient(hold=hold)
    stdio = served(client)
    try:
        stdio.command("council", QUESTION)
        wait_until(lambda: len(client.requests) >= 1)
        stdio.command("council", "Another one?")
        refusal = stdio.result()
        assert refusal["ok"] is False and "running" in refusal["message"]
    finally:
        for event in hold.values():
            event.set()
    stdio.out.wait_for(lambda event: event["type"] == "turn_end", 20)
    stdio.finish()


def test_cancel_in_round_one_ends_the_turn_without_a_verdict_and_stops_the_members(served):
    hold = hold_all()
    client = CouncilClient(hold=hold)
    stdio = served(client)
    try:
        stdio.command("council", QUESTION)
        wait_until(lambda: len(client.requests) >= 1)
        stdio.reader.push(json.dumps({"type": "cancel"}))
        result = stdio.result()
    finally:
        for event in hold.values():
            event.set()
    stdio.finish()

    types = stdio.types()
    assert result["ok"] is False and result["effects"]["stopped"] is True
    assert "council_verdict" not in types and "council_objection" not in types
    assert types[-3:] == ["final", "turn_end", "command_result"]
    assert next(e for e in stdio.out.events() if e["type"] == "final")["outcome"] == "incomplete"
    assert stdio.journal()[-1] == {"type": "turn_end", "stopped_by_user": True}
    assert client.of("verdict") == []


def test_a_failed_council_is_still_a_turn_with_a_failed_command_result(served):
    bad = verdict_text("nobody")
    stdio = served(CouncilClient({("chooser", "verdict"): [bad, bad]}))

    stdio.command("council", QUESTION)
    result = stdio.result()
    stdio.finish()

    assert result["ok"] is False and "Nothing was chosen." in result["message"]
    assert stdio.types()[-3:] == ["final", "turn_end", "command_result"]
    assert "council_verdict" not in stdio.types()


@pytest.mark.parametrize(("text", "expected"), [
    ("/council which db?", ("council", "which db?")),
    ("  /COUNCIL   which db?  ", ("council", "which db?")),
    ("/council", ("council", "")),
    ("/council\nline one\nline two", ("council", "line one\nline two")),
    ("/councilor which db?", None),
    ("council which db?", None),
    ("please /council which db?", None),
    ("/resume", None),
])
def test_only_a_line_that_starts_with_the_command_is_a_turn_command(text, expected):
    assert parse_turn_goal(text) == expected
