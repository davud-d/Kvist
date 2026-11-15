# -*- coding: utf-8 -*-
"""Best-of and council events: payload -> EventStreamUI -> event dict -> journal (UI_API.md §7)."""
from __future__ import annotations

import json
import threading

import pytest

from kernel.protocol.event_stream_ui import EventStreamUI
from kernel.protocol.frontend_protocol import (
    BestofFinishedEvent,
    ExecutorActivityEvent,
    FrontendChannel,
    HelloEvent,
    InputRequestEvent,
)
from kernel.session import session_events
from kernel.session.session_events import SessionJournal

SECRET = "sk-proj-" + "u" * 24

_EXECUTOR = {"executor_id": "e1", "style": "careful", "branch": "bo/e1", "session": "bo-e1"}
_TESTS = {"state": "passed", "summary": "12 passed"}
_DIFF = {"files": 2, "added": 10, "removed": 3}

# method -> (payload, exactly the dict the event must serialize to)
_CASES = {
    "print_bestof_started": (
        {"run_id": "r1", "task": "fix it", "count": 2, "base": "abc1234", "executors": [_EXECUTOR]},
        {"type": "bestof_started", "run_id": "r1", "task": "fix it", "count": 2, "base": "abc1234",
         "executors": [_EXECUTOR]},
    ),
    "print_bestof_status": (
        {"run_id": "r1", "executor_id": "e1", "style": "careful", "branch": "bo/e1", "state": "running",
         "step": 4, "tests": _TESTS, "diff": _DIFF,
         "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 50, "cost_usd": 0.5,
                   "cost_approx": True}},
        {"type": "bestof_status", "run_id": "r1", "executor_id": "e1", "style": "careful", "branch": "bo/e1",
         "state": "running", "step": 4, "tests": _TESTS, "diff": _DIFF,
         "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 50, "cost_usd": 0.5,
                   "cost_approx": True}},
    ),
    "print_executor_activity": (
        {"run_id": "r1", "executor_id": "e1", "phase": "result", "call_id": "c1", "action_type": "run_shell",
         "label": "run tests", "ok": True, "summary": "exit 0", "output": "12 passed"},
        {"type": "executor_activity", "run_id": "r1", "executor_id": "e1", "phase": "result", "call_id": "c1",
         "action_type": "run_shell", "label": "run tests", "summary": "exit 0", "output": "12 passed",
         "ok": True},
    ),
    "print_bestof_eliminated": (
        {"run_id": "r1", "executor_id": "e2", "reason": "tests failed"},
        {"type": "bestof_eliminated", "run_id": "r1", "executor_id": "e2", "reason": "tests failed"},
    ),
    "print_bestof_verdict": (
        {"run_id": "r1", "winner": "e1", "reason": "smallest diff",
         "rows": [{"executor_id": "e1", "style": "careful", "outcome": "winner", "tests": _TESTS,
                   "diff": _DIFF, "note": "clean"}]},
        {"type": "bestof_verdict", "run_id": "r1", "winner": "e1", "reason": "smallest diff",
         "rows": [{"executor_id": "e1", "style": "careful", "outcome": "winner", "tests": _TESTS,
                   "diff": _DIFF, "note": "clean"}]},
    ),
    "print_bestof_diff": (
        {"run_id": "r1", "executor_id": "e1", "patch": "--- a\n+++ b\n", "files": ["a.py"]},
        {"type": "bestof_diff", "run_id": "r1", "executor_id": "e1", "patch": "--- a\n+++ b\n",
         "files": ["a.py"]},
    ),
    "print_bestof_finished": (
        {"run_id": "r1", "outcome": "accepted", "executor_id": "e1", "commit": "abc1234", "message": "taken"},
        {"type": "bestof_finished", "run_id": "r1", "outcome": "accepted", "message": "taken",
         "executor_id": "e1", "commit": "abc1234"},
    ),
    "print_council_started": (
        {"council_id": "k1", "question": "which db?",
         "members": [{"executor_id": "m1", "angle": "cost", "session": "k-m1"}]},
        {"type": "council_started", "council_id": "k1", "question": "which db?",
         "members": [{"executor_id": "m1", "angle": "cost", "session": "k-m1"}]},
    ),
    "print_council_card": (
        {"council_id": "k1", "executor_id": "m1", "angle": "cost", "decision": "sqlite", "argument": "cheap"},
        {"type": "council_card", "council_id": "k1", "executor_id": "m1", "angle": "cost",
         "decision": "sqlite", "argument": "cheap"},
    ),
    "print_council_objection": (
        {"council_id": "k1", "executor_id": "m1", "angle": "cost",
         "risks": [{"target": "m2", "risk": "locks"}]},
        {"type": "council_objection", "council_id": "k1", "executor_id": "m1", "angle": "cost",
         "risks": [{"target": "m2", "risk": "locks"}]},
    ),
    "print_council_verdict": (
        {"council_id": "k1", "chosen": "m1", "decision": "sqlite", "reason": "simple",
         "dissent": [{"executor_id": "m2", "angle": "scale", "why": "limits"}]},
        {"type": "council_verdict", "council_id": "k1", "chosen": "m1", "decision": "sqlite",
         "reason": "simple", "dissent": [{"executor_id": "m2", "angle": "scale", "why": "limits"}]},
    ),
}

