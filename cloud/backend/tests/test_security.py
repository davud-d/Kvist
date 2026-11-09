# -*- coding: utf-8 -*-
"""Adversarial requests: billing ownership, replay, trial eligibility, admission and input boundaries."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from conftest import dev_login, make_machine, make_subscription, make_task, make_user
from platega_fake import MERCHANT, SECRET, callback, open_checkout, paid_client, pay_and_notify
from sqlalchemy import event, func, select
from test_billing import balance, checkout_row
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


def test_second_trial_request_says_the_trial_is_used_not_that_the_account_is_young(client_factory):
    client, _ = paid_client(client_factory)
    with session_scope(client.app.state.session_factory) as db:
        db.get(User, 1).github_created_at = utcnow() - timedelta(days=400)
    assert client.post("/api/billing/checkout", json={"plan": "trial"}).status_code == 200
    response = client.post("/api/billing/checkout", json={"plan": "trial"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "trial_used"


@pytest.mark.parametrize("age", [None, timedelta(0), timedelta(days=20)])
def test_unknown_or_young_github_age_cannot_start_a_trial(client_factory, age):
    client, platega = paid_client(client_factory)
    with session_scope(client.app.state.session_factory) as db:
        user = db.get(User, 1)
        user.github_created_at = utcnow() - age if age is not None else None
    response = client.post("/api/billing/checkout", json={"plan": "trial"})
    assert response.status_code == 403
    assert not platega.requests


@pytest.mark.parametrize("extra", ["plan", "status", "is_admin", "balance_seconds", "trial_used_at",
                                    "github_created_at",
                                    "payment_status", "max_parallel_tasks"])
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
    *[("POST", "/api/admin/users/1/plan", {"plan": "pro"}, k) for k in ("plan", "days", "note")],
    *[("POST", "/api/admin/users/1/hours", {"minutes": 5, "note": "gift"}, k) for k in ("minutes", "note")],
    ("POST", "/api/admin/users/1/admin", {"value": True}, "value"),
    *[("POST", "/api/admin/users/1/ban", {"days": 1, "reason": "abuse"}, k) for k in ("permanent", "days", "reason")],
    *[("POST", "/api/billing/checkout", {"plan": "trial"}, k) for k in ("plan", "method")],
    ("POST", "/api/chatgpt/start", {"next": "/onboarding"}, "next"),
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
        normalized = path.replace("{task_id}", "1").replace("{dispute_id}", "1").replace("{user_id}", "1")
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


# -- billing: the backend believes Platega, never the browser or the callback body ------------------


def test_a_callback_for_a_transaction_we_never_made_credits_nothing_and_asks_nothing(client_factory, engine):
    client, platega = paid_client(client_factory)
    forged = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    assert callback(client, forged, status="CONFIRMED", amount=1, payload="max").status_code == 200
    assert not platega.requests and balance(engine) == 0


def test_the_callback_body_cannot_confirm_a_payment_platega_has_not_received(client_factory, engine):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client, "max")
    # Even with the right headers, "CONFIRMED" in the body is only a doorbell; Platega still says PENDING.
    assert callback(client, transaction_id, status="CONFIRMED", amount=9900, currency="RUB").status_code == 200
    assert len(platega.gets()) == 1 and balance(engine) == 0
    assert checkout_row(engine).status == "pending"


@pytest.mark.parametrize("tamper", [
    {"paymentDetails": {"amount": 1.0, "currency": "RUB"}},
    {"paymentDetails": {"amount": 1500.0, "currency": "USD"}},
    {"paymentDetails": {"amount": "garbage", "currency": "RUB"}},
    {"paymentDetails": "1500 RUB"},
    {"paymentDetails": None},
    {"payload": "another-checkouts-token"},
    {"id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"},
])
def test_a_confirmation_that_is_not_the_purchase_we_created_credits_nothing(client_factory, engine, tamper):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client, "start")
    platega.pay(transaction_id)
    platega.tamper = tamper
    callback(client, transaction_id)
    assert balance(engine) == 0 and checkout_row(engine).status == "mismatch"
    platega.tamper = {}
    callback(client, transaction_id)
    assert balance(engine) == 0


def test_a_cheaper_payment_cannot_buy_a_dearer_plan(client_factory, engine):
    client, platega = paid_client(client_factory)
    cheap = open_checkout(client, "start")
    dear = open_checkout(client, "max")
    # The attacker pays for Start but points the notification at the Max checkout's id.
    platega.pay(cheap)
    callback(client, dear)
    assert balance(engine) == 0 and not subscriptions_exist(engine)


def subscriptions_exist(engine) -> bool:
    with session_scope(make_session_factory(engine)) as db:
        return db.scalar(select(Subscription)) is not None


def second_user(client_factory, platega):
    from fastapi import Response
    from platega_fake import PLATEGA

    from kvist_cloud.auth import set_session

    other = client_factory(platega, **PLATEGA)
    with session_scope(other.app.state.session_factory) as db:
        user = make_user(db, "intruder", 2)
        response = Response()
        set_session(response, other.app.state.settings, user)
        other.cookies.set("kc_session", response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1])
    return other


def test_a_payment_credits_the_account_that_created_the_checkout(client_factory, engine):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client, "start")
    other = second_user(client_factory, platega)
    platega.pay(transaction_id)
    # Anyone can ring the bell (it is authenticated by the merchant's headers, not by a user) ...
    assert callback(other, transaction_id).status_code == 200
    # ... and the plan still lands on the owner, nowhere else.
    with session_scope(client.app.state.session_factory) as db:
        assert [s.user_id for s in db.scalars(select(Subscription))] == [1]


def test_one_user_cannot_read_or_sync_anothers_checkout(client_factory, engine):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client, "start")
    other = second_user(client_factory, platega)
    platega.pay(transaction_id)
    assert other.post("/api/billing/sync").json()["status"] == "none"
    assert balance(engine) == 0 and not platega.gets()


def test_checkout_requires_a_signed_in_user(client_factory):
    client, platega = paid_client(client_factory)
    client.cookies.clear()
    assert client.post("/api/billing/checkout", json={"plan": "start"}).status_code in {401, 403}
    assert client.post("/api/billing/sync").status_code in {401, 403}


def test_credentials_never_leave_in_an_error_or_a_log(client_factory, caplog):
    import httpx

    client, _ = paid_client(client_factory)
    client.app.state.http_transport = httpx.MockTransport(
        lambda request: httpx.Response(500, text=f"echo {SECRET} {MERCHANT}"))
    response = client.post("/api/billing/checkout", json={"plan": "start"})
    assert response.status_code == 502
    assert SECRET not in response.text and SECRET not in caplog.text and MERCHANT not in response.text


def test_payments_cannot_be_hammered_at_platega(client_factory, engine):
    client, platega = paid_client(client_factory)
    with session_scope(client.app.state.session_factory) as db:
        for number in range(billing.MAX_NEW_CHECKOUTS_PER_HOUR):
            db.add(BillingCheckout(user_id=1, kind="pro", amount="3900.00", currency="RUB",
                                   token=f"{number}" * 48, status="revoked"))
    assert client.post("/api/billing/checkout", json={"plan": "start"}).status_code == 429
    assert not platega.requests


def test_a_checkout_abandoned_mid_creation_does_not_block_forever(client_factory, engine):
    client, platega = paid_client(client_factory)
    with session_scope(client.app.state.session_factory) as db:
        db.add(BillingCheckout(user_id=1, kind="start", amount="1500.00", currency="RUB", payment_method=2,
                               token="t" * 48))
    assert client.post("/api/billing/checkout", json={"plan": "start"}).status_code == 409
    with session_scope(client.app.state.session_factory) as db:
        db.scalar(select(BillingCheckout)).created_at = utcnow() - billing.CHECKOUT_CREATE_TIMEOUT
    assert open_checkout(client, "start")
    with session_scope(client.app.state.session_factory) as db:
        assert db.scalar(select(func.count()).select_from(BillingCheckout)) == 1


def test_a_payment_that_no_longer_fits_is_held_for_an_operator_not_stacked(client_factory, engine):
    client, platega = paid_client(client_factory)
    first = open_checkout(client, "pro")
    second_link = first  # same link; a second payer path is opened by hand below
    pay_and_notify(client, platega, "start")
    # A pack link opened while there was no plan, paid after the plan was charged back.
    with session_scope(client.app.state.session_factory) as db:
        db.add(BillingCheckout(user_id=1, kind="hours", amount="500.00", currency="RUB", token="h" * 48,
                               transaction_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))
    assert second_link
    platega.transactions["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"] = {
        "request": {"paymentDetails": {"amount": 500.0, "currency": "RUB"}, "payload": "h" * 48, "return": "x"},
        "status": "CONFIRMED"}
    with session_scope(client.app.state.session_factory) as db:
        db.scalars(select(Subscription)).first().status = "cancelled"
    callback(client, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    with session_scope(client.app.state.session_factory) as db:
        statuses = {row.kind: row.status for row in db.scalars(select(BillingCheckout))}
    assert statuses["hours"] == "needs_review"
    assert balance(engine) == 0
