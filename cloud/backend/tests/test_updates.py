# -*- coding: utf-8 -*-
"""In-place updates of a customer's machine: when it is told, what the swap does to a running task, and billing."""
from __future__ import annotations

import hashlib
from datetime import timedelta

from conftest import dev_login, eventually
from sqlalchemy import func, select
from test_gateway import TOKEN, connect, read, seed

from kvist_cloud import updates
from kvist_cloud.db import make_session_factory, session_scope, utcnow
from kvist_cloud.models import LedgerEntry, Machine, MachineStatus, Subscription, Task, TaskStatus

KVIST_WHEEL = "kvist-0.1.0b1-py3-none-any.whl"
RUNNER_WHEEL = "kvist_runner-0.1.0-py3-none-any.whl"


def wheels(tmp_path, kvist: bytes = b"kvist wheel one", runner: bytes = b"runner wheel one"):
    directory = tmp_path / "wheels"
    directory.mkdir(exist_ok=True)
    (directory / KVIST_WHEEL).write_bytes(kvist)
    (directory / RUNNER_WHEEL).write_bytes(runner)
    (directory / "httpx-0.28.1-py3-none-any.whl").write_bytes(b"a dependency: not part of a release")
    return directory


def make_idle(engine, minutes: int = 45, release: str = "") -> None:
    with session_scope(make_session_factory(engine)) as db:
        machine = db.get(Machine, 1)
        machine.status = MachineStatus.AWAKE
        machine.last_active_at = utcnow() - timedelta(minutes=minutes)
        machine.runner_release = release


def sweep(client) -> None:
    client.portal.call(updates.sweep_once, client.app)


def asked(client) -> None:
    """The server has told machine 1 to stage: what a runner's `update_ready` answers."""
    client.app.state.updates.staging[1] = utcnow()


def after_hello(ws, engine) -> None:
    """Messages are handled in order: once a heartbeat that followed the hello is recorded, the hello is done."""
    ws.send_json({"type": "heartbeat", "running": [], "background": []})
    eventually(lambda: read(engine, Machine, 1).last_beat_second is not None)


def switching(client, seconds_ago: float = 0.0) -> None:
    client.app.state.updates.applying[1] = utcnow() - timedelta(seconds=seconds_ago)


def next_of(ws, kind: str) -> dict:
    while True:
        message = ws.receive_json()
        if message["type"] == kind:
            return message


def test_a_release_is_the_two_wheels_and_changes_with_them(client_factory, tmp_path):
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    first = updates.current_release(client.app)
    assert [item["name"] for item in first.files] == [KVIST_WHEEL, RUNNER_WHEEL]
    assert first.files[0]["sha256"] == hashlib.sha256(b"kvist wheel one").hexdigest()
    wheels(tmp_path, kvist=b"kvist wheel two")
    assert updates.current_release(client.app).id != first.id


def test_no_wheels_means_no_updates(client_factory, tmp_path, engine):
    seed(engine)
    client = client_factory(WHEELS_DIR=str(tmp_path / "nothing"))
    assert updates.current_release(client.app) is None
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        make_idle(engine)
        sweep(client)


def test_an_idle_machine_is_told_to_stage_the_release(client_factory, tmp_path, engine):
    seed(engine, status=TaskStatus.DONE)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [], "release": ""})
        eventually(lambda: client.app.state.gateway.connected(1))
        make_idle(engine)
        sweep(client)
        message = next_of(ws, "update")
        assert message["release"] == updates.current_release(client.app).id
        assert [item["name"] for item in message["files"]] == [KVIST_WHEEL, RUNNER_WHEEL]


def test_a_machine_left_alone_for_less_than_half_an_hour_is_not_touched(client_factory, tmp_path, engine):
    seed(engine, status=TaskStatus.DONE)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [], "release": ""})
        eventually(lambda: client.app.state.gateway.connected(1))
        make_idle(engine, minutes=29)
        sweep(client)
        assert not client.app.state.updates.staging


def test_a_machine_with_a_task_is_not_touched(client_factory, tmp_path, engine):
    seed(engine, status=TaskStatus.RUNNING)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [{"task_id": 1, "running": True}], "release": ""})
        eventually(lambda: client.app.state.gateway.connected(1))
        make_idle(engine)
        sweep(client)
        assert not client.app.state.updates.staging


def test_a_machine_already_on_the_release_is_left_alone(client_factory, tmp_path, engine):
    seed(engine, status=TaskStatus.DONE)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    release = updates.current_release(client.app).id
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [], "release": release})
        eventually(lambda: read(engine, Machine, 1).runner_release == release)
        make_idle(engine, release=release)
        sweep(client)
        assert not client.app.state.updates.staging


