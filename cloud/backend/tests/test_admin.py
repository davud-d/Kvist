# -*- coding: utf-8 -*-
"""The admin panel's API: people, plans, hours, admin rights, a stopped machine, bans and the bell."""
from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import dev_login, make_machine, make_subscription, make_task, make_user, settle
from sqlalchemy import func, select

from kvist_cloud import auth
from kvist_cloud.db import session_scope, utcnow
from kvist_cloud.models import AdminAction, Machine, MachineStatus, Notification, Task, TaskStatus, User


@pytest.fixture
def admin(client_factory):
    """Signed in as the owner "dev" (user 1)."""
    client = client_factory(AUTH_DEV_LOGIN="true", ADMIN_GITHUB_LOGINS="dev")
    dev_login(client)
    return client


def customer(client, login="alice", plan="pro", machine=True):
    """A second person in the same database, with a plan and a machine of ours; returns (id, machine_id)."""
    with session_scope(client.app.state.session_factory) as db:
        user = make_user(db, login, github_id=1000 + db.scalar(select(func.count()).select_from(User)))
        user.model_provider, user.model_key_enc = "deepseek", "sealed"
        if plan:
            make_subscription(db, user, plan)
        made = make_machine(db, user) if machine else None
        if made is not None:
            made.status = MachineStatus.AWAKE
        return user.id, made.id if made is not None else None


def as_user(client_factory, admin_client, user_id):
    """A client holding that person's session cookie (the dev login cannot sign in anyone but user 1)."""
    other = client_factory(AUTH_DEV_LOGIN="true", ADMIN_GITHUB_LOGINS="dev")
    settings = admin_client.app.state.settings
    with session_scope(admin_client.app.state.session_factory) as db:
        version = db.get(User, user_id).session_version
    value = auth._serializer(settings, "session").dumps({"u": user_id, "v": version})
    other.cookies.set(auth.SESSION_COOKIE, value)
    return other


def notices(client, user_id):
    with session_scope(client.app.state.session_factory) as db:
        return [(row.kind, row.params) for row in db.scalars(
            select(Notification).where(Notification.user_id == user_id).order_by(Notification.id))]


def balance(admin, user_id):
    return admin.get(f"/api/admin/users/{user_id}").json()["subscription"]["balance_minutes"]


def test_only_admins_reach_the_panel(client_factory, admin):
    user_id, _ = customer(admin)
    plain = as_user(client_factory, admin, user_id)
    assert plain.get("/api/admin/users").status_code == 403
    assert plain.post(f"/api/admin/users/{user_id}/hours", json={"minutes": 5, "note": "gift"}).status_code == 403
    assert admin.get("/api/admin/users").status_code == 200


def test_the_list_searches_by_login_name_email_and_id(admin):
    first, _ = customer(admin, "alice")
    customer(admin, "bobby")
    found = admin.get("/api/admin/users", params={"q": "ALIC"}).json()["users"]
    assert [row["login"] for row in found] == ["alice"]
    assert found[0]["plan"] == "pro" and found[0]["balance_minutes"] == 150 * 60
    assert admin.get("/api/admin/users", params={"q": str(first)}).json()["users"][0]["login"] == "alice"
    assert len(admin.get("/api/admin/users").json()["users"]) == 3  # dev, alice, bobby
    assert admin.get("/api/admin/users/9999").status_code == 404


def test_giving_a_plan_starts_it_and_tells_the_person(admin):
    user_id, _ = customer(admin, plan=None, machine=False)
    response = admin.post(f"/api/admin/users/{user_id}/plan", json={"plan": "start", "days": 10, "note": "welcome"})
    assert response.status_code == 200
    detail = admin.get(f"/api/admin/users/{user_id}").json()
    assert detail["plan"] == "start" and detail["balance_minutes"] == 50 * 60
    assert detail["subscription"]["status"] == "active"
    assert notices(admin, user_id)[0][0] == "plan_granted"
    assert notices(admin, user_id)[0][1]["plan"] == "start" and notices(admin, user_id)[0][1]["note"] == "welcome"
    assert detail["actions"][0]["action"] == "plan" and detail["actions"][0]["admin"] == "dev"


def test_a_new_plan_keeps_the_hours_left_on_a_paid_one_but_not_a_trials(admin):
    paid, _ = customer(admin, "payer", plan="start")
    trial, _ = customer(admin, "tryer", plan="trial")
    admin.post(f"/api/admin/users/{paid}/hours", json={"minutes": -(40 * 60), "note": "used offline"})
    admin.post(f"/api/admin/users/{paid}/plan", json={"plan": "pro"})
    admin.post(f"/api/admin/users/{trial}/plan", json={"plan": "pro"})
    assert balance(admin, paid) == 10 * 60 + 150 * 60
    assert balance(admin, trial) == 150 * 60


