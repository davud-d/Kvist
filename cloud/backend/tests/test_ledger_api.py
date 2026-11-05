# -*- coding: utf-8 -*-
"""The dashboard's feed and disputes."""
from __future__ import annotations

from conftest import dev_login, make_task, settle
from sqlalchemy import select

from kvist_cloud import ledger
from kvist_cloud.db import make_session_factory, session_scope
from kvist_cloud.models import Dispute, Machine, TaskStatus, User

T0 = 1_800_000_000.0


def test_feed_groups_charges_by_task_and_disputes_are_recorded(client_factory, engine):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    settle(client)
    client.post("/api/billing/dev/skip-checkout")
    with session_scope(make_session_factory(engine)) as db:
        user = db.scalars(select(User)).one()
        machine = db.scalars(select(Machine)).one()
        task = make_task(db, user, machine, status=TaskStatus.RUNNING, goal="explain the project")
        ledger.record_heartbeat(db, machine, [task.id], T0, 45)
        for step in range(1, 9):
            ledger.record_heartbeat(db, machine, [task.id], T0 + 15 * step, 45)
        task_id = task.id
    body = client.get("/api/ledger").json()
    usage = [row for row in body["feed"] if row["kind"] == "usage"]
    assert usage == [{"kind": "usage", "task_id": task_id, "goal": "explain the project", "seconds": -120,
                      "minutes": 2, "at": usage[0]["at"], "disputed": False}]
    assert body["subscription"]["balance_seconds"] == 5 * 3600 - 120
    assert client.post("/api/disputes", json={"task_id": task_id, "message": "the model hung"}).status_code == 201
    assert client.get("/api/ledger").json()["feed"][0]["disputed"] is True
    with session_scope(make_session_factory(engine)) as db:
        assert db.scalars(select(Dispute)).one().message == "the model hung"


def test_cannot_dispute_someone_elses_task(client_factory, engine):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    settle(client)
    with session_scope(make_session_factory(engine)) as db:
        stranger = User(github_id=77, github_login="stranger")
        db.add(stranger)
        db.flush()
        task_id = make_task(db, stranger, db.scalars(select(Machine)).one()).id
    assert client.post("/api/disputes", json={"task_id": task_id, "message": "mine?"}).status_code == 404