def test_a_sleeping_machine_is_woken_for_it_and_told_when_it_is_back(client_factory, tmp_path, engine, fake_provider):
    seed(engine, status=TaskStatus.DONE)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    with session_scope(make_session_factory(engine)) as db:
        machine = db.get(Machine, 1)
        machine.status = MachineStatus.ASLEEP
        machine.last_active_at = utcnow() - timedelta(hours=2)
    sweep(client)
    assert 1 in client.app.state.updates.wanted
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [], "release": ""})
        assert next_of(ws, "update")["release"] == updates.current_release(client.app).id
    assert 1 not in client.app.state.updates.wanted


def test_a_staged_release_is_applied_and_nothing_is_dispatched_until_hello(client_factory, tmp_path, engine):
    seed(engine, status=TaskStatus.DONE)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    release = updates.current_release(client.app).id
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [], "release": ""})
        asked(client)
        ws.send_json({"type": "update_ready", "release": release})
        assert next_of(ws, "apply_release")["release"] == release
        assert updates.is_applying(client.app, 1)
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [], "release": release})
        eventually(lambda: not updates.is_applying(client.app, 1))
    assert read(engine, Machine, 1).runner_release == release


def test_a_ready_for_an_older_release_is_ignored(client_factory, tmp_path, engine):
    seed(engine, status=TaskStatus.DONE)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [], "release": ""})
        asked(client)
        ws.send_json({"type": "update_ready", "release": "0" * 16})
        ws.send_json({"type": "heartbeat", "running": [], "background": []})
        eventually(lambda: client.app.state.gateway.connected(1))
        assert not updates.is_applying(client.app, 1)


def test_a_failed_release_is_not_tried_again_for_an_hour(client_factory, tmp_path, engine):
    seed(engine, status=TaskStatus.DONE)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    release = updates.current_release(client.app).id
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [], "release": ""})
        asked(client)
        ws.send_json({"type": "update_failed", "release": release, "reason": "does not start"})
        eventually(lambda: 1 in client.app.state.updates.failed)
        make_idle(engine)
        sweep(client)
        assert not client.app.state.updates.staging


def test_a_task_stopped_for_the_switch_continues_in_the_same_session_and_costs_nothing(
    client_factory, tmp_path, engine
):
    task_id = seed(engine, status=TaskStatus.RUNNING)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    release = updates.current_release(client.app).id
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [{"task_id": task_id, "running": True}], "release": ""})
        asked(client)
        ws.send_json({"type": "update_ready", "release": release})
        next_of(ws, "apply_release")
        ws.send_json({"type": "task_exit", "task_id": task_id, "exit_code": -9, "reason": "core_updated"})
        eventually(lambda: read(engine, Task, task_id).status == TaskStatus.STARTING)
        task = read(engine, Task, task_id)
        assert task.resume_requested and task.status_reason == ""
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [], "release": release})
        start = next_of(ws, "start_task")
        assert start["task_id"] == task_id and start["resume"] is True
    with session_scope(make_session_factory(engine)) as db:
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.task_id == task_id)) == 0


def test_the_wheels_are_served_only_to_a_machine_and_only_the_current_release(client_factory, tmp_path, engine):
    seed(engine, status=TaskStatus.DONE)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    release = updates.current_release(client.app).id
    url = f"/gateway/release/{release}/{KVIST_WHEEL}"
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get(url, headers=auth).content == b"kvist wheel one"
    assert client.get(url).status_code == 404
    assert client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 404
    assert client.get(f"/gateway/release/{'f' * 16}/{KVIST_WHEEL}", headers=auth).status_code == 404
    # A dependency is in the directory but not in the release; a made-up path is not a name at all.
    assert client.get(f"/gateway/release/{release}/httpx-0.28.1-py3-none-any.whl", headers=auth).status_code == 404
    assert client.get(f"/gateway/release/{release}/..%2Fsecret.whl", headers=auth).status_code == 404


def exit_for_update(ws, task_id: int) -> None:
    ws.send_json({"type": "task_exit", "task_id": task_id, "exit_code": -9, "reason": "core_updated"})


def test_a_ready_nobody_asked_for_starts_no_switch(client_factory, tmp_path, engine):
    seed(engine, status=TaskStatus.DONE)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    release = updates.current_release(client.app).id
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [], "release": ""})
        ws.send_json({"type": "update_ready", "release": release})
        ws.send_json({"type": "heartbeat", "running": [], "background": []})
        eventually(lambda: read(engine, Machine, 1).last_beat_second is not None)
    assert not updates.is_applying(client.app, 1)