def test_hours_can_be_given_and_taken_but_not_below_zero(admin):
    user_id, _ = customer(admin)
    assert admin.post(f"/api/admin/users/{user_id}/hours", json={"minutes": 90, "note": "sorry"}).status_code == 200
    assert balance(admin, user_id) == 150 * 60 + 90
    assert admin.post(f"/api/admin/users/{user_id}/hours", json={"minutes": -30, "note": "mistake"}).status_code == 200
    assert balance(admin, user_id) == 150 * 60 + 60
    refused = admin.post(f"/api/admin/users/{user_id}/hours", json={"minutes": -999_999 // 10, "note": "too much"})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "hours_refused"
    zero = admin.post(f"/api/admin/users/{user_id}/hours", json={"minutes": 0, "note": "nothing"})
    assert zero.status_code in (400, 422)
    assert [kind for kind, _ in notices(admin, user_id)] == ["hours_granted", "hours_removed"]
    # The person sees it in their own feed, as a line with the admin's note.
    feed = [row for row in admin.get(f"/api/admin/users/{user_id}").json()["ledger"] if row["kind"] == "adjustment"]
    shown = [(row["minutes"], row["negative"], row["reason"]) for row in feed]
    assert shown == [(30, True, "mistake"), (90, False, "sorry")]


def test_hours_mean_nothing_on_a_plan_that_runs_on_the_customers_machine(admin):
    user_id, _ = customer(admin, plan="remote", machine=False)
    response = admin.post(f"/api/admin/users/{user_id}/hours", json={"minutes": 60, "note": "gift"})
    assert response.status_code == 409


def test_the_bell_lists_and_clears(client_factory, admin):
    user_id, _ = customer(admin)
    admin.post(f"/api/admin/users/{user_id}/hours", json={"minutes": 60, "note": "gift"})
    person = as_user(client_factory, admin, user_id)
    bell = person.get("/api/notifications").json()
    assert bell["unread"] == 1 and bell["items"][0]["kind"] == "hours_granted" and not bell["items"][0]["read"]
    assert person.post("/api/notifications/read").json() == {"unread": 0}
    assert person.get("/api/notifications").json()["items"][0]["read"] is True
    assert admin.get("/api/notifications").json()["items"] == []  # nobody else's lines


def test_only_an_owner_makes_admins_and_an_owner_cannot_be_demoted(client_factory, admin):
    user_id, _ = customer(admin, "carol")
    other_id, _ = customer(admin, "dave")
    assert admin.post(f"/api/admin/users/{user_id}/admin", json={"value": True}).status_code == 200
    assert notices(admin, user_id)[-1][0] == "admin_granted"
    helper = as_user(client_factory, admin, user_id)
    assert helper.get("/api/admin/users").status_code == 200
    assert helper.get("/api/me").json()["is_admin"] is True and helper.get("/api/me").json()["is_owner"] is False
    appoint = helper.post(f"/api/admin/users/{other_id}/admin", json={"value": True})
    assert appoint.json()["error"]["code"] == "owners_only"
    assert admin.post("/api/admin/users/1/admin", json={"value": False}).json()["error"]["code"] == "owner"
    assert admin.post(f"/api/admin/users/{user_id}/admin", json={"value": False}).status_code == 200
    assert helper.get("/api/admin/users").status_code == 403


def test_a_temporary_ban_blocks_the_account_stops_its_work_and_ends_by_itself(client_factory, admin, fake_provider):
    user_id, machine_id = customer(admin)
    with session_scope(admin.app.state.session_factory) as db:
        task_id = make_task(db, db.get(User, user_id), db.get(Machine, machine_id)).id
    person = as_user(client_factory, admin, user_id)
    assert person.get("/api/tasks").status_code == 200

    response = admin.post(f"/api/admin/users/{user_id}/ban", json={"days": 2, "reason": "abuse of the trial"})
    assert response.status_code == 200
    blocked = person.get("/api/tasks")
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "banned"
    me = person.get("/api/me").json()
    assert me["ban"]["permanent"] is False and me["ban"]["reason"] == "abuse of the trial" and me["ban"]["until"]
    assert person.post("/api/tasks", json={"goal": "x"}).status_code == 403
    assert admin.get("/api/admin/users", params={"q": "alice"}).json()["users"][0]["ban"]["permanent"] is False
    settle(admin)
    assert ("sleep", "") in fake_provider.calls
    with session_scope(admin.app.state.session_factory) as db:
        task = db.get(Task, task_id)
        assert (task.status, task.status_reason) == (TaskStatus.PAUSED, "machine_suspended")

    with session_scope(admin.app.state.session_factory) as db:
        db.get(User, user_id).ban_until = utcnow() - timedelta(minutes=1)
    assert person.get("/api/tasks").status_code == 200
    assert person.get("/api/me").json()["ban"] is None


def test_a_permanent_ban_stays_until_an_admin_lifts_it(client_factory, admin):
    user_id, _ = customer(admin)
    person = as_user(client_factory, admin, user_id)
    admin.post(f"/api/admin/users/{user_id}/ban", json={"permanent": True, "reason": "fraud"})
    assert person.get("/api/me").json()["ban"] == {"permanent": True, "until": None, "reason": "fraud"}
    assert person.get("/api/billing/subscription").status_code == 403
    assert admin.post(f"/api/admin/users/{user_id}/unban").status_code == 200
    assert person.get("/api/tasks").status_code == 200
    assert notices(admin, user_id)[-1][0] == "unbanned"
    assert admin.post(f"/api/admin/users/{user_id}/unban").json()["error"]["code"] == "not_banned"


def test_a_ban_needs_exactly_one_length_and_spares_owners_and_oneself(client_factory, admin):
    user_id, _ = customer(admin)
    reason = {"reason": "because"}
    assert admin.post(f"/api/admin/users/{user_id}/ban", json=reason).status_code == 422
    both = admin.post(f"/api/admin/users/{user_id}/ban", json={**reason, "permanent": True, "days": 3})
    assert both.status_code == 422
    assert admin.post("/api/admin/users/1/ban", json={**reason, "days": 1}).json()["error"]["code"] == "self_ban"
    admin.post(f"/api/admin/users/{user_id}/admin", json={"value": True})
    helper = as_user(client_factory, admin, user_id)
    assert helper.post("/api/admin/users/1/ban", json={**reason, "days": 1}).json()["error"]["code"] == "owner"
    third, _ = customer(admin, "erin")
    admin.post(f"/api/admin/users/{third}/admin", json={"value": True})
    ban_admin = helper.post(f"/api/admin/users/{third}/ban", json={**reason, "days": 1})
    assert ban_admin.json()["error"]["code"] == "owners_only"


def test_a_stopped_machine_sleeps_wakes_for_nobody_and_cannot_be_reset(client_factory, admin, fake_provider):
    user_id, machine_id = customer(admin)
    with session_scope(admin.app.state.session_factory) as db:
        task_id = make_task(db, db.get(User, user_id), db.get(Machine, machine_id)).id
    person = as_user(client_factory, admin, user_id)
    assert admin.post(f"/api/admin/users/{user_id}/machine/suspend").status_code == 200
    settle(admin)
    assert ("sleep", "") in fake_provider.calls
    assert notices(admin, user_id)[-1][0] == "machine_suspended"
    shown = person.get("/api/machines").json()[0]
    assert shown["suspended"] is True and shown["display_status"] == "suspended"
    with session_scope(admin.app.state.session_factory) as db:
        assert db.get(Task, task_id).status == TaskStatus.PAUSED
    assert person.post(f"/api/machines/{machine_id}/wake").json()["error"]["code"] == "machine_suspended"
    assert person.post(f"/api/machines/{machine_id}/reset").json()["error"]["code"] == "machine_suspended"
    assert person.post(f"/api/tasks/{task_id}/resume").json()["error"]["code"] == "machine_suspended"
    assert person.post("/api/tasks", json={"goal": "x"}).json()["error"]["code"] == "machine_suspended"
    assert person.get("/api/me").json()["ban"] is None  # stopped is not banned: the person can still sign in

    assert admin.post(f"/api/admin/users/{user_id}/machine/resume").status_code == 200
    assert person.get("/api/machines").json()[0]["suspended"] is False
    assert notices(admin, user_id)[-1][0] == "machine_resumed"
    assert admin.post(f"/api/admin/users/{user_id}/machine/resume").status_code == 404


def test_a_customers_own_machine_cannot_be_stopped(admin):
    user_id, _ = customer(admin, plan="remote", machine=False)
    response = admin.post(f"/api/admin/users/{user_id}/machine/suspend")
    assert response.status_code == 409 and response.json()["error"]["code"] == "own_machine"


def test_every_change_is_written_to_the_audit_trail(admin):
    user_id, _ = customer(admin)
    admin.post(f"/api/admin/users/{user_id}/hours", json={"minutes": 5, "note": "gift"})
    admin.post(f"/api/admin/users/{user_id}/ban", json={"days": 1, "reason": "spam"})
    admin.post(f"/api/admin/users/{user_id}/unban")
    with session_scope(admin.app.state.session_factory) as db:
        rows = list(db.scalars(select(AdminAction).where(AdminAction.user_id == user_id).order_by(AdminAction.id)))
    assert [row.action for row in rows] == ["hours", "ban", "unban"]
    assert rows[1].detail["reason"] == "spam"
