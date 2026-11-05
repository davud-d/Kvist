# -*- coding: utf-8 -*-
"""The provider interface, the local provider and the machines API."""
from __future__ import annotations

import json
import stat
import sys
import time
from datetime import timedelta

import pytest
from conftest import dev_login, make_machine, make_task, make_user, settle
from sqlalchemy import select

from kvist_cloud import machines
from kvist_cloud.db import make_session_factory, session_scope, utcnow
from kvist_cloud.models import Machine, MachineStatus, TaskStatus, User
from kvist_cloud.providers import MachineSpec, ProviderError, ProviderState
from kvist_cloud.providers.local import LocalProvider

# A stand-in runner: writes the names of its environment variables, then waits to be stopped.
FAKE_RUNNER = (
    "import json, os, sys, time; "
    "open(os.path.join(os.path.dirname(sys.argv[2]), 'env.json'), 'w').write(json.dumps(sorted(os.environ))); "
    "time.sleep(60)"
)


def spec(machine_id: int = 7, token: str = "runner-token-value-123") -> MachineSpec:
    return MachineSpec(machine_id, 2, 4, 10, "ws://127.0.0.1:8740/gateway/ws", token)


@pytest.fixture
def local(tmp_path):
    return LocalProvider(
        tmp_path / "machines", (sys.executable, "-c", FAKE_RUNNER), ("kvist",), heartbeat_seconds=15.0
    )


async def test_local_provider_life_cycle(local, tmp_path, monkeypatch):
    monkeypatch.setenv("KVIST_CLOUD_MASTER_KEY", "server-only-secret")
    ref = await local.create(spec())
    folder = tmp_path / "machines" / ref
    config = json.loads((folder / "runner.json").read_text())
    assert config["token"] == "runner-token-value-123"
    assert stat.S_IMODE((folder / "runner.json").stat().st_mode) == 0o600
    assert await local.status(ref) == ProviderState.STOPPED

    await local.wake(ref)
    assert await local.status(ref) == ProviderState.RUNNING
    for _ in range(100):
        if (folder / "env.json").exists():
            break
        time.sleep(0.05)
    seen = json.loads((folder / "env.json").read_text())
    assert "KVIST_CLOUD_MASTER_KEY" not in seen
    assert not any(name.startswith("KVIST_CLOUD") for name in seen)

    await local.wake(ref)  # already awake: no second runner
    await local.sleep(ref)
    assert await local.status(ref) == ProviderState.STOPPED
    await local.delete(ref)
    assert await local.status(ref) == ProviderState.MISSING
    assert not folder.exists()


async def test_local_provider_refuses_paths_outside_its_root(local):
    with pytest.raises(ProviderError):
        await local.wake("../elsewhere")


async def test_waking_a_missing_machine_is_an_error(local):
    with pytest.raises(ProviderError):
        await local.wake("m404")


def test_sign_in_creates_and_wakes_the_machine(client_factory, fake_provider):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    settle(client)
    assert fake_provider.calls == [("create", "m1"), ("wake", "m1")]
    listed = client.get("/api/machines").json()
    assert [(m["id"], m["status"], m["vcpu"], m["memory_gb"]) for m in listed] == [(1, "waking", 2, 4)]
    # The runner's token is given to the provider, never shown to the person.
    assert fake_provider.tokens["m1"] not in client.get("/api/machines").text


def test_second_sign_in_reuses_the_machine(client_factory, fake_provider):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    settle(client)
    dev_login(client)
    settle(client)
    assert [call for call in fake_provider.calls if call[0] == "create"] == [("create", "m1")]


def test_busy_machine_cannot_sleep_or_be_deleted(client_factory, engine):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    settle(client)
    with session_scope(make_session_factory(engine)) as db:
        machine = db.scalars(select(Machine)).one()
        make_task(db, db.get(User, machine.user_id), machine)
    assert client.post("/api/machines/1/sleep").status_code == 409
    assert client.delete("/api/machines/1").status_code == 409


def test_idle_machine_goes_to_sleep(client_factory, engine, fake_provider):
    client = client_factory(AUTH_DEV_LOGIN="true", MACHINE_IDLE_SLEEP_SECONDS="60")
    dev_login(client)
    settle(client)
    factory = make_session_factory(engine)
    with session_scope(factory) as db:
        machine = db.scalars(select(Machine)).one()
        machine.status, machine.connected = MachineStatus.AWAKE, True
        machine.last_active_at = utcnow() - timedelta(minutes=5)
    client.portal.call(machines.sweep_once, client.app)
    with session_scope(factory) as db:
        assert db.scalars(select(Machine)).one().status == MachineStatus.ASLEEP
    assert fake_provider.calls[-1] == ("sleep", "m1")


def test_machine_with_a_running_task_stays_awake(app_factory, engine, fake_provider):
    from fastapi.testclient import TestClient

    app = app_factory(MACHINE_IDLE_SLEEP_SECONDS="60")
    factory = make_session_factory(engine)
    with session_scope(factory) as db:
        user = make_user(db)
        machine = make_machine(db, user)
        machine.status, machine.connected = MachineStatus.AWAKE, True
        machine.last_active_at = utcnow() - timedelta(hours=1)
        make_task(db, user, machine, status=TaskStatus.WAITING_FOR_USER)
    with TestClient(app) as client:
        client.portal.call(machines.sweep_once, app)
    with session_scope(factory) as db:
        assert db.scalars(select(Machine)).one().status == MachineStatus.AWAKE
    assert fake_provider.calls == []


def test_machine_that_never_dials_in_is_marked(app_factory, engine):
    from fastapi.testclient import TestClient

    app = app_factory()
    factory = make_session_factory(engine)
    with session_scope(factory) as db:
        machine = make_machine(db, make_user(db))
        machine.status = MachineStatus.WAKING
        machine.last_active_at = utcnow() - timedelta(minutes=10)
    with TestClient(app) as client:
        client.portal.call(machines.sweep_once, app)
    with session_scope(factory) as db:
        machine = db.scalars(select(Machine)).one()
        assert (machine.status, machine.status_detail) == (MachineStatus.ERROR, "wake_timeout")


def test_display_status(db):
    user = make_user(db)
    machine = make_machine(db, user)
    machine.status, machine.connected = MachineStatus.ASLEEP, False
    assert machines.display_status(db, machine) == "asleep"
    task = make_task(db, user, machine, status=TaskStatus.RUNNING)
    assert machines.display_status(db, machine) == "working"
    task.status = TaskStatus.WAITING_FOR_USER
    db.flush()
    assert machines.display_status(db, machine) == "waiting_for_you"
