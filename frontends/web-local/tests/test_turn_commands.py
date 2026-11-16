from __future__ import annotations

import sys
import time

from kvist_web.events import EventBroker
from kvist_web.processes import SessionProcess
from kvist_web.protocol import ExecutionState

# `/council` and `/best-of` reach the core as a goal line (UI_API.md section 8c). The core answers with the turn's
# events, `turn_end`, then `command_result`; a refused command answers with `command_result` alone.

_CORE = r'''
import json
import sys

def emit(event):
    print(json.dumps(event), flush=True)

emit({"type": "hello", "protocol_version": 1, "session": "one"})
for line in sys.stdin:
    payload = json.loads(line)
    if payload.get("type") != "goal":
        continue
    text = payload["text"].strip()
    if text == "/council":
        emit({"type": "command_result", "name": "council", "ok": False, "message": "Usage: /council <question>", "effects": {}})
        continue
    emit({"type": "council_started", "council_id": "c1", "question": "q", "members": []})
    emit({"type": "bestof_status", "run_id": "r1", "executor_id": "e1", "state": "running"})
    emit({"type": "council_verdict", "council_id": "c1", "chosen": "security", "decision": "d", "reason": "r", "dissent": []})
    emit({"type": "final", "message": "## council\n\nuse security", "user_messages": [], "outcome": "done"})
    emit({"type": "turn_end"})
    emit({"type": "command_result", "name": "council", "ok": True, "message": "council: decision chosen", "effects": {}})
'''


def _process(tmp_path):
    core = tmp_path / "council-core"
    core.write_text(f"#!{sys.executable}\n{_CORE}", encoding="utf-8")
    core.chmod(0o755)
    broker = EventBroker()
    process = SessionProcess(server_id="local", workspace=tmp_path, session="one", broker=broker,
                             command=[sys.executable, str(core)])
    return process, broker


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true")


def _types(broker):
    return [envelope.event["type"] for envelope in broker.after(0, timeout=0)]


def test_a_council_turn_ends_finished_and_its_command_result_changes_nothing(tmp_path):
    process, broker = _process(tmp_path)
    try:
        process.send_goal("/council which one?")
        _wait_for(lambda: "command_result" in _types(broker))
        assert process.snapshot().state == ExecutionState.FINISHED
        assert _types(broker).index("turn_end") < _types(broker).index("command_result")
    finally:
        process.terminate()


def test_a_refused_turn_command_leaves_the_session_idle_not_running(tmp_path):
    process, broker = _process(tmp_path)
    try:
        process.send_goal("/council")
        _wait_for(lambda: "command_result" in _types(broker))
        _wait_for(lambda: process.snapshot().state == ExecutionState.IDLE)
        assert "turn_end" not in _types(broker)
        # The session takes the next goal: nothing is waiting for a turn that never began.
        process.send_goal("/council now with a question")
        _wait_for(lambda: process.snapshot().state == ExecutionState.FINISHED)
    finally:
        process.terminate()


def test_the_live_only_best_of_events_are_not_marked_durable(tmp_path):
    process, broker = _process(tmp_path)
    try:
        process.send_goal("/council which one?")
        _wait_for(lambda: "command_result" in _types(broker))
        durable = {envelope.event["type"]: envelope.durable for envelope in broker.after(0, timeout=0)}
        assert durable["bestof_status"] is False
        assert durable["council_verdict"] is True
        assert durable["final"] is True
        for kind in ("executor_activity", "bestof_diff"):
            process._handle_event({"type": kind, "run_id": "r1"})
        assert [envelope.durable for envelope in broker.after(0, timeout=0)][-2:] == [False, False]
    finally:
        process.terminate()
