# -*- coding: utf-8 -*-
"""Payments under attack: forged and replayed callbacks (CWE-345/294), races that would credit twice (CWE-362),
hostile payloads (CWE-20/674/400), money arithmetic (CWE-682/190), trial abuse (CWE-799) and paid-for hours.

test_billing.py and test_security.py prove the contract; this file hammers its edges. Nothing here reaches the
network: Platega is the fake from platega_fake.py.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from conftest import make_user
from platega_fake import MERCHANT, PLATEGA, SECRET, callback, open_checkout, paid_client
from sqlalchemy import func, select

from kvist_cloud import auth, billing, ledger, plans
from kvist_cloud.db import session_scope, utcnow
from kvist_cloud.models import (
    BillingCheckout,
    CheckoutStatus,
    LedgerEntry,
    Subscription,
    SubscriptionStatus,
    User,
)

HEADERS = {"X-MerchantId": MERCHANT, "X-Secret": SECRET, "Content-Type": "application/json"}


def rows(client, model):
    with session_scope(client.app.state.session_factory) as db:
        found = list(db.scalars(select(model).order_by(model.id)))
        db.expunge_all()
        return found


def balance(client, user_id=1) -> int:
    with session_scope(client.app.state.session_factory) as db:
        return ledger.balance_seconds(db, user_id)


LOOP_BLOCK = (
    "FINDING (CWE-833/400): `checkout` is an `async def` that waits for the database lock on the event loop, "
    "while the request that holds the lock (it raised after lock_account) can only release it through that "
    "same loop. A double click freezes the whole server for the SQLite busy timeout (30 s in production) and "
    "most requests end in OperationalError. Fix: make these endpoints `def` (threadpool)."
)


def short_lock_wait(client, millis: int = 2000) -> None:
    """A short wait for the database lock keeps the test quick; the stall itself is the finding."""
    from sqlalchemy import event

    engine = client.app.state.engine
    event.listen(engine, "connect", lambda connection, _record: connection.execute(f"PRAGMA busy_timeout={millis}"))
    engine.dispose()


def hammer(client, path: str, body: dict, workers: int = 8):
    """The status codes of ``workers`` simultaneous posts, with any escaping exception named, and the seconds taken."""
    import time

    started = time.monotonic()

    def click(_):
        try:
            return client.post(path, json=body).status_code
        except Exception as error:  # noqa: BLE001 - whatever escapes the app is a 500 in production
            return type(error).__name__

    with ThreadPoolExecutor(max_workers=workers) as pool:
        codes = list(pool.map(click, range(workers)))
    return codes, time.monotonic() - started


def post_callback(client, content: bytes, headers=None):
    return client.post("/billing/platega/callback", content=content, headers=headers or HEADERS)


# -- hostile callback bodies (CWE-20, CWE-674, CWE-400) --------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="FINDING: a deeply nested JSON body raises RecursionError inside json.loads, "
    "which the callback does not catch: answered 500 instead of 400 (CWE-674).",
)
def test_a_deeply_nested_callback_body_is_a_400_not_a_crash(client_factory):
    client, platega = paid_client(client_factory)
    nested = b"[" * 30_000 + b"]" * 30_000  # 60 KB: under the size limit, far over the recursion limit
    assert post_callback(client, nested).status_code == 400
    assert not platega.requests


def test_the_size_limit_is_exact(client_factory):
    client, platega = paid_client(client_factory)
    padding = b" " * (billing.CALLBACK_MAX_BYTES + 1)
    assert post_callback(client, b'{"id": "x"}' + padding).status_code == 413
    assert not platega.requests


@pytest.mark.parametrize(
    "body",
    [
        b'{"id": "3fa85f64-5717-4562-b3fc-2c963f66afa6\\n"}',
        b'{"id": " 3fa85f64-5717-4562-b3fc-2c963f66afa6"}',
        b'{"id": "{3fa85f64-5717-4562-b3fc-2c963f66afa6}"}',
        b'{"id": "3fa85f64-5717-4562-b3fc-2c963f66afa6/../../x"}',
        b'{"id": "3fa85f64-5717-4562-b3fc-2c963f66afa6?x=1"}',
        b'{"id": ["3fa85f64-5717-4562-b3fc-2c963f66afa6"]}',
        b'{"id": {"a": "3fa85f64-5717-4562-b3fc-2c963f66afa6"}}',
        b'{"id": null}',
        b"{}",
        b"null",
        b'"3fa85f64-5717-4562-b3fc-2c963f66afa6"',
        b"\xff\xfe\x00",
        b'{"id": "\xd9\xa3fa85f64-5717-4562-b3fc-2c963f66afa6"}',
    ],
)
def test_only_a_plain_uuid_ever_becomes_part_of_a_platega_request(client_factory, body):
    client, platega = paid_client(client_factory)
    assert post_callback(client, body).status_code == 400
    assert not platega.requests, "a hostile id reached the outbound request path (CWE-918)"


def test_duplicate_keys_and_extra_fields_in_the_body_change_nothing(client_factory):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client)
    platega.pay(transaction_id)
    other = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    body = json.dumps({"id": other, "amount": 1, "status": "CONFIRMED"})[:-1] + f', "id": "{transaction_id}"' + "}"
    assert post_callback(client, body.encode()).status_code == 200
    # Python keeps the last duplicate: exactly the transaction we meant, and it was verified with Platega.
    assert len(platega.gets()) == 1 and platega.gets()[0].url.path.endswith(transaction_id)


@pytest.mark.parametrize("header", ["X-Secret", "X-MerchantId"])
@pytest.mark.parametrize(
    "value",
    [
        "é".encode("latin-1"),
        "ÿÿ".encode("latin-1"),
        b"\x01\x02",
        b" " + SECRET.encode(),
        SECRET.encode() + b"\x00",
        SECRET.upper().encode(),
        b"A" * 100_000,
    ],
)
def test_odd_credential_bytes_are_a_clean_401(client_factory, header, value):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client)
    headers = {**HEADERS, header: value}
    try:
        response = post_callback(client, json.dumps({"id": transaction_id}).encode(), headers)
    except Exception as error:  # noqa: BLE001 - the client may refuse to send it; that is also safe
        pytest.skip(f"client refused to send: {type(error).__name__}")
    assert response.status_code in {400, 401, 431}, response.status_code
    assert not platega.gets()


def test_both_credentials_must_be_right_not_either(client_factory):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client)
    for headers in (
        {"X-MerchantId": MERCHANT},
        {"X-Secret": SECRET},
        {"X-MerchantId": SECRET, "X-Secret": MERCHANT},
        {"X-MerchantId": MERCHANT, "X-Secret": MERCHANT},
        {"X-MerchantId": SECRET, "X-Secret": SECRET},
    ):
        response = post_callback(client, json.dumps({"id": transaction_id}).encode(), headers)
        assert response.status_code == 401, headers
    assert not platega.gets()


def test_credentials_in_the_query_string_or_cookies_are_not_credentials(client_factory):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client)
    body = json.dumps({"id": transaction_id}).encode()
    url = f"/billing/platega/callback?X-MerchantId={MERCHANT}&X-Secret={SECRET}"
    assert client.post(url, content=body, headers={"Content-Type": "application/json"}).status_code == 401
    client.cookies.set("X-Secret", SECRET)
    assert client.post("/billing/platega/callback", content=body).status_code == 401
    assert not platega.gets()


@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE"])
def test_the_callback_answers_only_post(client_factory, method):
    client, _ = paid_client(client_factory)
    assert client.request(method, "/billing/platega/callback", headers=HEADERS).status_code in {404, 405}


# -- replay and ordering (CWE-294) -----------------------------------------------------------------------


def test_one_hundred_callbacks_for_one_payment_credit_it_once(client_factory):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client, "pro")
    platega.pay(transaction_id)
    with ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(lambda _: callback(client, transaction_id).status_code, range(100)))
    assert set(codes) == {200}
    assert len([s for s in rows(client, Subscription)]) == 1
    assert balance(client) == plans.get_plan("pro").seconds
    assert [c.status for c in rows(client, BillingCheckout)] == [CheckoutStatus.PAID]


def test_two_different_plans_confirmed_at_the_same_moment_do_not_stack(client_factory):
    client, platega = paid_client(client_factory)
    first, second = open_checkout(client, "start"), open_checkout(client, "pro")
    platega.pay(first)
    platega.pay(second)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda tx: callback(client, tx), [first, second] * 10))
    statuses = sorted(c.status for c in rows(client, BillingCheckout))
    assert statuses == sorted([CheckoutStatus.PAID, CheckoutStatus.NEEDS_REVIEW]), statuses
    paid_subscriptions = [s for s in rows(client, Subscription) if s.status == SubscriptionStatus.ACTIVE]
    assert len(paid_subscriptions) == 1


def test_confirm_cancel_confirm_leaves_the_account_revoked_and_the_hours_reversed_once(client_factory):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client, "start")
    for state in ("CONFIRMED", "CANCELED", "CONFIRMED", "CHARGEBACKED", "CONFIRMED", "CANCELED"):
        platega.pay(transaction_id, state)
        assert callback(client, transaction_id).status_code == 200
    assert rows(client, BillingCheckout)[0].status == CheckoutStatus.REVOKED
    assert [s.status for s in rows(client, Subscription)] == [SubscriptionStatus.CANCELLED]
    # A plan's hours die with its subscription; nothing may be left that the account could still spend.
    assert balance(client) == 0 and client.get("/api/billing/subscription").json()["status"] == "none"
    with session_scope(client.app.state.session_factory) as db:
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.kind == "reversal")) == 0


@pytest.mark.parametrize(
    "status",
    [
        "PENDING",
        "CANCELED",
        "FAILED",
        "EXPIRED",
        "confirmed",
        "Confirmed",
        "CONFIRMED ",
        " CONFIRMED",
        "",
        None,
        1,
        True,
        0,
        "REFUNDED",
        "CONFIRMED\n",
    ],
)
def test_only_the_exact_word_confirmed_pays(client_factory, status):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client)
    platega.pay(transaction_id, status)
    assert callback(client, transaction_id).status_code == 200
    assert balance(client) == 0 and not rows(client, Subscription)


@pytest.mark.parametrize(
    "status",
    [
        pytest.param(
            ["CONFIRMED"],
            marks=pytest.mark.xfail(
                strict=True,
                reason="FINDING (low, CWE-754): a list as Platega's "
                "status raises TypeError (unhashable) in apply_remote: 500, Platega retries forever.",
            ),
        ),
        pytest.param(
            {"a": "CONFIRMED"}, marks=pytest.mark.xfail(strict=True, reason="FINDING (low, CWE-754): same as above.")
        ),
    ],
)
def test_a_structured_status_from_platega_is_not_a_crash(client_factory, status):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client)
    platega.pay(transaction_id, status)
    assert callback(client, transaction_id).status_code < 500
    assert balance(client) == 0


# -- money (CWE-682, CWE-190) ----------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="FINDING (low, CWE-754): amount '1e999' is finite but too large to quantize: "
    "InvalidOperation escapes _money, 500.",
)
def test_a_gigantic_amount_is_a_mismatch_not_a_crash(client_factory):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client, "start")
    platega.pay(transaction_id)
    platega.tamper = {"paymentDetails": {"amount": "1e999", "currency": "RUB"}}
    assert callback(client, transaction_id).status_code == 200
    assert balance(client) == 0


@pytest.mark.parametrize(
    "amount",
    [
        1499.99,
        1500.01,
        0,
        -1500,
        1,
        15000,
        "1500.5",
        "NaN",
        "Infinity",
        "-Infinity",
        True,
        False,
        None,
        [],
        {},
        "",
        "1,500.00",
        "1 500",
    ],
)
def test_any_amount_but_the_one_we_asked_for_is_a_mismatch(client_factory, amount):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client, "start")
    platega.pay(transaction_id)
    platega.tamper = {"paymentDetails": {"amount": amount, "currency": "RUB"}}
    assert callback(client, transaction_id).status_code in {200, 502}
    assert balance(client) == 0 and not rows(client, Subscription)


@pytest.mark.parametrize("currency", ["USD", "EUR", "rub", "", None, 643, ["RUB"], "RUB "])
def test_the_currency_must_be_ours_exactly(client_factory, currency):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client, "start")
    platega.pay(transaction_id)
    platega.tamper = {"paymentDetails": {"amount": 1500.0, "currency": currency}}
    callback(client, transaction_id)
    assert not rows(client, Subscription) or currency == "rub"  # case is folded on purpose, nothing else is


@pytest.mark.parametrize(
    "raw,ok",
    [
        ("1500", True),
        ("1500.0", True),
        ("1500.00", True),
        (1500, True),
        (1500.0, True),
        ("1.5e3", True),
        ("NaN", False),
        ("Infinity", False),
        ("abc", False),
        (None, False),
    ],
)
def test_money_parsing_is_finite_and_exact(raw, ok):
    assert (billing._money(raw) is not None) is ok
    if ok:
        assert str(billing._money(raw)) == "1500.00"


def test_the_charged_price_is_the_servers_even_when_the_catalogue_changes(client_factory):
    client, platega = paid_client(client_factory, PLATEGA_PRICE_START="1234.50")
    transaction_id = open_checkout(client, "start")
    body = platega.transactions[transaction_id]["request"]
    assert body["paymentDetails"]["amount"] == 1234.5
    assert rows(client, BillingCheckout)[0].amount == "1234.50"


# -- hour packs ------------------------------------------------------------------------------------------


def test_each_paid_hour_pack_adds_exactly_its_hours_once(client_factory):
    client, platega = paid_client(client_factory)
    open_checkout_paid = [open_checkout(client, "start")]
    platega.pay(open_checkout_paid[0])
    callback(client, open_checkout_paid[0])
    base = balance(client)
    for _ in range(3):
        transaction_id = open_checkout(client, "hours")
        platega.pay(transaction_id)
        for _ in range(4):
            callback(client, transaction_id)
        # A fresh link is needed for the next pack: the first is now paid.
    assert balance(client) == base + 3 * plans.HOUR_PACK_HOURS * 3600


def test_an_hour_pack_cannot_be_bought_without_a_plan_or_on_an_unmetered_one_or_after_it_ended(client_factory):
    client, platega = paid_client(client_factory)
    assert client.post("/api/billing/checkout", json={"plan": "hours"}).status_code == 402
    transaction_id = open_checkout(client, "remote")
    platega.pay(transaction_id)
    callback(client, transaction_id)
    assert client.post("/api/billing/checkout", json={"plan": "hours"}).status_code == 409
    with session_scope(client.app.state.session_factory) as db:
        for row in db.scalars(select(Subscription)):
            row.current_period_end = utcnow() - timedelta(seconds=1)
    assert client.post("/api/billing/checkout", json={"plan": "hours"}).status_code == 402


def test_an_hour_pack_paid_after_the_plan_ended_is_held_not_lost_and_not_credited(client_factory):
    client, platega = paid_client(client_factory)
    plan_tx = open_checkout(client, "start")
    platega.pay(plan_tx)
    callback(client, plan_tx)
    pack_tx = open_checkout(client, "hours")
    with session_scope(client.app.state.session_factory) as db:
        for row in db.scalars(select(Subscription)):
            row.current_period_end = utcnow() - timedelta(seconds=1)
    platega.pay(pack_tx)
    callback(client, pack_tx)
    pack = [c for c in rows(client, BillingCheckout) if c.kind == "hours"][0]
    assert pack.status == CheckoutStatus.NEEDS_REVIEW
    assert balance(client) == 0


def test_a_charged_back_pack_whose_hours_were_already_spent_is_not_a_crash(client_factory):
    client, platega = paid_client(client_factory)
    plan_tx = open_checkout(client, "start")
    platega.pay(plan_tx)
    callback(client, plan_tx)
    pack_tx = open_checkout(client, "hours")
    platega.pay(pack_tx)
    callback(client, pack_tx)
    with session_scope(client.app.state.session_factory) as db:
        sub = db.scalar(select(Subscription))
        db.connection().exec_driver_sql("DROP TRIGGER IF EXISTS ledger_no_insert_guard")  # no-op if absent
        spent = LedgerEntry(
            user_id=1,
            period_start=sub.current_period_start,
            kind="consumption",
            seconds=-(plans.get_plan("start").seconds + plans.HOUR_PACK_HOURS * 3600),
            reason="spent",
        )
        db.add(spent)
    platega.pay(pack_tx, "CHARGEBACKED")
    assert callback(client, pack_tx).status_code == 200
    view = client.get("/api/billing/subscription")
    assert view.status_code == 200 and view.json()["balance_minutes"] >= 0


# -- the request side (CWE-20, CWE-639) ------------------------------------------------------------------


@pytest.mark.parametrize("method", [True, False, 2.0, "2", -1, 0, 99, 10**30, None.__class__, []])
def test_payment_method_must_be_one_of_the_servers_integers(client_factory, method):
    client, platega = paid_client(client_factory)
    body = {"plan": "start", "method": method if method is not None.__class__ else "x"}
    response = client.post("/api/billing/checkout", json=body)
    assert response.status_code == 422 and not platega.requests


@pytest.mark.parametrize(
    "plan", ["", "TRIAL", "free", "hours ", None, 1, ["start"], "start; DROP TABLE users", "../start"]
)
def test_unknown_plans_never_reach_platega(client_factory, plan):
    client, platega = paid_client(client_factory)
    assert client.post("/api/billing/checkout", json={"plan": plan}).status_code == 422
    assert not platega.requests and not rows(client, BillingCheckout)


def test_the_checkout_body_cannot_carry_an_amount_a_user_or_a_redirect(client_factory):
    client, platega = paid_client(client_factory)
    for extra in (
        {"amount": 1},
        {"price": 1},
        {"user_id": 2},
        {"return": "https://evil.example"},
        {"currency": "USD"},
        {"status": "paid"},
        {"token": "x"},
        {"transaction_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"},
    ):
        assert client.post("/api/billing/checkout", json={"plan": "start", **extra}).status_code == 422, extra
    assert not platega.requests


@pytest.mark.parametrize(
    "answer",
    [
        {"transactionId": "not-a-uuid", "redirect": "https://pay.example/x"},
        {"transactionId": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "redirect": "http://pay.example"},
        {"transactionId": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "redirect": "javascript:alert(1)"},
        {"transactionId": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "redirect": "//evil.example"},
        {"transactionId": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "redirect": ""},
        {"transactionId": "3fa85f64-5717-4562-b3fc-2c963f66afa6"},
        {"redirect": "https://x"},
        [],
        "text",
        None,
    ],
)
def test_a_strange_answer_from_platega_is_refused_and_leaves_no_checkout(client_factory, answer):
    import httpx

    def handler(request):
        return httpx.Response(200, json=answer)

    from conftest import dev_login

    client = client_factory(handler, **PLATEGA)
    dev_login(client)
    with session_scope(client.app.state.session_factory) as db:
        db.scalar(select(User)).github_created_at = utcnow() - timedelta(days=400)
    assert client.post("/api/billing/checkout", json={"plan": "start"}).status_code == 502
    assert not rows(client, BillingCheckout)


@pytest.mark.xfail(strict=True, reason=LOOP_BLOCK)
def test_parallel_checkout_clicks_neither_freeze_the_server_nor_fail(client_factory):
    client, platega = paid_client(client_factory)
    short_lock_wait(client)
    codes, seconds = hammer(client, "/api/billing/checkout", {"plan": "pro"})
    assert set(codes) <= {200, 409}, codes
    assert seconds < 5, "the server was stalled by parallel checkouts"
    assert len(rows(client, BillingCheckout)) == 1
    assert len([r for r in platega.requests if r.method == "POST"]) == 1


def test_one_sync_call_asks_platega_about_a_bounded_number_of_payments(client_factory):
    client, platega = paid_client(client_factory)
    with session_scope(client.app.state.session_factory) as db:
        for index in range(40):
            db.add(
                BillingCheckout(
                    user_id=1,
                    kind="start",
                    amount="1500.00",
                    currency="RUB",
                    payment_method=2,
                    token=f"t{index}",
                    transaction_id=f"3fa85f64-5717-4562-b3fc-2c963f66{index:04d}",
                    pay_url="https://pay.example/x",
                )
            )
    assert client.post("/api/billing/sync").status_code == 200
    assert len(platega.gets()) <= billing.SYNC_MAX_COUNT


# -- the trial (CWE-799, CWE-837) ------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=LOOP_BLOCK)
def test_parallel_trial_requests_start_one_trial_and_never_stall_the_server(client_factory):
    client, platega = paid_client(client_factory)
    short_lock_wait(client)
    codes, seconds = hammer(client, "/api/billing/checkout", {"plan": "trial"})
    assert codes.count(200) == 1 and set(codes) <= {200, 403, 409}, codes
    assert seconds < 5
    assert len(rows(client, Subscription)) == 1 and balance(client) == plans.TRIAL.seconds
    assert not platega.requests, "a trial must never involve the payment provider"


def test_a_double_click_on_the_trial_button_starts_one_trial(client_factory):
    client, platega = paid_client(client_factory)
    assert client.post("/api/billing/checkout", json={"plan": "trial"}).status_code == 200
    assert client.post("/api/billing/checkout", json={"plan": "trial"}).status_code == 403
    assert len(rows(client, Subscription)) == 1 and balance(client) == plans.TRIAL.seconds
    assert not platega.requests


def test_cancelling_and_waiting_does_not_bring_the_trial_back(client_factory):
    client, _ = paid_client(client_factory)
    assert client.post("/api/billing/checkout", json={"plan": "trial"}).status_code == 200
    assert client.post("/api/billing/cancel").status_code == 200
    with session_scope(client.app.state.session_factory) as db:
        for row in db.scalars(select(Subscription)):
            row.current_period_end = utcnow() - timedelta(seconds=1)
    response = client.post("/api/billing/checkout", json={"plan": "trial"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "trial_used"
    assert client.get("/api/billing/eligibility").json()["trial_eligible"] is False


@pytest.mark.parametrize(
    "created_at",
    [None, 12345, ["2020-01-01T00:00:00Z"], {"a": 1}, "garbage", "2020-01-01T00:00:00", "9999-01-01T00:00:00Z", True],
)
def test_a_github_profile_with_a_strange_creation_date_never_earns_a_trial(client_factory, created_at):
    client = client_factory(AUTH_DEV_LOGIN="true")
    with session_scope(client.app.state.session_factory) as db:
        user = auth._upsert_user(db, {"id": 42, "login": "someone", "created_at": created_at}, "en")
        assert (
            user.github_created_at is None
            or user.github_created_at > utcnow() - timedelta(days=30)
            or (user.github_created_at.year >= 9999)
        )
        assert not billing.trial_eligible(user)


def test_an_old_github_account_gets_the_trial_a_new_one_does_not():
    now = utcnow()
    old, new = User(github_id=1, github_login="a"), User(github_id=2, github_login="b")
    old.github_created_at, new.github_created_at = now - timedelta(days=400), now - timedelta(days=3)
    assert billing.trial_eligible(old) and not billing.trial_eligible(new)
    old.trial_used_at = now
    assert not billing.trial_eligible(old)


# -- the dev stand-in must never be reachable where money is real (CWE-489, CWE-1188) -------------------


@pytest.mark.parametrize("path", ["/api/billing/dev/skip-checkout", "/api/billing/dev/buy-hours"])
def test_dev_money_shortcuts_do_not_exist_when_billing_is_on(client_factory, path):
    client, platega = paid_client(client_factory)
    assert client.post(path).status_code == 404
    assert balance(client) == 0 and not rows(client, Subscription)


@pytest.mark.parametrize("change", [{"BILLING_ENABLED": "true"}, {"AUTH_DEV_LOGIN": "false"}])
def test_dev_billing_is_off_unless_it_is_explicitly_local(env, change):
    from kvist_cloud.config import load_settings

    local = {**env, "AUTH_DEV_LOGIN": "true"}
    assert load_settings(local).dev_billing
    assert not load_settings({**local, **PLATEGA, **change}).dev_billing


def test_dev_login_cannot_be_turned_on_for_a_public_address(env):
    from kvist_cloud.config import ConfigError, load_settings

    with pytest.raises(ConfigError):
        load_settings({**env, "PUBLIC_URL": "https://cloud.example.org", "AUTH_DEV_LOGIN": "true"})


def test_paying_for_a_stranger_is_impossible_even_with_a_stolen_transaction_id(client_factory):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client, "start")
    with session_scope(client.app.state.session_factory) as db:
        thief = make_user(db, "thief", 99)
        thief_id = thief.id
    platega.pay(transaction_id)
    callback(client, transaction_id)
    assert [s.user_id for s in rows(client, Subscription)] == [1]
    assert balance(client, thief_id) == 0
