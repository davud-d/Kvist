# -*- coding: utf-8 -*-
"""The runner gateway: auth, start messages, heartbeats, events, and billing stopping with them."""
from __future__ import annotations

import hashlib
import os
import signal
import time

import pytest
from conftest import (
    MASTER_KEY,
    dev_login,
    eventually,
    make_machine,
    make_subscription,
    make_task,
    make_user,
)
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from kvist_cloud import ledger
from kvist_cloud.db import make_session_factory, session_scope
from kvist_cloud.gateway import dispatch
from kvist_cloud.models import Machine, Task, TaskEvent, TaskStatus, User
from kvist_cloud.secrets import Vault

KEY = "sk-gateway-canary-0123456789abcdef"
TOKEN = "machine-token-for-tests-0123456789"
T0 = 1_800_000_000.0


def seed(engine, status: str = TaskStatus.STARTING, goal: str = "quick") -> int:
    with session_scope(make_session_factory(engine)) as db:
        user = make_user(db)
        user.model_provider = "deepseek"
        user.model_key_enc = Vault(MASTER_KEY).encrypt(KEY)
        make_subscription(db, user, "trial")
        machine = make_machine(db, user)
        machine.token_hash = hashlib.sha256(TOKEN.encode()).hexdigest()
        return make_task(db, user, machine, status=status, goal=goal).id


def read(engine, model, ident):
    with session_scope(make_session_factory(engine)) as db:
        row = db.get(model, ident)
        db.expunge(row)
        return row


def used(engine, task_id: int) -> int:
    with session_scope(make_session_factory(engine)) as db:
        return ledger.task_used_seconds(db, task_id)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


def connect(client, token: str = TOKEN):
    return client.websocket_connect("/gateway/ws", headers={"Authorization": f"Bearer {token}"})


def beat(ws, engine, clock: Clock, at: float, running: list[int]) -> None:
    clock.now = at
    ws.send_json({"type": "heartbeat", "running": running, "background": []})
    eventually(lambda: read(engine, Machine, 1).last_beat_second == int(at))


def test_unknown_machine_is_refused(client_factory, engine):
    seed(engine)
    client = client_factory()
    with pytest.raises(WebSocketDisconnect) as refused, connect(client, "wrong-token") as ws:
        ws.receive_json()
    assert refused.value.code == 4401


def test_task_runs_and_heartbeats_bill_it(client_factory, engine):
    task_id = seed(engine)
    client = client_factory()
    clock = Clock()
    client.app.state.clock = clock
    with connect(client) as ws:
        ws.send_json({"type": "hello", "runner_version": "test", "tasks": []})
        start = ws.receive_json()
        assert start["type"] == "start_task" and start["task_id"] == task_id
        assert start["env"] == {"AGENT_LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": KEY}
        assert start["limits"]["background_seconds"] == 0 and start["limits"]["command_seconds"] == 1800
        assert read(engine, Machine, 1).connected is True

        ws.send_json({"type": "event", "task_id": task_id, "event": {"type": "hello", "protocol_version": 1}})
        eventually(lambda: read(engine, Task, task_id).status == TaskStatus.RUNNING)
        beat(ws, engine, clock, T0, [task_id])
        beat(ws, engine, clock, T0 + 15, [task_id])
        assert used(engine, task_id) == 15

        ws.send_json({"type": "event", "task_id": task_id,
                      "event": {"type": "task_state", "state": "awaiting_input", "reason": ""}})
        eventually(lambda: read(engine, Task, task_id).status == TaskStatus.WAITING_FOR_USER)
        beat(ws, engine, clock, T0 + 20, [])
        beat(ws, engine, clock, T0 + 60, [])
        assert used(engine, task_id) == 20

        ws.send_json({"type": "event", "task_id": task_id,
                      "event": {"type": "final", "message": "ok", "user_messages": [], "outcome": "done"}})
        eventually(lambda: read(engine, Task, task_id).status == TaskStatus.DONE)
    with session_scope(make_session_factory(engine)) as db:
        kinds = [e.type for e in db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id))]
    assert kinds == ["hello", "task_state", "final"]
    machine = read(engine, Machine, 1)
    assert (machine.connected, machine.last_beat_second) == (False, None)


def test_silence_stops_the_meter_even_on_an_open_connection(client_factory, engine):
    task_id = seed(engine, status=TaskStatus.RUNNING)
    client = client_factory()
    clock = Clock()
    client.app.state.clock = clock
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [{"task_id": task_id, "running": True}]})
        beat(ws, engine, clock, T0, [task_id])
        beat(ws, engine, clock, T0 + 15, [task_id])
        # Two minutes without a heartbeat, then one arrives: the gap is not billed.
        beat(ws, engine, clock, T0 + 135, [task_id])
        assert used(engine, task_id) == 15
        beat(ws, engine, clock, T0 + 150, [task_id])
        assert used(engine, task_id) == 30


def test_reconnect_does_not_bill_the_outage(client_factory, engine):
    task_id = seed(engine, status=TaskStatus.RUNNING)
    client = client_factory()
    clock = Clock()
    client.app.state.clock = clock
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [{"task_id": task_id, "running": True}]})
        beat(ws, engine, clock, T0, [task_id])
    eventually(lambda: read(engine, Machine, 1).connected is False)
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [{"task_id": task_id, "running": True}]})
        beat(ws, engine, clock, T0 + 10, [task_id])
        beat(ws, engine, clock, T0 + 25, [task_id])
    assert used(engine, task_id) == 15


