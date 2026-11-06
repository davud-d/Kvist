# -*- coding: utf-8 -*-
"""Adversarial requests: billing ownership, replay, trial eligibility, admission and input boundaries."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from conftest import dev_login, make_machine, make_subscription, make_task, make_user
from paddle_fake import open_checkout, paid_client
from sqlalchemy import event, func, select
from test_billing import balance, webhook
from test_tasks_api import signed_in

from kvist_cloud import billing, limits
from kvist_cloud.db import lock_account, make_session_factory, session_scope, utcnow
from kvist_cloud.models import BillingCheckout, Machine, Subscription, Task, TaskStatus, User


@pytest.mark.parametrize("age,eligible",
                         [(None, False), (0, False), (30, False), (31, False), (32, True), (365, True)])
def test_trial_uses_strict_calendar_month(age, eligible):
    now = datetime(2026, 11, 6, tzinfo=UTC)
    created = now - timedelta(days=age) if age is not None else None
    user = User(github_id=1, github_login="test", github_created_at=created)
    assert billing.trial_eligible(user, now) is eligible
    user.trial_used_at = now
    assert not billing.trial_eligible(user, now)


def test_month_boundary_and_leap_year():
    now = datetime(2024, 3, 31, tzinfo=UTC)
    user = User(github_id=1, github_login="test", github_created_at=datetime(2024, 2, 29, tzinfo=UTC))
    assert not billing.trial_eligible(user, now)
    user.github_created_at -= timedelta(microseconds=1)
    assert billing.trial_eligible(user, now)


@pytest.mark.parametrize("profile", [None, "2026-11-06T00:00:00Z", "not-a-date"])
def test_unknown_young_or_invalid_github_age_cannot_checkout(client_factory, profile):
    client, paddle = paid_client(client_factory)
    with session_scope(client.app.state.session_factory) as db:
        user = db.get(User, 1)
        user.github_created_at = billing._parse_time(profile)
    response = client.post("/api/billing/checkout", json={"plan": "trial"})
    assert response.status_code == 403
    assert not paddle.requests


def test_unbound_checkout_metadata_cannot_mint_anything(client_factory, engine):
    client, paddle = paid_client(client_factory)
    payment = paddle.payment("txn_unowned", "max")
    payment["custom_data"] = {"user_id": 1, "kind": "hours", "plan": "max", "hours": 999999}
    webhook(client, "evt_forged_metadata", "transaction.completed", payment)
    webhook(client, "evt_subscription", "subscription.created", paddle.subscriptions["sub_01"] | {
        "transaction_id": "txn_unowned", "custom_data": {"user_id": 1}})
    assert balance(engine) == 0
    assert not paddle.requests


@pytest.mark.parametrize("change", [
    {"status": "paid"}, {"currency_code": "EUR"}, {"payments": []},
    {"payments": [{"status": "authorized", "amount": "100"}]},
    {"details": {"totals": {"total": "0", "credit": "100"}}},
    {"items": [{"price": {"id": "pri_max"}, "quantity": 1}]},
    {"items": [{"price": {"id": "pri_trial"}, "quantity": 150}]},
])
def test_trial_requires_correct_price_quantity_and_captured_dollar(client_factory, engine, change):
    client, paddle = paid_client(client_factory)
    data = paddle.payment(open_checkout(client)) | change
    assert webhook(client, "evt_bad_payment", "transaction.completed", data).status_code == 200
    assert balance(engine) == 0
    assert not paddle.adjustments


def test_trial_waits_for_approved_refund_then_cannot_repeat(client_factory, engine):
    client, paddle = paid_client(client_factory)
    paddle.refund_status = "pending_approval"
    payment = paddle.payment(open_checkout(client))
    webhook(client, "evt_paid", "transaction.completed", payment)
    assert balance(engine) == 0
    # The browser claiming success, or a subscription event, is insufficient.
    webhook(client, "evt_early", "subscription.created", paddle.subscriptions["sub_01"])
    assert balance(engine) == 0
    paddle.adjustments[0]["status"] = "approved"
    webhook(client, "evt_refund", "adjustment.updated", paddle.adjustments[0])
    assert balance(engine) == 5 * 3600
    assert len(paddle.adjustments) == 1
    with session_scope(client.app.state.session_factory) as db:
        row = db.scalar(select(Subscription))
        row.status = "cancelled"
    assert client.post("/api/billing/checkout", json={"plan": "trial"}).status_code == 403


def test_payment_and_refund_replays_with_distinct_event_ids_are_idempotent(client_factory, engine):
    client, paddle = paid_client(client_factory)
    payment = paddle.payment(open_checkout(client))
    for i in range(4):
        assert webhook(client, f"evt_paid_{i}", "transaction.completed", payment).status_code == 200
    assert balance(engine) == 5 * 3600
    assert len(paddle.adjustments) == 1
    pack = paddle.payment(open_checkout(client, "hours"), "hours")
    for i in range(4):
        assert webhook(client, f"evt_pack_{i}", "transaction.completed", pack).status_code == 200
    assert balance(engine) == 15 * 3600


def test_checkout_cannot_be_reassigned_by_custom_data(client_factory, engine):
    client, paddle = paid_client(client_factory)
    with session_scope(client.app.state.session_factory) as db:
        other = make_user(db, "other", 2)
        other_id = other.id
    payment = paddle.payment(open_checkout(client, "pro"), "pro")
    payment["custom_data"] = {"user_id": other_id, "plan": "max", "kind": "hours"}
    webhook(client, "evt_paid", "transaction.completed", payment)
    assert balance(engine) == 150 * 3600
    with session_scope(client.app.state.session_factory) as db:
        assert db.scalar(select(Subscription.user_id)) == 1


def test_stale_webhook_cannot_reactivate_cancelled_subscription(client_factory, engine):
    client, paddle = paid_client(client_factory)
    payment = paddle.payment(open_checkout(client, "start"), "start")
    webhook(client, "evt_paid", "transaction.completed", payment)
    stale = copy.deepcopy(paddle.subscriptions["sub_01"])
    paddle.subscriptions["sub_01"].update(
        status="canceled", updated_at=utcnow().isoformat(), current_billing_period=None)
    webhook(client, "evt_cancel", "subscription.canceled", paddle.subscriptions["sub_01"])
    webhook(client, "evt_old", "subscription.activated", stale)
    assert balance(engine) == 0


def test_unpaid_upgrade_cannot_raise_plan_or_hours(client_factory, engine):
    client, paddle = paid_client(client_factory)
    webhook(client, "evt_paid", "transaction.completed", paddle.payment(open_checkout(client, "start"), "start"))
    snapshot = paddle.subscriptions["sub_01"]
    snapshot["items"][0]["price"]["id"] = "pri_max"
    snapshot["updated_at"] = utcnow().isoformat()
    webhook(client, "evt_upgrade", "subscription.updated", snapshot)
    view = client.get("/api/billing/subscription").json()
    assert view["plan"] == "start" and view["balance_seconds"] == 50 * 3600


@pytest.mark.parametrize("extra", ["plan", "status", "is_admin", "balance_seconds", "trial_used_at",
                                    "github_created_at",
                                    "paddle_customer_id", "max_parallel_tasks"])
def test_profile_cannot_change_server_owned_fields(client_factory, extra):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    response = client.patch("/api/me", json={"locale": "en", extra: 150})
    assert response.status_code == 422
    assert not client.get("/api/me").json()["is_admin"]


def test_dev_billing_requires_explicit_loopback_dev_login(client_factory):
    from fastapi import Response

    from kvist_cloud.auth import set_session

    client = client_factory(PUBLIC_URL="https://cloud.example")
    with session_scope(client.app.state.session_factory) as db:
        user = make_user(db)
        response = Response()
        set_session(response, client.app.state.settings, user)
        cookie = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    client.cookies.set("kc_session", cookie)
    for path in ("skip-checkout", "buy-hours"):
        assert client.post(f"/api/billing/dev/{path}").status_code == 404


def test_expired_subscription_cannot_start_or_resume(client_factory, engine):
    client = signed_in(client_factory, engine)
    task = client.post("/api/tasks", json={"goal": "first"}).json()
    with session_scope(client.app.state.session_factory) as db:
        db.scalar(select(Subscription)).current_period_end = utcnow() - timedelta(seconds=1)
        db.get(Task, task["id"]).status = TaskStatus.PAUSED
    assert client.post("/api/tasks", json={"goal": "after expiry"}).status_code == 402
    assert client.post(f"/api/tasks/{task['id']}/resume").status_code == 402
    with session_scope(client.app.state.session_factory) as db:
        assert limits.promote(db, 1) == []


def test_150_concurrent_admissions_cannot_exceed_one_slot(engine):
    factory = make_session_factory(engine)
    with session_scope(factory) as db:
        user = make_user(db)
        make_subscription(db, user)
        machine = make_machine(db, user)
        user_id, machine_id = user.id, machine.id

    def submit(index):
        with session_scope(factory) as db:
            lock_account(db, user_id)
            task = Task(user_id=user_id, machine_id=machine_id, goal=f"task {index}")
            db.add(task)
            db.flush()
            limits.admit(db, task)
            return task.status

    with ThreadPoolExecutor(max_workers=20) as pool:
        statuses = list(pool.map(submit, range(150)))
    assert statuses.count(TaskStatus.STARTING) == 1
    assert statuses.count(TaskStatus.QUEUED) == 149


SQL = "'; UPDATE users SET session_version=999; --"
XSS = '<img src=x onerror="globalThis.__xss=1"><script>globalThis.__xss=1</script>'
# All declared JSON body fields, including numeric/boolean fields that must reject strings.
FIELDS = [
    ("PATCH", "/api/me", {"locale": "en"}, "locale"),
    *[("POST", "/api/model-key", {"provider": "deepseek", "key": "test-key-12345678"}, k)
      for k in ("provider", "key", "base_url", "model")],
    *[("POST", "/api/tasks", {"goal": "safe"}, k) for k in ("goal", "repo_url", "project_id")],
    ("POST", "/api/tasks/1/messages", {"text": "safe"}, "text"),
    *[("POST", "/api/tasks/1/input", {"request_id": "r1"}, k) for k in ("request_id", "value", "approved")],
    *[("POST", "/api/projects", {"name": "safe"}, k) for k in ("name", "repo_url")],
    *[("POST", "/api/disputes", {"message": "please review"}, k) for k in ("task_id", "message")],
    *[("POST", "/api/admin/disputes/1/refund", {"minutes": 1, "note": "refund"}, k) for k in ("minutes", "note")],
    ("POST", "/api/billing/checkout", {"plan": "trial"}, "plan"),
]


@pytest.mark.parametrize("attack", [SQL, XSS], ids=["sql", "xss"])
@pytest.mark.parametrize("method,path,body,field", FIELDS, ids=[f"{p}:{f}" for _, p, _, f in FIELDS])
def test_injection_in_every_body_field(client_factory, engine, method, path, body, field, attack):
    import httpx

    client = client_factory(lambda _: httpx.Response(200, json={"is_available": True}),
                            AUTH_DEV_LOGIN="true", ADMIN_GITHUB_LOGINS="dev")
    dev_login(client)
    client.post("/api/billing/dev/skip-checkout")
    client.post("/api/model-key", json={"provider": "deepseek", "key": "test-key-12345678"})
    with session_scope(client.app.state.session_factory) as db:
        user = db.get(User, 1)
        machine = db.scalar(select(Machine))
        make_task(db, user, machine, status=TaskStatus.WAITING_FOR_USER)
    client.post("/api/disputes", json={"message": "please review", "task_id": 1})
    statements = []
    def observe(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)
    event.listen(client.app.state.engine, "before_cursor_execute", observe)
    try:
        response = client.request(method, path, json=body | {field: attack})
    finally:
        event.remove(client.app.state.engine, "before_cursor_execute", observe)
    assert response.status_code in {200, 201, 400, 402, 403, 409, 422, 503}, response.text
    assert "application/json" in response.headers["content-type"]
    assert all(attack not in statement for statement in statements)
    with session_scope(client.app.state.session_factory) as db:
        assert db.get(User, 1).session_version == 1
        assert db.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.parametrize("attack", [SQL, XSS])
@pytest.mark.parametrize("path,param", [("/api/sessions/{attack}", None), ("/api/tasks/{attack}", None),
                                       ("/api/tasks/1/events", "after"), ("/api/admin/funnel", "days"),
                                       ("/auth/github/login", "next"), ("/auth/github/callback", "code"),
                                       ("/auth/github/callback", "state")])
def test_injection_in_path_and_query_fields(client_factory, path, param, attack):
    from urllib.parse import quote
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    response = client.get(path.replace("{attack}", quote(attack, safe="")),
                          params={param: attack} if param else None, follow_redirects=False)
    assert response.status_code in {302, 400, 403, 404, 422}
    assert "<script>" not in response.text


def test_body_field_matrix_covers_every_api_input(app_factory):
    covered = {(method, path, field) for method, path, _, field in FIELDS}
    declared = set()
    schema = app_factory().openapi()
    for path, operations in schema["paths"].items():
        normalized = path.replace("{task_id}", "1").replace("{dispute_id}", "1")
        for method, operation in operations.items():
            body = operation.get("requestBody", {}).get("content", {}).get("application/json")
            if body is None:
                continue
            name = body["schema"]["$ref"].rsplit("/", 1)[-1]
            declared.update((method.upper(), normalized, field)
                            for field in schema["components"]["schemas"][name]["properties"])
    assert declared == covered


def test_150_parallel_http_creations_have_only_one_active_task(client_factory, engine):
    client = signed_in(client_factory, engine)
    with ThreadPoolExecutor(max_workers=15) as pool:
        responses = list(pool.map(lambda i: client.post("/api/tasks", json={"goal": f"job {i}"}), range(150)))
    assert all(response.status_code == 201 for response in responses)
    with session_scope(client.app.state.session_factory) as db:
        assert limits.active_count(db, 1) == 1
        assert db.scalar(select(func.count()).select_from(Task).where(Task.status == "queued")) == 149
        assert db.scalar(select(func.count()).select_from(Machine)) == 1


@pytest.mark.parametrize("override", [{"tax_mode": "external"}, {"trial_period": None},
                                      {"unit_price": {"amount": "100", "currency_code": "USD"}}])
def test_incorrect_price_configuration_fails_before_checkout(client_factory, override):
    client, paddle = paid_client(client_factory)
    paddle.price_override = override
    assert client.post("/api/billing/checkout", json={"plan": "trial"}).status_code == 503
    assert not paddle.transactions


def test_private_repo_token_only_reaches_runner_clone(client_factory, engine):
    from kvist_cloud.gateway import start_message

    client = signed_in(client_factory, engine)
    token = "gho_private_repository_canary123456"
    with session_scope(client.app.state.session_factory) as db:
        db.get(User, 1).github_token_enc = client.app.state.vault.encrypt(token)
    response = client.post("/api/tasks", json={"goal": "edit private code", "repo_url": "https://github.com/o/private.git"})
    assert response.status_code == 201
    with session_scope(client.app.state.session_factory) as db:
        message = start_message(client.app, db, db.get(Task, response.json()["id"]))
    assert message["github_token"] == token
    assert token not in str(message["env"])
    for path in ("/api/me", "/api/tasks", "/api/projects", "/api/billing/subscription"):
        assert token not in client.get(path).text


def test_profile_script_terminator_cannot_escape_boot_json(client_factory, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "app.en.html").write_text('<script id="boot" type="application/json">null</script>')
    client = client_factory(AUTH_DEV_LOGIN="true", FRONTEND_DIST=str(dist))
    dev_login(client)
    attack = '</script><img src=x onerror="globalThis.__xss=1">'
    with session_scope(client.app.state.session_factory) as db:
        user = db.get(User, 1)
        user.name = attack
        user.github_login = attack
    response = client.get("/start")
    assert attack not in response.text
    assert response.text.count("</script>") == 1
    assert "\\u003c/script>" in response.text


def test_local_provider_cannot_be_exposed_as_public_hosting(settings):
    from dataclasses import replace

    from kvist_cloud.providers import ProviderError, make_provider

    with pytest.raises(ProviderError, match="tenant isolation"):
        make_provider(replace(settings, public_url="https://cloud.example"))


@pytest.mark.parametrize("before_payment", [False, True])
def test_refunded_hour_pack_is_reversed_once_even_out_of_order(client_factory, engine, before_payment):
    client, paddle = paid_client(client_factory)
    webhook(client, "evt_start", "transaction.completed", paddle.payment(open_checkout(client, "start"), "start"))
    txn = open_checkout(client, "hours")
    payment = paddle.payment(txn, "hours")
    adjustment = {"id": "adj_hours", "transaction_id": txn, "status": "approved", "currency_code": "USD",
                  "action": "refund", "type": "full", "totals": {"total": "500"}}
    if before_payment:
        webhook(client, "evt_refund_early", "adjustment.updated", adjustment)
    webhook(client, "evt_pack", "transaction.completed", payment)
    for i in range(3):
        assert webhook(client, f"evt_refund_{i}", "adjustment.updated", adjustment).status_code == 200
    assert balance(engine) == 50 * 3600
    webhook(client, "evt_paid_again", "transaction.completed", payment)
    assert balance(engine) == 50 * 3600


@pytest.mark.parametrize("before_payment", [False, True])
def test_chargeback_does_not_leave_paid_plan_active(client_factory, engine, before_payment):
    client, paddle = paid_client(client_factory)
    txn = open_checkout(client, "max")
    payment = paddle.payment(txn, "max")
    adjustment = {"id": "adj_dispute", "transaction_id": txn, "status": "approved",
                  "action": "chargeback", "type": "full"}
    if before_payment:
        webhook(client, "evt_chargeback_early", "adjustment.created", adjustment)
    webhook(client, "evt_paid", "transaction.completed", payment)
    webhook(client, "evt_chargeback", "adjustment.created", adjustment)
    webhook(client, "evt_stale_active", "subscription.activated", paddle.subscriptions["sub_01"])
    assert balance(engine) == 0


def test_partial_refund_removes_only_refunded_pack_hours(client_factory, engine):
    client, paddle = paid_client(client_factory)
    webhook(client, "evt_start", "transaction.completed", paddle.payment(open_checkout(client, "start"), "start"))
    txn = open_checkout(client, "hours")
    webhook(client, "evt_pack", "transaction.completed", paddle.payment(txn, "hours"))
    adjustment = {"id": "adj_partial", "transaction_id": txn, "status": "approved", "currency_code": "USD",
                  "action": "refund", "type": "partial", "totals": {"total": "250"}}
    webhook(client, "evt_refund", "adjustment.updated", adjustment)
    assert balance(engine) == 55 * 3600


def test_checkout_abandoned_mid_creation_does_not_block_forever(client_factory, engine):
    client, paddle = paid_client(client_factory)
    with session_scope(client.app.state.session_factory) as db:
        db.add(BillingCheckout(user_id=1, kind="trial", price_id="pri_trial"))
    assert client.post("/api/billing/checkout", json={"plan": "trial"}).status_code == 409
    with session_scope(client.app.state.session_factory) as db:
        db.scalar(select(BillingCheckout)).created_at = utcnow() - billing.CHECKOUT_CREATE_TIMEOUT
    assert open_checkout(client).startswith("txn_")
    with session_scope(client.app.state.session_factory) as db:
        assert db.scalar(select(func.count()).select_from(BillingCheckout)) == 1


def test_hours_gone_before_start_pause_the_task_not_blame_the_key(client_factory, engine):
    from kvist_cloud.gateway import dispatch

    client = signed_in(client_factory, engine)
    with session_scope(client.app.state.session_factory) as db:
        machine = make_machine(db, db.get(User, 1))
        task_id = make_task(db, db.get(User, 1), machine, status=TaskStatus.STARTING).id
        db.get(Subscription, 1).status = "cancelled"
    client.app.state.gateway.connected = lambda machine_id: True
    client.portal.call(dispatch, client.app, machine.id)
    with session_scope(client.app.state.session_factory) as db:
        task = db.get(Task, task_id)
        assert (task.status, task.status_reason) == (TaskStatus.PAUSED, "hours_exhausted")


def test_machine_never_created_is_replaced_and_keeps_its_tasks(client_factory, engine):
    client = signed_in(client_factory, engine)
    with session_scope(client.app.state.session_factory) as db:
        broken = db.scalars(select(Machine)).one()
        broken.status, broken.provider_ref = "error", ""
        waiting = make_task(db, db.get(User, 1), broken, status=TaskStatus.QUEUED).id
    response = client.post("/api/tasks", json={"goal": "try again"})
    assert response.status_code == 201
    with session_scope(client.app.state.session_factory) as db:
        live = [m for m in db.scalars(select(Machine)) if m.status != "deleted"]
        assert len(live) == 1 and live[0].id != broken.id
        assert db.get(Task, waiting).machine_id == live[0].id
        assert db.get(Task, response.json()["id"]).machine_id == live[0].id