# Methods whose events the journal keeps; the rest are live only.
_JOURNALED = {
    "print_bestof_started", "print_bestof_eliminated", "print_bestof_verdict", "print_bestof_finished",
    "print_council_started", "print_council_card", "print_council_objection", "print_council_verdict",
}


def _emit(method: str, payload: dict) -> list[dict]:
    channel = FrontendChannel()
    getattr(EventStreamUI(channel), method)(payload)
    return [event.to_dict() for event in channel.drain()]


@pytest.mark.parametrize("method", sorted(_CASES))
def test_each_method_sends_its_event_with_exactly_the_contract_fields(method):
    payload, expected = _CASES[method]
    assert _emit(method, payload) == [expected]


@pytest.mark.parametrize("method", sorted(_CASES))
def test_unknown_payload_keys_do_not_reach_the_event(method):
    payload, expected = _CASES[method]
    assert _emit(method, {**payload, "stray": "x", "api_key": SECRET}) == [expected]


def test_types_are_normalized():
    [event] = _emit("print_bestof_status", {
        "run_id": 7, "executor_id": 1, "state": "running", "step": "3",
        "diff": {"files": "2", "added": "5", "removed": 0}, "usage": {"input_tokens": "9", "output_tokens": 1.0},
    })
    assert event["run_id"] == "7" and event["executor_id"] == "1" and event["step"] == 3
    assert event["diff"] == {"files": 2, "added": 5, "removed": 0}
    assert event["usage"] == {"input_tokens": 9, "output_tokens": 1, "cost_approx": False}
    assert event["tests"] == {"state": "unknown", "summary": ""}


def test_status_usage_leaves_out_unknown_cache_and_cost():
    [event] = _emit("print_bestof_status", {
        "run_id": "r1", "executor_id": "e1", "state": "waiting",
        "usage": {"input_tokens": 1, "output_tokens": 2, "cache_read_tokens": None, "cost_usd": None},
    })
    assert event["usage"] == {"input_tokens": 1, "output_tokens": 2, "cost_approx": False}


def test_status_usage_keeps_a_known_zero_cost():
    [event] = _emit("print_bestof_status", {
        "run_id": "r1", "executor_id": "e1", "state": "finished",
        "usage": {"input_tokens": 1, "output_tokens": 2, "cache_read_tokens": 0, "cost_usd": 0.0},
    })
    assert event["usage"]["cache_read_tokens"] == 0 and event["usage"]["cost_usd"] == 0.0


def test_activity_without_ok_leaves_the_key_out():
    [event] = _emit("print_executor_activity", {
        "run_id": "r1", "executor_id": "e1", "phase": "step", "call_id": "c1", "action_type": "read_file",
    })
    assert "ok" not in event
    assert event["label"] == "" and event["summary"] == "" and event["output"] == ""


def test_activity_keeps_a_false_ok():
    [event] = _emit("print_executor_activity", {
        "run_id": "r1", "executor_id": "e1", "phase": "result", "call_id": "c1", "action_type": "x", "ok": False,
    })
    assert event["ok"] is False


def test_finished_leaves_out_executor_and_commit_when_not_set():
    assert BestofFinishedEvent("r1", "cancelled", "stopped").to_dict() == {
        "type": "bestof_finished", "run_id": "r1", "outcome": "cancelled", "message": "stopped",
    }
    [event] = _emit("print_bestof_finished", {"run_id": "r1", "outcome": "pending", "message": "waiting"})
    assert "executor_id" not in event and "commit" not in event


def test_activity_event_class_omits_ok_until_it_is_known():
    event = ExecutorActivityEvent("r1", "e1", "step", "c1", "run_shell")
    assert "ok" not in event.to_dict()


def test_empty_collections_are_sent_as_empty_lists():
    [started] = _emit("print_bestof_started", {"run_id": "r1", "count": 0})
    [verdict] = _emit("print_bestof_verdict", {"run_id": "r1"})
    [objection] = _emit("print_council_objection", {"council_id": "k1", "executor_id": "m1"})
    [council] = _emit("print_council_verdict", {"council_id": "k1"})
    assert started["executors"] == [] and started["base"] == ""
    assert verdict["rows"] == [] and verdict["winner"] == ""
    assert objection["risks"] == []
    assert council["dissent"] == [] and council["chosen"] == ""


