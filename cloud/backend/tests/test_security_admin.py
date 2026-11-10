# -*- coding: utf-8 -*-
"""Admin rights, hours and plans under attack: privilege escalation (CWE-269/285/639), races (CWE-362),
mass assignment (CWE-915), integer abuse (CWE-190/20), information exposure (CWE-200), audit gaps (CWE-778).

test_admin.py proves the panel works; this file tries to make it do what it must not.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from conftest import dev_login, make_machine, make_task
from sqlalchemy import func, select
from test_admin import as_user, customer, notices

from kvist_cloud import auth, ledger
from kvist_cloud.db import session_scope, utcnow
from kvist_cloud.models import AdminAction, Dispute, LedgerEntry, Notification, Subscription, SubscriptionStatus, User


@pytest.fixture
def owner(client_factory):
    """Signed in as the owner "dev" (user 1)."""
    client = client_factory(AUTH_DEV_LOGIN="true", ADMIN_GITHUB_LOGINS="dev")
    dev_login(client)
    return client


def make_admin(owner_client, user_id):
    assert owner_client.post(f"/api/admin/users/{user_id}/admin", json={"value": True}).status_code == 200


def count(client, model) -> int:
    with session_scope(client.app.state.session_factory) as db:
        return db.scalar(select(func.count()).select_from(model))


def usable_rows(client, user_id) -> int:
    now = utcnow()
    with session_scope(client.app.state.session_factory) as db:
        return db.scalar(
            select(func.count())
            .select_from(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status.in_(SubscriptionStatus.USABLE),
                Subscription.current_period_end > now,
            )
        )


# -- who can reach what (CWE-285, CWE-862) ---------------------------------------------------------------


def admin_routes(app):
    """Every route under /api/admin, found from the app itself, so a new one is covered the day it is added."""
    found = [
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        if path.startswith("/api/admin")
        for method in item
    ]
    assert len(found) >= 8, "the admin routes were not found"
    return found


def call(client, method, path, target):
    filled = re.sub(r"\{[a-z_]+\}", str(target), path)
    return client.request(method, filled, json={} if method != "GET" else None)


def test_every_admin_route_refuses_the_anonymous_and_the_plain_user(client_factory, owner):
    target, _ = customer(owner, "victim")
    plain_id, _ = customer(owner, "plain")
    plain = as_user(client_factory, owner, plain_id)
    anonymous = client_factory(AUTH_DEV_LOGIN="true", ADMIN_GITHUB_LOGINS="dev")
    before = count(owner, AdminAction)
    for method, path in admin_routes(owner.app):
        assert call(anonymous, method, path, target).status_code == 401, (method, path)
        assert call(plain, method, path, target).status_code == 403, (method, path)
    assert count(owner, AdminAction) == before, "a refused call left a trace of success"


def test_a_banned_admin_loses_the_panel_at_once(client_factory, owner):
    helper_id, _ = customer(owner, "helper", plan=None, machine=False)
    make_admin(owner, helper_id)
    helper = as_user(client_factory, owner, helper_id)
    assert helper.get("/api/admin/users").status_code == 200
    assert (
        owner.post(f"/api/admin/users/{helper_id}/ban", json={"permanent": True, "reason": "abuse"}).status_code == 200
    )
    assert helper.get("/api/admin/users").status_code == 403
    for method, path in admin_routes(owner.app):
        assert call(helper, method, path, 1).status_code == 403, (method, path)


def test_a_revoked_admin_loses_the_panel_at_once(client_factory, owner):
    helper_id, _ = customer(owner, "helper", plan=None, machine=False)
    make_admin(owner, helper_id)
    helper = as_user(client_factory, owner, helper_id)
    assert helper.get("/api/admin/users").status_code == 200
    assert owner.post(f"/api/admin/users/{helper_id}/admin", json={"value": False}).status_code == 200
    assert helper.get("/api/admin/users").status_code == 403
    assert helper.post(f"/api/admin/users/{helper_id}/hours", json={"minutes": 5, "note": "self"}).status_code == 403


def test_an_admin_cannot_appoint_or_dismiss_admins_or_make_themselves_owner(client_factory, owner):
    helper_id, _ = customer(owner, "helper", plan=None, machine=False)
    friend_id, _ = customer(owner, "friend", plan=None, machine=False)
    make_admin(owner, helper_id)
    helper = as_user(client_factory, owner, helper_id)
    for target in (helper_id, friend_id, 1):
        response = helper.post(f"/api/admin/users/{target}/admin", json={"value": True})
        assert response.status_code in {403, 409}, (target, response.text)
    # Dismissing the owner or another admin is no easier.
    make_admin(owner, friend_id)
    assert helper.post(f"/api/admin/users/{friend_id}/admin", json={"value": False}).status_code == 403
    with session_scope(owner.app.state.session_factory) as db:
        assert db.get(User, friend_id).is_admin and not db.get(User, 1).is_admin  # the owner's right is config-only


def test_an_admin_cannot_ban_the_owner_another_admin_or_themselves(client_factory, owner):
    helper_id, _ = customer(owner, "helper", plan=None, machine=False)
    peer_id, _ = customer(owner, "peer", plan=None, machine=False)
    make_admin(owner, helper_id)
    make_admin(owner, peer_id)
    helper = as_user(client_factory, owner, helper_id)
    body = {"permanent": True, "reason": "coup"}
    for target in (1, peer_id, helper_id):
        assert helper.post(f"/api/admin/users/{target}/ban", json=body).status_code in {403, 409}, target
    with session_scope(owner.app.state.session_factory) as db:
        assert not any(auth.ban_active(db.get(User, i)) for i in (1, helper_id, peer_id))


@pytest.mark.parametrize("extra", ["is_owner", "is_admin", "user_id", "github_login", "role"])
def test_admin_rights_endpoint_takes_nothing_but_the_value(owner, extra):
    target, _ = customer(owner, "mallory", plan=None, machine=False)
    response = owner.post(f"/api/admin/users/{target}/admin", json={"value": True, extra: True})
    assert response.status_code == 422
    with session_scope(owner.app.state.session_factory) as db:
        assert not db.get(User, target).is_admin


@pytest.mark.parametrize("value", [None, [], {}, "maybe", 2, -1, [True]])
def test_admin_rights_value_must_be_a_real_boolean(owner, value):
    target, _ = customer(owner, "mallory", plan=None, machine=False)
    assert owner.post(f"/api/admin/users/{target}/admin", json={"value": value}).status_code == 422
    with session_scope(owner.app.state.session_factory) as db:
        assert not db.get(User, target).is_admin


@pytest.mark.xfail(
    strict=True,
    reason="FINDING: ownership is the GitHub *login* from ADMIN_GITHUB_LOGINS; a login freed "
    "by a rename can be registered by a stranger who then becomes owner (CWE-290). "
    "Fix: pin owners by numeric GitHub id.",
)
def test_a_stranger_who_takes_a_freed_owner_login_is_not_the_owner(owner):
    settings = owner.app.state.settings
    with session_scope(owner.app.state.session_factory) as db:
        auth._upsert_user(db, {"id": 1, "login": "dev-renamed"}, "en")  # the real owner renames their account
        stranger = auth._upsert_user(db, {"id": 777, "login": "dev"}, "en")  # a stranger registers the freed name
        assert not auth.is_owner(settings, stranger)


# -- hours (CWE-190, CWE-20, CWE-362) --------------------------------------------------------------------


@pytest.mark.parametrize(
    "minutes",
    [
        "0",
        "100001",
        "-100001",
        "1.5",
        '"abc"',
        "null",
        "[5]",
        '{"a": 1}',
        str(10**30),
        "Infinity",
        "-Infinity",
        "NaN",
        "1e400",
    ],
)
def test_hours_reject_every_amount_that_is_not_a_sane_whole_number(owner, minutes):
    target, _ = customer(owner)
    entries = count(owner, LedgerEntry)
    # Raw JSON text, so the odd literals (Infinity, NaN, 1e400) reach the server exactly as an attacker would send them.
    response = owner.post(
        f"/api/admin/users/{target}/hours",
        headers={"Content-Type": "application/json"},
        content=f'{{"minutes": {minutes}, "note": "test"}}',
    )
    assert response.status_code in {400, 422}, response.text
    assert count(owner, LedgerEntry) == entries


@pytest.mark.parametrize("note", ["", "  ", "ab", "x" * 301, None, 5])
def test_hours_need_a_real_note(owner, note):
    target, _ = customer(owner)
    entries = count(owner, LedgerEntry)
    response = owner.post(f"/api/admin/users/{target}/hours", json={"minutes": 5, "note": note})
    assert response.status_code in {409, 422}, response.text
    assert count(owner, LedgerEntry) == entries and not notices(owner, target)


def test_the_largest_allowed_gift_is_exact_and_one_more_is_refused(owner):
    target, _ = customer(owner, plan="pro")
    with session_scope(owner.app.state.session_factory) as db:
        before = ledger.balance_seconds(db, target)
    assert (
        owner.post(f"/api/admin/users/{target}/hours", json={"minutes": 100_000, "note": "max gift"}).status_code == 200
    )
    with session_scope(owner.app.state.session_factory) as db:
        assert ledger.balance_seconds(db, target) == before + 100_000 * 60
    assert (
        owner.post(f"/api/admin/users/{target}/hours", json={"minutes": 100_001, "note": "too much"}).status_code == 422
    )


def test_taking_back_more_than_the_balance_holds_changes_nothing(owner):
    target, _ = customer(owner, plan="start")
    with session_scope(owner.app.state.session_factory) as db:
        held = ledger.balance_seconds(db, target)
    over = held // 60 + 1
    response = owner.post(f"/api/admin/users/{target}/hours", json={"minutes": -over, "note": "too greedy"})
    assert response.status_code == 409
    with session_scope(owner.app.state.session_factory) as db:
        assert ledger.balance_seconds(db, target) == held


def test_parallel_takebacks_cannot_drive_the_balance_below_zero(owner):
    target, _ = customer(owner, plan="start")
    with session_scope(owner.app.state.session_factory) as db:
        held_minutes = ledger.balance_seconds(db, target) // 60
    chunk = held_minutes // 4
    with ThreadPoolExecutor(max_workers=12) as pool:
        codes = list(
            pool.map(
                lambda i: (
                    owner.post(
                        f"/api/admin/users/{target}/hours", json={"minutes": -chunk, "note": f"race {i}"}
                    ).status_code
                ),
                range(12),
            )
        )
    assert codes.count(200) == 4 and codes.count(409) == 8, codes
    with session_scope(owner.app.state.session_factory) as db:
        assert 0 <= ledger.balance_seconds(db, target) < 60 * 4 + chunk * 60


def test_parallel_gifts_all_land_exactly_once(owner):
    target, _ = customer(owner, plan="start")
    with session_scope(owner.app.state.session_factory) as db:
        before = ledger.balance_seconds(db, target)
    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(
            pool.map(
                lambda i: (
                    owner.post(f"/api/admin/users/{target}/hours", json={"minutes": 7, "note": f"gift {i}"}).status_code
                ),
                range(30),
            )
        )
    assert set(codes) == {200}
    with session_scope(owner.app.state.session_factory) as db:
        assert ledger.balance_seconds(db, target) == before + 30 * 7 * 60
    assert count(owner, AdminAction) == 30


def test_hours_cannot_be_given_to_nobody_or_to_an_expired_plan(owner):
    nobody, _ = customer(owner, "nobody", plan=None, machine=False)
    expired, _ = customer(owner, "expired", plan="start", machine=False)
    with session_scope(owner.app.state.session_factory) as db:
        for row in db.scalars(select(Subscription).where(Subscription.user_id == expired)):
            row.current_period_end = utcnow() - timedelta(seconds=1)
    entries = count(owner, LedgerEntry)
    for target in (nobody, expired):
        assert owner.post(f"/api/admin/users/{target}/hours", json={"minutes": 60, "note": "gift"}).status_code == 409
    assert count(owner, LedgerEntry) == entries
    assert not notices(owner, nobody) and not notices(owner, expired)


def test_a_failed_change_leaves_neither_a_notice_nor_an_audit_row(owner):
    target, _ = customer(owner, plan=None, machine=False)
    actions, notes = count(owner, AdminAction), count(owner, Notification)
    for path, body in [
        ("hours", {"minutes": 60, "note": "gift"}),
        ("hours", {"minutes": 0, "note": "gift"}),
        ("plan", {"plan": "nonsense"}),
        ("ban", {"reason": "no length at all"}),
        ("ban", {"permanent": True, "days": 3, "reason": "both lengths"}),
    ]:
        assert owner.post(f"/api/admin/users/{target}/{path}", json=body).status_code in {400, 409, 422}
    assert (count(owner, AdminAction), count(owner, Notification)) == (actions, notes)


def test_a_gift_is_noted_in_the_audit_trail_with_the_acting_admin(client_factory, owner):
    helper_id, _ = customer(owner, "helper", plan=None, machine=False)
    target, _ = customer(owner, "alice", plan="pro")
    make_admin(owner, helper_id)
    helper = as_user(client_factory, owner, helper_id)
    assert (
        helper.post(f"/api/admin/users/{target}/hours", json={"minutes": 30, "note": "compensation"}).status_code == 200
    )
    with session_scope(owner.app.state.session_factory) as db:
        row = db.scalars(select(AdminAction).where(AdminAction.action == "hours")).one()
        assert (row.admin_id, row.user_id, row.detail["minutes"]) == (helper_id, target, 30)


# -- plans -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"plan": "hours"},
        {"plan": "PRO"},
        {"plan": ""},
        {"plan": "free"},
        {"plan": None},
        {"plan": "pro", "days": 0},
        {"plan": "pro", "days": -1},
        {"plan": "pro", "days": 3661},
        {"plan": "pro", "days": 1.5},
        {"plan": "pro", "days": "abc"},
        {"plan": "pro", "days": 10**30},
        {"plan": "pro", "note": "x" * 301},
    ],
)
def test_a_plan_grant_refuses_nonsense(owner, body):
    target, _ = customer(owner, plan=None, machine=False)
    assert owner.post(f"/api/admin/users/{target}/plan", json=body).status_code == 422
    assert usable_rows(owner, target) == 0


def test_the_longest_and_shortest_plans_are_accepted_exactly(owner):
    target, _ = customer(owner, plan=None, machine=False)
    for days in (1, 3660):
        response = owner.post(f"/api/admin/users/{target}/plan", json={"plan": "start", "days": days})
        assert response.status_code == 200
        assert usable_rows(owner, target) == 1


def test_parallel_plan_grants_leave_exactly_one_running_plan(owner):
    target, _ = customer(owner, plan=None, machine=False)
    plan_names = ["start", "pro", "max", "start", "pro", "max"] * 3
    with ThreadPoolExecutor(max_workers=9) as pool:
        codes = list(
            pool.map(
                lambda name: owner.post(f"/api/admin/users/{target}/plan", json={"plan": name}).status_code, plan_names
            )
        )
    assert set(codes) == {200}
    assert usable_rows(owner, target) == 1


def test_a_grant_over_a_paid_plan_never_doubles_the_hours_left_on_it(owner):
    target, _ = customer(owner, plan="pro", machine=False)
    with session_scope(owner.app.state.session_factory) as db:
        held = ledger.balance_seconds(db, target)
    for _ in range(3):
        assert owner.post(f"/api/admin/users/{target}/plan", json={"plan": "pro"}).status_code == 200
    with session_scope(owner.app.state.session_factory) as db:
        pro_seconds = ledger.balance_seconds(db, target)
    # A fresh period of 'pro' plus what was carried: it can only grow by the carried amount each time, never explode.
    assert held <= pro_seconds <= held + 4 * held


def test_an_admin_grant_does_not_spend_the_trial_of_a_person_who_never_used_it(owner):
    target, _ = customer(owner, plan=None, machine=False)
    assert owner.post(f"/api/admin/users/{target}/plan", json={"plan": "pro"}).status_code == 200
    with session_scope(owner.app.state.session_factory) as db:
        assert db.get(User, target).trial_used_at is None


# -- what an admin can see (CWE-200, CWE-359) ------------------------------------------------------------


def test_the_panel_never_returns_a_secret(owner):
    target, _ = customer(owner, "carol", plan="pro")
    with session_scope(owner.app.state.session_factory) as db:
        user = db.get(User, target)
        user.github_token_enc = "gAAAA-github-token-sealed"
        user.model_key_enc = "gAAAA-model-key-sealed"
        user.chatgpt_auth_enc = "gAAAA-chatgpt-sealed" if hasattr(user, "chatgpt_auth_enc") else ""
    bodies = [owner.get("/api/admin/users").text, owner.get(f"/api/admin/users/{target}").text]
    for text in bodies:
        for needle in ("gAAAA", "token_enc", "key_enc", "session_version", "password", "secret"):
            assert needle not in text, needle


@pytest.mark.parametrize(
    "term", ["' OR 1=1 --", "%", "_", "\\", "\\%", "a" * 5000, "\x00", "😀", "' UNION SELECT 1,2,3 --"]
)
def test_search_terms_are_data_not_syntax(owner, term):
    customer(owner, "alice")
    response = owner.get("/api/admin/users", params={"q": term})
    assert response.status_code == 200, response.text
    assert all(isinstance(row["id"], int) for row in response.json()["users"])


BIG = [
    pytest.param(
        str(2**63),
        marks=pytest.mark.xfail(
            strict=True,
            reason="FINDING: an integer above 2**63 reaches SQLite and ends in an unhandled "
            "OverflowError, answered 500 (CWE-190). Fix: bound the path/query integers (ge=1, le=2**31).",
        ),
    ),
    pytest.param(
        str(2**70),
        marks=pytest.mark.xfail(
            strict=True,
            reason="FINDING: an integer above 2**63 reaches SQLite and ends in an unhandled "
            "OverflowError, answered 500 (CWE-190). Fix: bound the path/query integers (ge=1, le=2**31).",
        ),
    ),
]


@pytest.mark.parametrize("offset", ["-1", "0", "9999999999", *BIG, "abc", "1.5", ""])
def test_paging_offsets_never_crash_the_server(owner, offset):
    assert owner.get("/api/admin/users", params={"offset": offset}).status_code < 500


@pytest.mark.parametrize("user_id", ["0", "-1", "999999", str(2**31), *BIG, "abc", "1.5", "1;DROP TABLE users"])
def test_odd_user_ids_are_a_clean_refusal_never_a_crash(owner, user_id):
    for method, path in admin_routes(owner.app):
        if "{user_id}" not in path:
            continue
        response = call(owner, method, path, user_id)
        assert response.status_code in {404, 422}, (method, path, user_id, response.status_code)


def test_the_note_is_stored_as_text_and_served_as_json_only(owner):
    target, _ = customer(owner)
    note = "<script>alert(1)</script>"
    assert owner.post(f"/api/admin/users/{target}/hours", json={"minutes": 5, "note": note}).status_code == 200
    page = owner.get(f"/api/admin/users/{target}")
    assert page.headers["content-type"].startswith("application/json")
    assert page.headers["x-content-type-options"] == "nosniff"
    assert note in [a["detail"].get("note") for a in page.json()["actions"]]


def test_a_validation_error_never_echoes_what_was_sent(owner):
    target, _ = customer(owner)
    secret = "SENTINEL-do-not-echo-9f3a"
    response = owner.post(f"/api/admin/users/{target}/hours", json={"minutes": secret, "note": secret})
    assert response.status_code == 422 and secret not in response.text


# -- the bell and bans -----------------------------------------------------------------------------------


def test_a_banned_person_is_shut_out_of_every_part_of_the_api_but_can_ask_who_they_are_and_sign_out(
    client_factory, owner
):
    target, _ = customer(owner, "troll", plan="pro")
    victim = as_user(client_factory, owner, target)
    assert victim.get("/api/billing/subscription").status_code == 200
    assert owner.post(f"/api/admin/users/{target}/ban", json={"permanent": True, "reason": "abuse"}).status_code == 200
    for method, path, body in [
        ("GET", "/api/billing/subscription", None),
        ("GET", "/api/billing/eligibility", None),
        ("POST", "/api/billing/cancel", {}),
        ("POST", "/api/billing/checkout", {"plan": "start"}),
        ("POST", "/api/billing/sync", {}),
        ("POST", "/api/tasks", {"goal": "x"}),
        ("PATCH", "/api/me", {"locale": "en"}),
        ("GET", "/api/notifications", None),
    ]:
        response = victim.request(method, path, json=body)
        assert response.status_code == 403 and response.json()["error"]["code"] == "banned", (method, path)
    me = victim.get("/api/me")
    assert me.status_code == 200 and me.json()["ban"]["permanent"] is True
    assert victim.post("/auth/logout").status_code == 204


def test_a_temporary_ban_ends_to_the_second_and_not_before(client_factory, owner):
    target, _ = customer(owner, "troll", plan="pro")
    victim = as_user(client_factory, owner, target)
    owner.post(f"/api/admin/users/{target}/ban", json={"days": 1, "reason": "cool off"})
    with session_scope(owner.app.state.session_factory) as db:
        db.get(User, target).ban_until = utcnow() + timedelta(seconds=30)
    assert victim.get("/api/billing/subscription").status_code == 403
    with session_scope(owner.app.state.session_factory) as db:
        db.get(User, target).ban_until = utcnow() - timedelta(seconds=1)
    assert victim.get("/api/billing/subscription").status_code == 200


def test_unbanning_someone_who_is_not_banned_is_refused_and_not_logged(owner):
    target, _ = customer(owner)
    actions = count(owner, AdminAction)
    assert owner.post(f"/api/admin/users/{target}/unban").status_code == 409
    assert count(owner, AdminAction) == actions


def test_a_cookie_with_a_tampered_signature_is_nobody(client_factory, owner):
    a, _ = customer(owner, "aaa")
    cookie = as_user(client_factory, owner, a).cookies.get(auth.SESSION_COOKIE)
    forged = cookie[:-3] + ("AAA" if not cookie.endswith("AAA") else "BBB")
    stranger = client_factory(AUTH_DEV_LOGIN="true")
    stranger.cookies.set(auth.SESSION_COOKIE, forged)
    assert stranger.get("/api/me").status_code == 401


# -- refunds on disputes: the other way hours are added ----------------------------------------------------


def open_dispute(owner, user_id, task_of=None):
    """A dispute row for ``user_id`` (about a task of ``task_of`` if given); returns its id."""
    with session_scope(owner.app.state.session_factory) as db:
        task_id = None
        if task_of is not None:
            machine = make_machine(db, db.get(User, task_of))
            task_id = make_task(db, db.get(User, task_of), machine, status="finished").id
        dispute = Dispute(user_id=user_id, task_id=task_id, message="it looped")
        db.add(dispute)
        db.flush()
        return dispute.id


@pytest.mark.xfail(
    strict=True,
    reason="FINDING (CWE-362): admin_refund checks `status == open` without lock_account, so "
    "simultaneous clicks each pass the check and each pay out (3 refunds of 30 min seen for one dispute). "
    "Fix: lock_account(db, dispute.user_id) and re-read the dispute before checking its status.",
)
def test_a_refund_pays_out_once_even_when_the_button_is_hammered(owner):
    target, _ = customer(owner, plan="start", machine=False)
    dispute = open_dispute(owner, target)
    with session_scope(owner.app.state.session_factory) as db:
        before = ledger.balance_seconds(db, target)
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(
            pool.map(
                lambda _: (
                    owner.post(
                        f"/api/admin/disputes/{dispute}/refund", json={"minutes": 30, "note": "our fault"}
                    ).status_code
                ),
                range(8),
            )
        )
    with session_scope(owner.app.state.session_factory) as db:
        refunded = ledger.balance_seconds(db, target) - before
    assert codes.count(200) == 1 and refunded == 30 * 60, (codes, refunded)


@pytest.mark.parametrize("minutes", [0, -1, 100_001, 1.5, None, "abc"])
def test_a_refund_amount_is_a_positive_whole_bounded_number(owner, minutes):
    target, _ = customer(owner, plan="start", machine=False)
    dispute = open_dispute(owner, target)
    response = owner.post(f"/api/admin/disputes/{dispute}/refund", json={"minutes": minutes, "note": "our fault"})
    assert response.status_code == 422
    with session_scope(owner.app.state.session_factory) as db:
        assert db.get(Dispute, dispute).status == "open"


def test_a_settled_dispute_cannot_be_refunded_again_or_refunded_after_closing(owner):
    target, _ = customer(owner, plan="start", machine=False)
    first, second = open_dispute(owner, target), open_dispute(owner, target)
    assert (
        owner.post(f"/api/admin/disputes/{first}/refund", json={"minutes": 5, "note": "our fault"}).status_code == 200
    )
    assert owner.post(f"/api/admin/disputes/{first}/refund", json={"minutes": 5, "note": "again"}).status_code == 404
    assert owner.post(f"/api/admin/disputes/{second}/close").status_code == 200
    assert owner.post(f"/api/admin/disputes/{second}/refund", json={"minutes": 5, "note": "late"}).status_code == 404


def test_a_refund_into_no_plan_is_refused_and_the_dispute_stays_open(owner):
    target, _ = customer(owner, plan=None, machine=False)
    dispute = open_dispute(owner, target)
    assert (
        owner.post(f"/api/admin/disputes/{dispute}/refund", json={"minutes": 5, "note": "our fault"}).status_code == 409
    )
    with session_scope(owner.app.state.session_factory) as db:
        assert db.get(Dispute, dispute).status == "open"


def test_nobody_can_dispute_a_charge_that_is_not_theirs(client_factory, owner):
    victim, _ = customer(owner, "victim", plan="start", machine=False)
    attacker, _ = customer(owner, "attacker", plan="start", machine=False)
    task_of_victim = open_dispute(owner, victim, task_of=victim)
    with session_scope(owner.app.state.session_factory) as db:
        stolen_task = db.get(Dispute, task_of_victim).task_id
    response = as_user(client_factory, owner, attacker).post(
        "/api/disputes", json={"task_id": stolen_task, "message": "mine"}
    )
    assert response.status_code == 404


def test_one_persons_notifications_are_not_another_persons(client_factory, owner):
    a, _ = customer(owner, "aaa")
    b, _ = customer(owner, "bbb")
    owner.post(f"/api/admin/users/{a}/hours", json={"minutes": 5, "note": "for a only"})
    reader_b = as_user(client_factory, owner, b)
    assert reader_b.get("/api/notifications").json()["items"] == []
    reader_b.post("/api/notifications/read")
    assert any(not item["read"] for item in as_user(client_factory, owner, a).get("/api/notifications").json()["items"])
