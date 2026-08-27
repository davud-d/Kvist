# -*- coding: utf-8 -*-
"""Phase 2: the multi-turn stdio transport (``serve_session_multi``).

Driven entirely with in-memory streams and fake agents — no real process, no
LLM. The single-turn ``serve_session`` path is asserted to be unchanged by the
``pump`` refactor.
"""
from __future__ import annotations

import json
import queue
import threading
import time

import pytest

import event_stdio
from event_session import EventSession
from event_stream_ui import EventStreamUI
from frontend_protocol import FrontendChannel


class _FakeConfig:
    provider = "openai"
    fast_model = "f"
    smart_model = "s"
    planner_model = "p"


class _ScriptedReader:
    """A stdin-like iterator whose lines the test pushes on demand.

    ``push`` supplies the next line; ``eof`` ends iteration (stdin closed).
    Blocking on an empty queue models a live stdin between turns.
    """

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()

    def push(self, line: str) -> None:
        self._q.put(line)

    def eof(self) -> None:
        self._q.put(None)

    def __iter__(self):
        return self

    def __next__(self) -> str:
        item = self._q.get()
        if item is None:
            raise StopIteration
        return item


class _Collector:
    """Thread-safe out_stream capturing one NDJSON line per write."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._lock = threading.Lock()

    def write(self, chunk: str) -> None:
        with self._lock:
            self._lines.append(chunk)

    def flush(self) -> None:
        pass

    def raw(self) -> list[str]:
        with self._lock:
            return list(self._lines)

    def events(self) -> list[dict]:
        return [json.loads(line) for line in self.raw() if line.strip()]

    def wait_for(self, predicate, timeout: float = 3.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for event in self.events():
                if predicate(event):
                    return event
            time.sleep(0.01)
        raise TimeoutError(f"event never arrived; got {self.events()}")


class _ScriptAgent:
    """Fake agent driving deterministic turns through the real EventStreamUI."""

    def __init__(self, ui: EventStreamUI, handler) -> None:
        self._ui = ui
        self._handler = handler
        self.config = _FakeConfig()
        self.current_session = "default"
        self.goals: list[str] = []
        self.cancel_calls = 0
        self._cancel = threading.Event()

    def _peek_checkpoint(self):
        return None

    def request_cancel(self) -> None:
        self.cancel_calls += 1
        self._cancel.set()

    def run(self, user_goal: str, routed_role: str | None = None) -> str:
        self._cancel.clear()
        self.goals.append(user_goal)
        return self._handler(self, self._ui, user_goal)


def _run_multi(handler):
    """Wire a channel/ui/session/agent and run serve_session_multi on a thread."""
    channel = FrontendChannel()
    ui = EventStreamUI(channel)
    agent = _ScriptAgent(ui, handler)
    session = EventSession(channel, agent)
    reader = _ScriptedReader()
    out = _Collector()

    thread = threading.Thread(
        target=event_stdio.serve_session_multi,
        kwargs={"session": session, "out_stream": out, "in_reader": reader},
        daemon=True,
    )
    thread.start()
    return agent, reader, out, thread


def _simple_handler(agent, ui, goal):
    ui.print_final(f"done {goal}")
    return f"done {goal}"


def test_two_goals_one_process_two_full_event_sets():
    agent, reader, out, thread = _run_multi(_simple_handler)

    reader.push("goal one")
    out.wait_for(lambda e: e["type"] == "final" and "goal one" in e["message"])
    reader.push("goal two")
    out.wait_for(lambda e: e["type"] == "final" and "goal two" in e["message"])
    reader.eof()
    thread.join(timeout=3.0)

    assert not thread.is_alive()
    events = out.events()
    assert [e["type"] for e in events].count("hello") == 1
    assert events[0]["type"] == "hello"
    finals = [e for e in events if e["type"] == "final"]
    assert len(finals) == 2
    assert agent.goals == ["goal one", "goal two"]


def test_input_response_reaches_session_in_second_turn():
    def handler(agent, ui, goal):
        if goal == "ask":
            value = ui.prompt_text("your answer?")
            ui.print_final(f"got {value}")
            return f"got {value}"
        ui.print_final(f"done {goal}")
        return f"done {goal}"

    agent, reader, out, thread = _run_multi(handler)

    reader.push("first")
    out.wait_for(lambda e: e["type"] == "final" and "first" in e["message"])

    reader.push("ask")
    request = out.wait_for(lambda e: e["type"] == "input_request")
    reader.push(json.dumps({"request_id": request["request_id"], "value": "42"}))

    final = out.wait_for(lambda e: e["type"] == "final" and "got 42" in e["message"])
    assert "42" in final["message"]

    reader.eof()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


def test_cancel_interrupts_only_current_turn_process_survives():
    def handler(agent, ui, goal):
        if goal == "block":
            # Runs until phase-1 cancellation is requested over the wire.
            assert agent._cancel.wait(3.0)
            ui.print_final("stopped")
            return "stopped"
        ui.print_final(f"done {goal}")
        return f"done {goal}"

    agent, reader, out, thread = _run_multi(handler)

    reader.push("block")
    # Give the worker time to enter its wait, then cancel over the wire.
    time.sleep(0.05)
    reader.push(json.dumps({"type": "cancel"}))
    out.wait_for(lambda e: e["type"] == "final" and e["message"] == "stopped")
    assert agent.cancel_calls == 1

    # The process is still alive and serves the next goal.
    reader.push("after")
    out.wait_for(lambda e: e["type"] == "final" and "after" in e["message"])
    assert agent.goals == ["block", "after"]

    reader.eof()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


def test_eof_between_turns_ends_process():
    agent, reader, out, thread = _run_multi(_simple_handler)
    reader.eof()
    thread.join(timeout=3.0)
    assert not thread.is_alive()
    # Only the hello was emitted; no goal ran.
    assert [e["type"] for e in out.events()] == ["hello"]
    assert agent.goals == []


def test_single_turn_serve_session_unchanged_by_refactor():
    channel = FrontendChannel()
    ui = EventStreamUI(channel)
    agent = _ScriptAgent(ui, _simple_handler)
    session = EventSession(channel, agent)
    reader = _ScriptedReader()
    out = _Collector()

    thread = threading.Thread(
        target=event_stdio.serve_session,
        kwargs={
            "session": session,
            "out_stream": out,
            "in_reader": reader,
            "goal": "only goal",
        },
        daemon=True,
    )
    thread.start()
    out.wait_for(lambda e: e["type"] == "final")
    reader.eof()
    thread.join(timeout=3.0)

    assert not thread.is_alive()
    events = out.events()
    assert events[0]["type"] == "hello"
    assert [e["type"] for e in events if e["type"] == "final"]
    assert agent.goals == ["only goal"]