def _with_secret(value):
    if isinstance(value, str):
        return f"{value} {SECRET}"
    if isinstance(value, list):
        return [_with_secret(item) for item in value]
    if isinstance(value, dict):
        return {key: _with_secret(item) for key, item in value.items()}
    return value


# Fields that name things (ids, states, branches, sessions, paths) are not prose and stay as they are.
_STRUCTURAL = {
    "run_id", "council_id", "executor_id", "session", "branch", "base", "state", "phase", "call_id",
    "action_type", "outcome", "winner", "chosen", "target", "commit", "files",
}


def _secret_in_prose(value):
    if isinstance(value, list):
        return [_secret_in_prose(item) for item in value]
    if isinstance(value, dict):
        return {key: item if key in _STRUCTURAL else _secret_in_prose(item) for key, item in value.items()}
    return _with_secret(value) if isinstance(value, str) else value


@pytest.mark.parametrize("method", sorted(_CASES))
def test_secrets_are_redacted_from_every_prose_field(method):
    payload, _ = _CASES[method]
    [event] = _emit(method, _secret_in_prose(payload))
    assert SECRET not in json.dumps(event)


def test_a_large_patch_is_cut_with_a_marker():
    patch = "+x\n" * 20_000
    [event] = _emit("print_bestof_diff", {"run_id": "r1", "executor_id": "e1", "patch": patch})
    assert len(event["patch"]) < len(patch)
    assert event["patch"].startswith("+x\n")
    assert f"[... {len(patch) - 20_000} characters omitted ...]" in event["patch"]


def test_a_secret_on_the_patch_cut_line_is_redacted_before_the_cut():
    patch = "x" * 19_985 + f" {SECRET}\n" + "z\n" * 20_000
    [event] = _emit("print_bestof_diff", {"run_id": "r1", "executor_id": "e1", "patch": patch})
    assert "sk-proj" not in event["patch"]
    assert "[REDACTED]" in event["patch"]


def test_hello_protocol_version_is_still_1():
    assert HelloEvent().to_dict()["protocol_version"] == 1


# --- ask_decision ---------------------------------------------------------------


def _ask(ui_call, answer):
    channel = FrontendChannel()
    result: dict = {}

    def worker():
        result["value"] = ui_call(EventStreamUI(channel))

    thread = threading.Thread(target=worker)
    thread.start()
    event = channel.get(timeout=2)
    assert isinstance(event, InputRequestEvent)
    channel.respond(event.request_id, value=answer)
    thread.join(timeout=2)
    assert not thread.is_alive()
    return event, result["value"]


def test_ask_decision_sends_purpose_run_id_and_body():
    event, value = _ask(
        lambda ui: ui.ask_decision("Take a result?", [("e1", "Take e1"), ("none", "Discard")],
                                   body="e1 passed the tests", purpose="bestof_decision", run_id="r1"),
        "e1",
    )
    assert value == "e1"
    assert event.to_dict() == {
        "type": "input_request", "request_id": event.request_id, "kind": "choice",
        "title": "Take a result?", "body": "e1 passed the tests",
        "options": [{"value": "e1", "label": "Take e1"}, {"value": "none", "label": "Discard"}],
        "current_value": "", "help_text": "", "purpose": "bestof_decision", "run_id": "r1",
    }


def test_ask_decision_without_purpose_and_run_id_has_no_such_keys():
    event, value = _ask(lambda ui: ui.ask_decision("Pick", [("a", "A")]), "a")
    assert value == "a"
    payload = event.to_dict()
    assert "purpose" not in payload and "run_id" not in payload
    assert payload["body"] == ""


def test_ask_decision_returns_none_when_cancelled_like_ask_choice():
    _, value = _ask(lambda ui: ui.ask_decision("Pick", [("a", "A")]), None)
    assert value is None


def test_ask_decision_redacts_title_and_body():
    event, _ = _ask(lambda ui: ui.ask_decision(f"Pick {SECRET}", [("a", "A")], body=f"see {SECRET}"), "a")
    assert SECRET not in json.dumps(event.to_dict())


def test_ordinary_requests_have_no_purpose_or_run_id():
    event, _ = _ask(lambda ui: ui.ask_choice("Pick", [("a", "A")]), "a")
    assert "purpose" not in event.to_dict() and "run_id" not in event.to_dict()


# --- journal ---------------------------------------------------------------------


class _BareUI:
    """A UI that knows none of the optional best-of methods."""


