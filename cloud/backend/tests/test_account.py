# -*- coding: utf-8 -*-
"""Deleting one's own account: asked for, cancelled by signing in again, erased after the grace period."""
from __future__ import annotations

from datetime import timedelta

from conftest import dev_login, settle
from sqlalchemy import func, select

from kvist_cloud import account
from kvist_cloud.db import session_scope, utcnow
from kvist_cloud.models import (
    LedgerEntry,
    Machine,
    MachineStatus,
    Notification,
    Project,
    Subscription,
    Task,
    TaskEvent,
    User,
)


def _with_work(client) -> None:
    """A trial, a machine, a project and a session with an event, as a real account has."""
    assert client.post("/api/billing/dev/skip-checkout").status_code == 200
    settle(client)
    with session_scope(client.app.state.session_factory) as db:
        machine = db.scalar(select(Machine))
        machine.status = MachineStatus.AWAKE
        db.add(Project(user_id=1, name="site", folder="site", folder_key="site"))
        task = Task(user_id=1, machine_id=machine.id, goal="secret plans", final_message="done: secret")
        db.add(task)
        db.flush()
        db.add(TaskEvent(task_id=task.id, seq=1, type="text", payload={"text": "secret"}))


def _ask(client):
    return client.post("/api/me/delete", json={"confirm": True})


def test_asking_signs_out_puts_the_machine_to_sleep_and_stops_renewal(client_factory, fake_provider):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    _with_work(client)
    response = _ask(client)
    assert response.status_code == 200
    assert response.json()["erase_after"] > utcnow().isoformat()
    settle(client)
    assert client.get("/api/me").status_code == 401
    with session_scope(client.app.state.session_factory) as db:
        user = db.get(User, 1)
        assert user.delete_requested_at is not None and user.deleted_at is None
        assert db.scalar(select(Subscription)).cancel_at_period_end
        assert db.scalar(select(Machine)).status == MachineStatus.ASLEEP
        assert db.scalar(select(Task)).goal == "secret plans"
    assert ("sleep", "m1") in fake_provider.calls


def test_the_body_must_say_yes(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    assert client.post("/api/me/delete", json={"confirm": False}).status_code == 422
    assert client.post("/api/me/delete", json={}).status_code == 422
    assert client.get("/api/me").status_code == 200


def test_signing_in_again_within_the_grace_period_cancels_it(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    _with_work(client)
    _ask(client)
    settle(client)
    dev_login(client)
    assert client.get("/api/me").json()["login"] == "dev"
    kinds = [item["kind"] for item in client.get("/api/notifications").json()["items"]]
    assert "deletion_cancelled" in kinds
    with session_scope(client.app.state.session_factory) as db:
        assert db.get(User, 1).delete_requested_at is None
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(Task)).goal == "secret plans"


def _past_grace(client) -> None:
    with session_scope(client.app.state.session_factory) as db:
        db.get(User, 1).delete_requested_at = utcnow() - account.GRACE - timedelta(minutes=1)


def test_after_the_grace_period_the_account_is_erased_but_payments_stay(client_factory, fake_provider):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    _with_work(client)
    _ask(client)
    settle(client)
    _past_grace(client)
    client.portal.call(account.purge_due, client.app)
    assert ("delete", "m1") in fake_provider.calls
    with session_scope(client.app.state.session_factory) as db:
        user = db.get(User, 1)
        assert user.deleted_at is not None and user.github_id is None
        assert (user.github_login, user.name, user.github_token_enc) == ("deleted", "", "")
        assert user.trial_github_id == 1  # the dev profile's GitHub id; the trial was used
        task = db.scalar(select(Task))
        assert (task.goal, task.final_message) == ("", "")
        assert db.scalar(select(func.count()).select_from(TaskEvent)) == 0
        assert db.scalar(select(func.count()).select_from(Project)) == 0
        assert db.scalar(select(func.count()).select_from(Notification)) == 0
        assert db.scalar(select(Machine)).status == MachineStatus.DELETED
        assert db.scalar(select(func.count()).select_from(LedgerEntry)) > 0


def test_signing_up_again_after_erasure_is_a_new_account_without_a_second_trial(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    _with_work(client)
    _ask(client)
    settle(client)
    _past_grace(client)
    client.portal.call(account.purge_due, client.app)
    dev_login(client)
    assert client.get("/api/me").json()["id"] == 2
    assert client.get("/api/billing/eligibility").json()["trial_used"] is True
    assert client.post("/api/billing/dev/skip-checkout").status_code == 403


def test_a_machine_that_will_not_go_keeps_the_account_for_the_next_pass(client_factory, fake_provider):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    _with_work(client)
    _ask(client)
    settle(client)
    _past_grace(client)

    async def refuse(ref: str) -> None:
        raise OSError("provider down")

    fake_provider.delete = refuse
    client.portal.call(account.purge_due, client.app)
    with session_scope(client.app.state.session_factory) as db:
        user = db.get(User, 1)
        assert user.deleted_at is None and user.github_login == "dev"
        assert db.scalar(select(Task)).goal == "secret plans"
