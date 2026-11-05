# -*- coding: utf-8 -*-
"""Plan limits: the parallel-task queue, the limits sent to the runner, hours running out."""
from __future__ import annotations

import pytest
from conftest import dev_login, eventually, make_machine, make_subscription, make_task, make_user, settle
from sqlalchemy import select
from test_tasks_api import deepseek_ok, relay, runner, status

from kvist_cloud import ledger, limits
from kvist_cloud.db import make_session_factory, session_scope
from kvist_cloud.gateway import start_message
from kvist_cloud.models import Task, TaskStatus, User

KEY = "sk-limits-canary-0123456789abcdef"


def account(client_factory, engine, plan: str = "trial", hours: float | None = None):
    client = client_factory(deepseek_ok, AUTH_DEV_LOGIN="true", HEARTBEAT_MAX_GAP_SECONDS="100000")
    dev_login(client)
    settle(client)
    with session_scope(make_session_factory(engine)) as db:
        make_subscription(db, db.scalars(select(User)).one(), plan, hours=hours)
    assert client.post("/api/model-key", json={"provider": "deepseek", "key": KEY}).json()["ok"]
    return client


def test_trial_runs_one_task_at_a_time(client_factory, engine, fake_provider):
    client = account(client_factory, engine)
    with runner(client, fake_provider) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        first = client.post("/api/tasks", json={"goal": "one"}).json()
        second = client.post("/api/tasks", json={"goal": "two"}).json()
        assert (first["status"], second["status"]) == (TaskStatus.STARTING, TaskStatus.QUEUED)
        assert ws.receive_json()["task_id"] == first["id"]
        relay(ws, first["id"], {"type": "hello", "protocol_version": 1})
        relay(ws, first["id"], {"type": "final", "message": "ok", "user_messages": [], "outcome": "done"})
        # The slot is free as soon as the first task is done: the queue moves without a reload.
        start = ws.receive_json()
        assert (start["type"], start["task_id"]) == ("start_task", second["id"])
        assert status(engine, second["id"]) == TaskStatus.STARTING


def test_pro_runs_three_and_queues_the_fourth(client_factory, engine, fake_provider):
    client = account(client_factory, engine, plan="pro")
    with runner(client, fake_provider) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        created = [client.post("/api/tasks", json={"goal": f"task {n}"}).json() for n in range(4)]
        assert [task["status"] for task in created] == ["starting", "starting", "starting", "queued"]
        assert sorted(ws.receive_json()["task_id"] for _ in range(3)) == [task["id"] for task in created[:3]]
        relay(ws, created[1]["id"], {"type": "hello", "protocol_version": 1})
        relay(ws, created[1]["id"], {"type": "task_state", "state": "awaiting_input", "reason": ""})
        # Waiting for the person still holds the slot.
        eventually(lambda: status(engine, created[1]["id"]) == TaskStatus.WAITING_FOR_USER)
        assert status(engine, created[3]["id"]) == TaskStatus.QUEUED
        client.post(f"/api/tasks/{created[0]['id']}/cancel")
        assert ws.receive_json()["action"] == "cancel"
        relay(ws, created[0]["id"], {"type": "task_state", "state": "cancelled", "reason": ""})
        assert ws.receive_json()["task_id"] == created[3]["id"]


def test_cancelling_a_queued_task_keeps_the_others(client_factory, engine):
    client = account(client_factory, engine)
    first = client.post("/api/tasks", json={"goal": "one"}).json()
    second = client.post("/api/tasks", json={"goal": "two"}).json()
    assert client.post(f"/api/tasks/{second['id']}/cancel").json()["status"] == TaskStatus.CANCELLED
    assert status(engine, first["id"]) == TaskStatus.STARTING