def _journaled(tmp_path, calls, live=True, stamp=None):
    """Play ``calls`` [(method, payload)] through a JournalingUI; return (live events, journal events)."""
    path = tmp_path / ".kvist" / "sessions" / "s.events.jsonl"
    channel = FrontendChannel()
    journal = SessionJournal(tmp_path, lambda: path, print, stamp=stamp)
    ui = session_events.JournalingUI(EventStreamUI(channel) if live else _BareUI(), journal)
    journal.begin_turn("go")
    for method, payload in calls:
        getattr(ui, method)(payload)
    journal.end_turn()
    seen = [event.to_dict() for event in channel.drain()]
    kept = [entry["event"] for entry in session_events.read_history(path, tmp_path)["events"]]
    return seen, kept


@pytest.mark.parametrize("method", sorted(_CASES))
def test_journal_keeps_exactly_the_marked_events_and_forwards_all(tmp_path, method):
    payload, expected = _CASES[method]
    seen, kept = _journaled(tmp_path, [(method, payload)])
    assert seen == [expected]
    recorded = [event for event in kept if event["type"] == expected["type"]]
    assert recorded == ([expected] if method in _JOURNALED else [])


def test_journal_records_even_when_the_live_ui_lacks_the_methods(tmp_path):
    calls = [(method, _CASES[method][0]) for method in sorted(_CASES)]
    _, kept = _journaled(tmp_path, calls, live=False)
    assert {event["type"] for event in kept} >= {_CASES[method][1]["type"] for method in _JOURNALED}
    live_only = {_CASES[method][1]["type"] for method in _CASES if method not in _JOURNALED}
    assert not live_only & {event["type"] for event in kept}


def test_journaled_event_is_redacted_like_the_live_one(tmp_path):
    payload = {"council_id": "k1", "executor_id": "m1", "angle": "cost", "decision": "x",
               "argument": f"token {SECRET}"}
    seen, kept = _journaled(tmp_path, [("print_council_card", payload)])
    assert SECRET not in json.dumps(seen) and SECRET not in json.dumps(kept)


# --- stamp -----------------------------------------------------------------------


def test_stamped_journal_marks_all_the_events_it_writes(tmp_path):
    path = tmp_path / ".kvist" / "sessions" / "c.events.jsonl"
    journal = SessionJournal(tmp_path, lambda: path, print, stamp={"executor_id": "e2"})
    ui = session_events.JournalingUI(EventStreamUI(FrontendChannel()), journal)
    journal.begin_turn("child task")
    ui.print_result("tests pass")
    journal.end_turn()

    events = [entry["event"] for entry in session_events.read_history(path, tmp_path)["events"]]
    assert [event["type"] for event in events] == ["user", "message", "turn_end"]
    assert all(event["executor_id"] == "e2" for event in events)


def test_journal_without_stamp_has_no_executor_id(tmp_path):
    path = tmp_path / ".kvist" / "sessions" / "m.events.jsonl"
    journal = SessionJournal(tmp_path, lambda: path, print)
    ui = session_events.JournalingUI(EventStreamUI(FrontendChannel()), journal)
    journal.begin_turn("task")
    ui.print_result("done")
    journal.end_turn()

    events = [entry["event"] for entry in session_events.read_history(path, tmp_path)["events"]]
    assert events and all("executor_id" not in event for event in events)


def test_stamp_does_not_overwrite_a_key_the_event_has(tmp_path):
    payload = {"run_id": "r1", "executor_id": "e9", "reason": "tests failed"}
    _, kept = _journaled(tmp_path, [("print_bestof_eliminated", payload)], stamp={"executor_id": "e2"})
    [eliminated] = [event for event in kept if event["type"] == "bestof_eliminated"]
    assert eliminated["executor_id"] == "e9"
    [user] = [event for event in kept if event["type"] == "user"]
    assert user["executor_id"] == "e2"


def test_stamp_survives_compaction_including_the_omitted_turns_notice(tmp_path, monkeypatch):
    monkeypatch.setattr(session_events, "MAX_SESSION_TURNS", 2)
    monkeypatch.setattr(session_events, "KEEP_SESSION_TURNS", 1)
    path = tmp_path / ".kvist" / "sessions" / "c.events.jsonl"
    journal = SessionJournal(tmp_path, lambda: path, print, stamp={"executor_id": "e2"})
    ui = session_events.JournalingUI(EventStreamUI(FrontendChannel()), journal)
    for number in range(4):
        journal.begin_turn(f"task {number}")
        ui.print_result(f"done {number}")
        journal.end_turn()

    events = [entry["event"] for entry in session_events.read_history(path, tmp_path)["events"]]
    notices = [event for event in events if event["type"] == "journal_notice" and "omitted_turns" in event]
    assert len(notices) == 1 and notices[0]["omitted_turns"] >= 1
    assert "task 0" not in json.dumps(events)
    assert all(event["executor_id"] == "e2" for event in events)
