from __future__ import annotations

import json
import sys
import textwrap
import threading
import time
from dataclasses import dataclass

import pytest

from kernel.session_store import SessionStore

from kvist_web.events import EventBroker
from kvist_web.processes import SessionProcess
from kvist_web.protocol import ExecutionState, ProcessSnapshot
from kvist_web.runtime.local import LocalRuntime


_FAKE_CORE = r'''#!/usr/bin/env python3
import argparse
import json
import os
import pathlib
import sys
import time

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--workspace", required=True)
args, _unknown = parser.parse_known_args()
workspace = pathlib.Path(args.workspace)
active = json.loads((workspace / ".kvist" / "active_session.json").read_text())
session = active["session"]
journal = workspace / ".kvist" / "sessions" / f"{session}.events.jsonl"

def emit(event):
    print(json.dumps(event, separators=(",", ":")), flush=True)

def append_turn(goal):
    seq = 0
    if journal.exists():
        for line in journal.read_text(encoding="utf-8").splitlines():
            try:
                seq = max(seq, int(json.loads(line).get("seq", 0)))
            except Exception:
                pass
    records = [
        {"seq": seq + 1, "turn": seq + 1, "ts": time.time(), "event": {"type": "user", "text": goal}},
        {"seq": seq + 2, "turn": seq + 1, "ts": time.time(), "event": {"type": "final", "message": "done: " + goal, "user_messages": [], "outcome": "done"}},
        {"seq": seq + 3, "turn": seq + 1, "ts": time.time(), "event": {"type": "turn_end"}},
    ]
    with journal.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())

emit({"type": "hello", "protocol_version": 1, "session": session})
for line in sys.stdin:
    payload = json.loads(line)
    if payload.get("type") == "goal":
        goal = payload["text"]
        emit({"type": "status", "message": "working"})
        time.sleep(float(os.environ.get("KVIST_FAKE_DELAY", "0.25")))
        append_turn(goal)
        emit({"type": "final", "message": "done: " + goal, "user_messages": [], "outcome": "done"})
        emit({"type": "turn_end"})
    elif payload.get("type") == "cancel":
        emit({"type": "turn_end", "stopped_by_user": True})
'''


def make_fake_core(path):
    executable = path / "fake-kvist-core"
    executable.write_text(textwrap.dedent(_FAKE_CORE), encoding="utf-8")
    executable.chmod(0o755)
    return executable


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition did not become true")


@dataclass
class _FakeProcess:
    workspace: str
    session: str
    goals: list[str]
    cancelled: bool = False

    def send_goal(self, text):
        self.goals.append(text)

    def cancel(self):
        self.cancelled = True

    def respond(self, response):
        self.response = response

    def snapshot(self):
        return ProcessSnapshot("local", self.workspace, self.session, state="running", pid=42)