@pytest.mark.parametrize(
    ("plan", "command", "background", "vcpu", "memory"),
    [("trial", 1800, 0, 2, 4), ("start", 1800, 1800, 2, 4), ("pro", 7200, 7200, 4, 8), ("max", 21600, 28800, 4, 8)],
)
def test_limits_sent_to_the_runner_follow_the_plan(app_factory, engine, plan, command, background, vcpu, memory):
    app = app_factory()
    with session_scope(make_session_factory(engine)) as db:
        user = make_user(db)
        user.model_provider = "deepseek"
        user.model_key_enc = app.state.vault.encrypt(KEY)
        make_subscription(db, user, plan)
        task = make_task(db, user, make_machine(db, user), status=TaskStatus.STARTING)
        sent = start_message(app, db, task)["limits"]
    assert (sent["command_seconds"], sent["background_seconds"]) == (command, background)
    assert (sent["vcpu"], sent["memory_gb"], sent["hours_grace_seconds"]) == (vcpu, memory, 300.0)


def test_hours_running_out_pause_the_task_and_nothing_is_lost(client_factory, engine, fake_provider):
    client = account(client_factory, engine, hours=10 / 3600)
    clock = [1_800_000_000.0]
    client.app.state.clock = lambda: clock[0]
    with runner(client, fake_provider) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        task_id = client.post("/api/tasks", json={"goal": "big job"}).json()["id"]
        ws.receive_json()
        relay(ws, task_id, {"type": "hello", "protocol_version": 1})
        eventually(lambda: status(engine, task_id) == TaskStatus.RUNNING)
        ws.send_json({"type": "heartbeat", "running": [task_id], "background": []})
        eventually(lambda: _beat_second(engine) == int(clock[0]))
        clock[0] += 15
        ws.send_json({"type": "heartbeat", "running": [task_id], "background": []})
        assert ws.receive_json() == {"type": "control", "task_id": task_id, "action": "hours_exhausted"}
        # The runner pauses Kvist at its next safe point and reports it.
        ws.send_json({"type": "task_reason", "task_id": task_id, "reason": "hours_exhausted"})
        relay(ws, task_id, {"type": "final", "message": "Paused.", "user_messages": [], "outcome": "incomplete",
                            "paused": True})
        eventually(lambda: status(engine, task_id) == TaskStatus.PAUSED)
    task = client.get(f"/api/tasks/{task_id}").json()
    assert (task["reason"], task["used_seconds"], task["balance_seconds"]) == ("hours_exhausted", 10, 0)
    assert client.get(f"/api/tasks/{task_id}/events").json()["events"], "the history is kept"
    assert client.post(f"/api/tasks/{task_id}/resume").json()["error"]["code"] == "no_hours"
    with session_scope(make_session_factory(engine)) as db:
        ledger.purchase(db, db.scalars(select(User)).one().id, 3600, "10 h pack")
    assert client.post(f"/api/tasks/{task_id}/resume").json()["status"] == TaskStatus.STARTING


def _beat_second(engine):
    from kvist_cloud.models import Machine

    with session_scope(make_session_factory(engine)) as db:
        return db.scalars(select(Machine)).one().last_beat_second


def test_queued_tasks_without_hours_wait_paused(db):
    user = make_user(db)
    make_subscription(db, user, "trial", hours=0)
    machine = make_machine(db, user)
    queued = make_task(db, user, machine, status=TaskStatus.QUEUED)
    assert limits.promote(db, user.id) == []
    assert (queued.status, queued.status_reason) == (TaskStatus.PAUSED, "hours_exhausted")


def test_promote_takes_the_oldest_first(db):
    user = make_user(db)
    make_subscription(db, user, "trial")
    machine = make_machine(db, user)
    older = make_task(db, user, machine, status=TaskStatus.QUEUED)
    newer = make_task(db, user, machine, status=TaskStatus.QUEUED)
    assert limits.promote(db, user.id) == [machine.id]
    assert (older.status, newer.status) == (TaskStatus.STARTING, TaskStatus.QUEUED)
    assert db.scalar(select(Task).where(Task.status == TaskStatus.STARTING)).id == older.id
