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

from kernel.protocol import event_stdio
from kernel.protocol.event_session import EventSession
from kernel.protocol.event_stream_ui import EventStreamUI
from kernel.protocol.frontend_protocol import FrontendChannel


class _FakeConfig:
    provider = "openai"
    fast_model = "f"
    smart_model = "s"


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


def test_goal_message_carries_line_breaks_to_the_agent():
    agent, reader, out, thread = _run_multi(_simple_handler)

    reader.push(json.dumps({"type": "goal", "text": "first\nsecond\n"}) + "\n")
    out.wait_for(lambda e: e["type"] == "final")
    reader.eof()
    thread.join(timeout=3.0)

    assert agent.goals == ["first\nsecond"]


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


def test_every_turn_ends_with_turn_end_even_without_final():
    def handler(agent, ui, goal):
        with ui.live_markdown() as update:
            update("a chat reply")
        return "a chat reply"

    agent, reader, out, thread = _run_multi(handler)

    reader.push("hi")
    out.wait_for(lambda e: e["type"] == "turn_end")
    reader.push("again")
    out.wait_for(lambda e: e["type"] == "turn_end" and [x["type"] for x in out.events()].count("turn_end") == 2)
    reader.eof()
    thread.join(timeout=3.0)

    types = [e["type"] for e in out.events()]
    assert "final" not in types
    assert types.count("turn_end") == 2
    assert types.index("stream_commit") < types.index("turn_end")


class _WakeAgent(_ScriptAgent):
    """A fake whose background work ends when the test says so."""

    def __init__(self, ui: EventStreamUI, handler) -> None:
        super().__init__(ui, handler)
        self.wake_listener = None
        self.news = False
        self.wakes = 0

    def wake_due(self) -> bool:
        return self.news

    def wake(self) -> str:
        self.news = False
        self.wakes += 1
        self._ui.print_final("the build finished")
        return "the build finished"

    def finish_background_work(self) -> None:
        """What a job's thread does when the job ends."""
        self._ui.print_job_finished("job_1", "exited", 0, "built\n")
        self.news = True
        self.wake_listener()


def _run_wake(handler):
    channel = FrontendChannel()
    ui = EventStreamUI(channel)
    agent = _WakeAgent(ui, handler)
    session = EventSession(channel, agent)
    reader = _ScriptedReader()
    out = _Collector()
    thread = threading.Thread(
        target=event_stdio.serve_session_multi,
        kwargs={"session": session, "out_stream": out, "in_reader": reader, "wake": True},
        daemon=True,
    )
    thread.start()
    out.wait_for(lambda e: e["type"] == "hello")
    return agent, ui, reader, out, thread


def _types(out: _Collector) -> list[str]:
    return [e["type"] for e in out.events()]


def test_work_that_ends_between_turns_wakes_the_agent_in_a_turn_of_its_own():
    agent, _ui, reader, out, thread = _run_wake(_simple_handler)
    reader.push("start the build in the background")
    out.wait_for(lambda e: e["type"] == "turn_end")

    agent.finish_background_work()
    out.wait_for(lambda e: e["type"] == "final" and e["message"] == "the build finished")
    out.wait_for(lambda e: e["type"] == "turn_end" and _types(out).count("turn_end") == 2)
    reader.eof()
    thread.join(timeout=3.0)

    types = _types(out)
    woken = types[types.index("turn_end") + 1:]
    # The finish that caused the wake-up is shown before the turn it opens.
    assert woken[:2] == ["job_finished", "wake"] and "final" in woken and woken[-1] == "turn_end"
    assert agent.wakes == 1
    assert agent.goals == ["start the build in the background"]


def test_work_that_ends_during_a_turn_wakes_the_agent_right_after_it():
    def handler(agent, ui, goal):
        # The job ends while the turn still runs: the listener finds the loop busy.
        agent.finish_background_work()
        ui.print_final("answered")
        return "answered"

    agent, _ui, reader, out, thread = _run_wake(handler)
    reader.push("go")
    out.wait_for(lambda e: e["type"] == "turn_end" and _types(out).count("turn_end") == 2)
    reader.eof()
    thread.join(timeout=3.0)

    types = _types(out)
    assert types.count("wake") == 1
    assert types.index("turn_end") < types.index("wake")
    assert agent.wakes == 1


def test_no_wake_turn_when_the_news_was_already_told():
    agent, _ui, reader, out, thread = _run_wake(_simple_handler)
    agent.wake_listener()  # a finish the person's next turn already reported
    reader.push("hello")
    out.wait_for(lambda e: e["type"] == "turn_end")
    reader.eof()
    thread.join(timeout=3.0)

    assert "wake" not in _types(out)
    assert agent.wakes == 0


def test_with_wake_ups_events_between_turns_are_written_at_once():
    agent, ui, reader, out, thread = _run_wake(_simple_handler)

    ui.print_job_finished("job_1", "exited", 0, "built\n")
    out.wait_for(lambda e: e["type"] == "job_finished")
    reader.eof()
    thread.join(timeout=3.0)

    assert "turn_end" not in _types(out)


def test_without_the_wake_flag_the_agent_gets_no_listener():
    channel = FrontendChannel()
    ui = EventStreamUI(channel)
    agent = _WakeAgent(ui, _simple_handler)
    reader = _ScriptedReader()
    reader.eof()

    event_stdio.serve_session_multi(EventSession(channel, agent), out_stream=_Collector(), in_reader=reader)

    assert agent.wake_listener is None