def test_agent_start_is_lazy_and_sessions_are_independent(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    store.create("alpha")
    store.create("beta")
    runtime = LocalRuntime("local", [str(tmp_path)], EventBroker())
    processes = {}

    def start(workspace, session):
        return processes.setdefault(session, _FakeProcess(str(workspace), session, []))

    monkeypatch.setattr(runtime.supervisor, "get_or_start", start)
    monkeypatch.setattr(runtime.supervisor, "find", lambda workspace, session: processes.get(session))

    runtime.sessions(str(tmp_path))
    assert processes == {}

    runtime.send(str(tmp_path), "alpha", "first task")
    runtime.send(str(tmp_path), "beta", "second task")
    assert processes["alpha"].goals == ["first task"]
    assert processes["beta"].goals == ["second task"]

    # Reading/switching the visible session is metadata-only and cannot cancel A.
    runtime.history(str(tmp_path), "beta")
    assert processes["alpha"].cancelled is False

    runtime.stop(str(tmp_path), "beta")
    assert processes["beta"].cancelled is True
    assert processes["alpha"].cancelled is False


def test_history_recovers_only_events_after_durable_cursor(tmp_path):
    store = SessionStore(tmp_path)
    store.create("one")
    from kernel.session_events import SessionJournal

    class Event:
        def __init__(self, text):
            self.text = text

        def to_dict(self):
            return {"type": "message", "markdown": self.text}

    path = tmp_path / ".kvist" / "sessions" / "one.events.jsonl"
    journal = SessionJournal(tmp_path, lambda: path, print)
    journal.begin_turn("one")
    journal.emit(Event("first"))
    journal.end_turn()
    first = store.history("one", after=0)["events"][-1]["seq"]
    journal.begin_turn("two")
    journal.emit(Event("second"))
    journal.end_turn()

    recovered = LocalRuntime("local", [str(tmp_path)], EventBroker()).history(
        str(tmp_path), "one", after=first
    )
    assert recovered["events"]
    assert all(event["seq"] > first for event in recovered["events"])


def test_real_supervisor_runs_two_sessions_without_switch_stopping_either(
    tmp_path, monkeypatch
):
    core = make_fake_core(tmp_path)
    monkeypatch.setattr("kvist_web.processes.core_command", lambda: [sys.executable, str(core)])
    monkeypatch.setenv("KVIST_FAKE_DELAY", "0.35")
    store = SessionStore(tmp_path)
    store.create("alpha")
    store.create("beta")
    runtime = LocalRuntime("local", [str(tmp_path)], EventBroker())
    try:
        runtime.send(str(tmp_path), "alpha", "first")
        runtime.send(str(tmp_path), "beta", "second")
        snapshots = {item["session"]: item for item in runtime.processes()}
        assert snapshots["alpha"]["pid"] != snapshots["beta"]["pid"]
        assert snapshots["alpha"]["state"] == "running"
        assert snapshots["beta"]["state"] == "running"

        # Session selection and durable-history reads are metadata operations.
        runtime.history(str(tmp_path), "beta")
        assert runtime.supervisor.find(tmp_path, "alpha").snapshot().state == "running"

        finished = wait_for(
            lambda: all(item["state"] == "finished" for item in runtime.processes())
        )
        assert finished
        assert all(item["pid"] is not None for item in runtime.processes())
        assert store.history("alpha", after=0)["events"][-1]["event"]["type"] == "turn_end"
        assert store.history("beta", after=0)["events"][-1]["event"]["type"] == "turn_end"
    finally:
        runtime.supervisor.close()


def test_concurrent_first_send_starts_one_process_per_session(tmp_path, monkeypatch):
    core = make_fake_core(tmp_path)
    monkeypatch.setattr("kvist_web.processes.core_command", lambda: [sys.executable, str(core)])
    SessionStore(tmp_path).create("alpha")
    runtime = LocalRuntime("local", [str(tmp_path)], EventBroker())
    errors = []

    def send(value):
        try:
            runtime.send(str(tmp_path), "alpha", value)
        except Exception as error:  # pragma: no cover - asserted below
            errors.append(error)

    threads = [threading.Thread(target=send, args=(f"goal-{index}",)) for index in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        assert not errors
        assert len(runtime.processes()) == 1
    finally:
        runtime.supervisor.close()


def _one_shot_core(path, body: str):
    executable = path / "one-shot-kvist-core"
    executable.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    executable.chmod(0o755)
    return executable


def test_clean_exit_before_hello_fails_startup_instead_of_looking_running(tmp_path):
    core = _one_shot_core(tmp_path, "raise SystemExit(0)")
    process = SessionProcess(
        server_id="local",
        workspace=tmp_path,
        session="one",
        broker=EventBroker(),
        command=[sys.executable, str(core)],
    )

    with pytest.raises(RuntimeError, match="before its protocol handshake"):
        process.start()

    snapshot = process.snapshot()
    assert snapshot.state == ExecutionState.FAILED
    assert snapshot.pid is None


def test_clean_exit_after_hello_is_stopped_not_running(tmp_path):
    core = _one_shot_core(
        tmp_path,
        "import json\nprint(json.dumps({'type': 'hello', 'session': 'one'}), flush=True)",
    )
    process = SessionProcess(
        server_id="local",
        workspace=tmp_path,
        session="one",
        broker=EventBroker(),
        command=[sys.executable, str(core)],
    )

    process.start()
    wait_for(lambda: process.snapshot().pid is None)

    assert process.snapshot().state == ExecutionState.STOPPED


def test_raw_core_envelopes_mark_only_journal_events_durable(tmp_path):
    broker = EventBroker()
    process = SessionProcess(
        server_id="local",
        workspace=tmp_path,
        session="one",
        broker=broker,
        command=[sys.executable],
    )

    process._handle_event({"type": "status", "message": "working"})
    process._handle_event({"type": "manifest", "version": 1})
    process._handle_event({"type": "final", "message": "done"})

    events = broker.after(0, timeout=0)
    assert [event.durable for event in events] == [False, True, True]