def test_claiming_an_update_does_not_take_a_task_out_of_billing(client_factory, tmp_path, engine):
    """A runner that says `core_updated` with no switch under way gets a pause, not a quiet restart that is unbilled."""
    task_id = seed(engine, status=TaskStatus.RUNNING)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [{"task_id": task_id, "running": True}], "release": ""})
        exit_for_update(ws, task_id)
        eventually(lambda: read(engine, Task, task_id).status == TaskStatus.PAUSED)
    task = read(engine, Task, task_id)
    assert task.status_reason == "core_updated" and not task.resume_requested


def test_the_allowance_for_a_switch_runs_out(client_factory, tmp_path, engine):
    task_id = seed(engine, status=TaskStatus.RUNNING)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    switching(client, seconds_ago=updates.APPLY_ALLOWANCE.total_seconds() + 1)
    assert not updates.is_applying(client.app, 1)
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [{"task_id": task_id, "running": True}], "release": ""})
        switching(client, seconds_ago=updates.APPLY_ALLOWANCE.total_seconds() + 1)
        exit_for_update(ws, task_id)
        eventually(lambda: read(engine, Task, task_id).status == TaskStatus.PAUSED)


def test_a_runner_that_never_comes_back_does_not_leave_the_task_waiting_unbilled(client_factory, tmp_path, engine):
    task_id = seed(engine, status=TaskStatus.RUNNING)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [{"task_id": task_id, "running": True}], "release": ""})
        eventually(lambda: client.app.state.gateway.connected(1))
        after_hello(ws, engine)
        switching(client)
        exit_for_update(ws, task_id)
        eventually(lambda: read(engine, Task, task_id).status == TaskStatus.STARTING)
        # No hello comes; when the allowance is over the sweep sends the start instead of leaving it waiting.
        switching(client, seconds_ago=updates.APPLY_ALLOWANCE.total_seconds() + 1)
        sweep(client)
        assert next_of(ws, "start_task")["task_id"] == task_id
    assert 1 not in client.app.state.updates.applying


def test_a_switch_does_not_give_hours_back(client_factory, tmp_path, engine):
    """Out of hours, a task stopped for a switch is paused for hours, never resumed: nothing is dispatched."""
    task_id = seed(engine, status=TaskStatus.RUNNING)
    with session_scope(make_session_factory(engine)) as db:
        for subscription in db.scalars(select(Subscription)):
            db.delete(subscription)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    with connect(client) as ws:
        ws.send_json({"type": "hello", "tasks": [{"task_id": task_id, "running": True}], "release": ""})
        eventually(lambda: client.app.state.gateway.connected(1))
        after_hello(ws, engine)
        switching(client)
        exit_for_update(ws, task_id)
        eventually(lambda: read(engine, Task, task_id).status == TaskStatus.PAUSED)
    assert read(engine, Task, task_id).status_reason == "hours_exhausted"


def test_a_deploy_does_not_wake_every_sleeping_machine_at_once(client_factory, tmp_path, engine):
    seed(engine, status=TaskStatus.DONE)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)))
    with session_scope(make_session_factory(engine)) as db:
        first = db.get(Machine, 1)
        for number in range(4):
            other = Machine(user_id=first.user_id, provider="local", token_hash=f"{number:064d}", vcpu=2, memory_gb=4,
                            disk_gb=10)
            db.add(other)
        db.flush()
        for machine in db.scalars(select(Machine)):
            machine.status = MachineStatus.ASLEEP
            machine.last_active_at = utcnow() - timedelta(hours=3)
    sweep(client)
    assert len(client.app.state.updates.wanted) == updates.MAX_WAKES_PER_SWEEP


def test_the_page_can_read_that_an_update_is_going_on_but_no_route_can_say_so(client_factory, tmp_path, engine):
    seed(engine, status=TaskStatus.DONE)
    client = client_factory(WHEELS_DIR=str(wheels(tmp_path)), AUTH_DEV_LOGIN="true")
    dev_login(client)
    with session_scope(make_session_factory(engine)) as db:
        db.get(Machine, 1).user_id = client.get("/api/me").json()["id"]
    assert client.get("/api/machines").json()[0]["updating"] is False
    asked(client)
    assert client.get("/api/machines").json()[0]["updating"] is True
    # The state is the server's alone: nothing under /api takes it from a browser.
    assert not [path for path in client.app.openapi()["paths"] if "updat" in path and path.startswith("/api")]