def test_runner_restart_pauses_the_tasks_it_lost(client_factory, engine):
    task_id = seed(engine, status=TaskStatus.RUNNING)
    client = client_factory()
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        eventually(lambda: read(engine, Task, task_id).status == TaskStatus.PAUSED)
    assert read(engine, Task, task_id).status_reason == "machine_restarted"


def test_server_scrubs_events_the_runner_did_not(client_factory, engine):
    task_id = seed(engine, status=TaskStatus.STARTING)
    client = client_factory()
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        ws.receive_json()  # start_task: the key was decrypted, so the server knows it
        ws.send_json({"type": "event", "task_id": task_id,
                      "event": {"type": "message", "markdown": f"key: {KEY}", "source": "reply"}})
        eventually(lambda: _events(engine, task_id))
    stored = _events(engine, task_id)[0].payload
    assert stored == {"type": "message", "markdown": "key: [redacted]", "source": "reply"}


def _events(engine, task_id):
    with session_scope(make_session_factory(engine)) as db:
        rows = list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id)))
        for row in rows:
            db.expunge(row)
        return rows


def test_event_for_someone_elses_task_is_ignored(client_factory, engine):
    seed(engine)
    with session_scope(make_session_factory(engine)) as db:
        other = make_user(db, "other", github_id=2)
        foreign = make_task(db, other, make_machine(db, other), status=TaskStatus.RUNNING).id
    client = client_factory()
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        ws.receive_json()
        ws.send_json({"type": "event", "task_id": foreign, "event": {"type": "final", "outcome": "done"}})
        ws.send_json({"type": "heartbeat", "running": [], "background": []})
        eventually(lambda: read(engine, Machine, 1).last_beat_second is not None)
    assert read(engine, Task, foreign).status == TaskStatus.RUNNING


def test_turn_end_settles_the_status():
    from kvist_cloud.tasks import apply_event

    replied = Task(status=TaskStatus.RUNNING, status_reason="", outcome="")
    for event in ({"type": "message", "markdown": "It is a FastAPI app.", "source": "reply"}, {"type": "turn_end"}):
        apply_event(replied, event)
    assert (replied.status, replied.final_message) == (TaskStatus.DONE, "It is a FastAPI app.")

    notice_only = Task(status=TaskStatus.RUNNING, status_reason="", outcome="")
    for event in ({"type": "message", "markdown": "cleaned up", "source": "notice"}, {"type": "turn_end"}):
        apply_event(notice_only, event)
    assert (notice_only.status, notice_only.status_reason) == (TaskStatus.FAILED, "agent_error")

    wrong_key = Task(status=TaskStatus.RUNNING, status_reason="invalid_key", outcome="")
    for event in ({"type": "error", "message": "HTTP 401"}, {"type": "turn_end"}):
        apply_event(wrong_key, event)
    assert (wrong_key.status, wrong_key.status_reason) == (TaskStatus.PAUSED, "invalid_key")


def _start_hanging_task(stack, goal: str = "hang") -> tuple[int, int]:
    dev_login(stack.http)
    machine_id = eventually(lambda: _connected_machine(stack))
    with stack.db() as db:
        user = db.scalars(select(User)).one()
        user.model_provider = "deepseek"
        user.model_key_enc = stack.app.state.vault.encrypt(KEY)
        make_subscription(db, user, "trial")
        task_id = make_task(db, user, db.get(Machine, machine_id), status=TaskStatus.STARTING, goal=goal).id
        db.commit()
    stack.call(dispatch(stack.app, machine_id))
    return machine_id, task_id


def _connected_machine(stack) -> int | None:
    with stack.db() as db:
        machine = db.scalars(select(Machine)).first()
        return machine.id if machine is not None and machine.connected else None


def _used(stack, task_id: int) -> int:
    with stack.db() as db:
        return ledger.task_used_seconds(db, task_id)


def _runner_pid(stack, machine_id: int) -> int:
    return stack.app.state.provider._processes[f"m{machine_id}"].pid


def test_lost_heartbeats_stop_billing_with_a_real_runner(live_stack):
    stack = live_stack()
    machine_id, task_id = _start_hanging_task(stack)
    eventually(lambda: _used(stack, task_id) >= 2, timeout=30)
    pid = _runner_pid(stack, machine_id)
    # Frozen runner: the connection stays open, the heartbeats stop.
    os.kill(pid, signal.SIGSTOP)
    frozen_at = _used(stack, task_id)
    time.sleep(3.0)
    assert _used(stack, task_id) == frozen_at
    os.kill(pid, signal.SIGCONT)
    time.sleep(1.5)
    resumed = _used(stack, task_id)
    # Only the time after the heartbeats came back is billed, never the three silent seconds.
    assert frozen_at <= resumed <= frozen_at + 2
    # A runner that dies: billing stops at its last heartbeat.
    os.kill(pid, signal.SIGKILL)
    eventually(lambda: _connected_machine(stack) is None, timeout=20)
    dead_at = _used(stack, task_id)
    time.sleep(2.0)
    assert _used(stack, task_id) == dead_at


def test_real_runner_runs_a_task_to_the_end(live_stack):
    stack = live_stack()
    machine_id, task_id = _start_hanging_task(stack, goal="slow 2")
    eventually(lambda: _status(stack, task_id) == TaskStatus.DONE, timeout=30)
    with stack.db() as db:
        kinds = [e.type for e in db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id))]
    assert kinds[0] == "hello" and "action_result" in kinds and "final" in kinds
    assert 1 <= _used(stack, task_id) <= 4
    workspace = stack.app.state.settings.data_dir / "machines" / f"m{machine_id}" / "work" / f"task-{task_id}"
    assert (workspace / "hello.txt").exists()


def _status(stack, task_id: int) -> str:
    with stack.db() as db:
        return db.get(Task, task_id).status
