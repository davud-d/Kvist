# -*- coding: utf-8 -*-
"""One person never reaches another's machine, and a hand-granted plan gets its machine at once."""
from __future__ import annotations

import hashlib

import pytest
from conftest import dev_login, eventually, make_task, settle
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect
from test_admin import as_user, customer

from kvist_cloud.db import session_scope
from kvist_cloud.models import Machine, MachineStatus, TaskStatus, User


@pytest.fixture
def owner(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true", ADMIN_GITHUB_LOGINS="dev")
    dev_login(client)
    return client


def machines_of_user(client, user_id: int) -> list[Machine]:
    with session_scope(client.app.state.session_factory) as db:
        rows = list(db.scalars(select(Machine).where(Machine.user_id == user_id).order_by(Machine.id)))
        db.expunge_all()
        return rows


def test_nobody_can_drive_or_read_someone_elses_machine(client_factory, owner, fake_provider):
    alice_id, alice_machine = customer(owner, "alice")
    bob_id, bob_machine = customer(owner, "bob")
    with session_scope(owner.app.state.session_factory) as db:
        alice_task = make_task(db, db.get(User, alice_id), db.get(Machine, alice_machine), status=TaskStatus.DONE).id
    bob = as_user(client_factory, owner, bob_id)
    before = list(fake_provider.calls)

    for path in (f"/api/machines/{alice_machine}/wake", f"/api/machines/{alice_machine}/sleep",
                 f"/api/machines/{alice_machine}/reset", f"/api/machines/{alice_machine}/export",
                 f"/api/tasks/{alice_task}/export"):
        answer = bob.post(path)
        assert answer.status_code == 404, path
        assert answer.json()["error"]["code"] == "not_found", path
    # Nothing reached the hypervisor, and Alice's machine is exactly as it was.
    assert fake_provider.calls == before
    assert machines_of_user(owner, alice_id)[0].status == MachineStatus.AWAKE
    # The list only ever shows one's own machine.
    assert [m["id"] for m in bob.get("/api/machines").json()] == [bob_machine]


def test_a_runner_token_binds_only_its_own_machine(client_factory, owner):
    _, alice_machine = customer(owner, "alice")
    _, bob_machine = customer(owner, "bob")
    tokens = {alice_machine: "alice-runner-token-0123456789abcdef", bob_machine: "bob-runner-token-0123456789abcdef"}
    with session_scope(owner.app.state.session_factory) as db:
        for machine_id, token in tokens.items():
            db.get(Machine, machine_id).token_hash = hashlib.sha256(token.encode()).hexdigest()

    def connected(machine_id: int) -> bool:
        with session_scope(owner.app.state.session_factory) as db:
            return db.get(Machine, machine_id).connected

    with owner.websocket_connect("/gateway/ws", headers={"Authorization": f"Bearer {tokens[alice_machine]}"}) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        eventually(lambda: connected(alice_machine))
        assert not connected(bob_machine)

    # A token that is not any machine's (or is a machine's hash, which is all the database holds) opens nothing.
    for wrong in ("x" * 40, hashlib.sha256(tokens[alice_machine].encode()).hexdigest()):
        with pytest.raises(WebSocketDisconnect) as refused, owner.websocket_connect(
                "/gateway/ws", headers={"Authorization": f"Bearer {wrong}"}) as ws:
            ws.receive_json()
        assert refused.value.code == 4401


def test_a_deleted_machines_token_stops_working(owner):
    _, machine_id = customer(owner, "alice")
    token = "alice-runner-token-0123456789abcdef"
    with session_scope(owner.app.state.session_factory) as db:
        machine = db.get(Machine, machine_id)
        machine.token_hash = hashlib.sha256(token.encode()).hexdigest()
        machine.status = MachineStatus.DELETED
    with pytest.raises(WebSocketDisconnect) as refused, owner.websocket_connect(
            "/gateway/ws", headers={"Authorization": f"Bearer {token}"}) as ws:
        ws.receive_json()
    assert refused.value.code == 4401


def test_a_plan_granted_by_hand_creates_the_machine_at_once(owner, fake_provider):
    newbie_id, _ = customer(owner, "newbie", plan=None, machine=False)
    assert machines_of_user(owner, newbie_id) == []

    assert owner.post(f"/api/admin/users/{newbie_id}/plan", json={"plan": "pro"}).status_code == 200
    settle(owner)

    (machine,) = machines_of_user(owner, newbie_id)
    assert machine.user_id == newbie_id and machine.provider == "fake"
    for step in ("create", "wake"):
        assert (step, machine.provider_ref) in fake_provider.calls
    # Granting again, or adding hours, finds the machine instead of making a second one.
    owner.post(f"/api/admin/users/{newbie_id}/hours", json={"minutes": 60, "note": "bonus hour"})
    settle(owner)
    assert len(machines_of_user(owner, newbie_id)) == 1


def test_no_machine_for_a_banned_person(owner):
    banned_id, _ = customer(owner, "banned", plan=None, machine=False)
    owner.post(f"/api/admin/users/{banned_id}/ban", json={"permanent": True, "reason": "fraud"})
    owner.post(f"/api/admin/users/{banned_id}/plan", json={"plan": "pro"})
    settle(owner)
    assert machines_of_user(owner, banned_id) == []
