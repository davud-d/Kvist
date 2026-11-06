# -*- coding: utf-8 -*-
"""Platega checkout, callbacks (a doorbell, never a proof), the dev stub, cancel, the trial reminder."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from conftest import dev_login, make_user
from platega_fake import MERCHANT, PLATEGA, PRICES, SECRET, callback, open_checkout, paid_client, pay_and_notify
from sqlalchemy import select

from kvist_cloud import billing, ledger
from kvist_cloud.config import ConfigError, load_settings, read_env_file
from kvist_cloud.db import make_session_factory, session_scope, utcnow
from kvist_cloud.models import BillingCheckout, CheckoutStatus, Outbox, Subscription, SubscriptionStatus


def subscriptions(engine) -> list[Subscription]:
    with session_scope(make_session_factory(engine)) as db:
        rows = list(db.scalars(select(Subscription).order_by(Subscription.id)))
        db.expunge_all()
        return rows


def balance(engine) -> int:
    with session_scope(make_session_factory(engine)) as db:
        return ledger.balance_seconds(db, 1)


def checkout_row(engine) -> BillingCheckout:
    with session_scope(make_session_factory(engine)) as db:
        row = db.scalars(select(BillingCheckout).order_by(BillingCheckout.id.desc())).first()
        db.expunge(row)
        return row


# -- configuration -------------------------------------------------------------------------------


def test_billing_needs_credentials_and_every_price(env):
    with pytest.raises(ConfigError, match="PLATEGA_MERCHANT_ID"):
        load_settings({**env, "BILLING_ENABLED": "true"})
    with pytest.raises(ConfigError, match="PLATEGA_SECRET"):
        load_settings({**env, "BILLING_ENABLED": "true", "PLATEGA_MERCHANT_ID": MERCHANT})
    ready = {**env, **PLATEGA}
    with pytest.raises(ConfigError, match="PLATEGA_PRICE_HOURS"):
        load_settings({k: v for k, v in ready.items() if k != "PLATEGA_PRICE_HOURS"})
    settings = load_settings(ready)
    assert settings.platega_prices["start"] == 1500 and settings.platega_currency == "RUB"
    assert SECRET not in repr(settings) and MERCHANT not in repr(settings)


@pytest.mark.parametrize("name,value", [("PLATEGA_PRICE_START", "0"), ("PLATEGA_PRICE_START", "-5"),
                                         ("PLATEGA_PRICE_START", "12.345"), ("PLATEGA_PRICE_START", "nan"),
                                         ("PLATEGA_PAYMENT_METHODS", "7"), ("PLATEGA_PAYMENT_METHODS", "2,x"),
                                         ("PLATEGA_BASE_URL", "http://x")])
def test_nonsense_billing_settings_are_refused(env, name, value):
    with pytest.raises(ConfigError):
        load_settings({**env, **PLATEGA, name: value})


def test_env_file_is_read_and_a_real_variable_wins(tmp_path, monkeypatch, env):
    path = tmp_path / ".env"
    path.write_text("# comment\n\nPLATEGA_SECRET='from-file'\nexport PLATEGA_MERCHANT_ID=" + MERCHANT + "\n")
    assert read_env_file(path) == {"PLATEGA_SECRET": "from-file", "PLATEGA_MERCHANT_ID": MERCHANT}
    assert read_env_file(tmp_path / "missing.env") == {}
    path.write_text("not a pair\n")
    with pytest.raises(ConfigError):
        read_env_file(path)
    path.write_text("PLATEGA_SECRET=from-file\nPLATEGA_MERCHANT_ID=" + MERCHANT + "\n")
    monkeypatch.setenv("KVIST_CLOUD_ENV_FILE", str(path))
    monkeypatch.setenv("PLATEGA_SECRET", "from-process")
    for name, value in {**env, **PLATEGA}.items():
        if name not in {"PLATEGA_SECRET", "PLATEGA_MERCHANT_ID"}:
            monkeypatch.setenv(name, value)
    assert load_settings().platega_secret == "from-process"


# -- callback authentication -----------------------------------------------------------------------


@pytest.mark.parametrize("merchant,secret", [(None, None), (MERCHANT, None), (None, SECRET), (MERCHANT, "wrong"),
                                              ("wrong", SECRET), ("", ""), (MERCHANT, SECRET[:-1])])
def test_callback_without_the_exact_headers_is_refused_before_anything_else(client_factory, merchant, secret):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client)
    platega.pay(transaction_id)
    assert callback(client, transaction_id, merchant, secret).status_code == 401
    assert not platega.gets()
    assert checkout_row_status(client) == CheckoutStatus.PENDING


def checkout_row_status(client) -> str:
    with session_scope(client.app.state.session_factory) as db:
        return db.scalar(select(BillingCheckout.status))


def test_callback_is_off_while_billing_is_disabled(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    assert callback(client, "3fa85f64-5717-4562-b3fc-2c963f66afa6").status_code == 404


@pytest.mark.parametrize("body", [b"", b"[]", b"not json", b'{"id": 5}', b'{"id": "../../x"}', b'{"id": "abc"}'])
def test_malformed_callbacks_are_refused(client_factory, body):
    client, platega = paid_client(client_factory)
    response = client.post("/billing/platega/callback", content=body,
                           headers={"X-MerchantId": MERCHANT, "X-Secret": SECRET})
    assert response.status_code == 400
    assert not platega.requests


# -- checkout --------------------------------------------------------------------------------------


def test_checkout_asks_platega_for_the_servers_own_amount(client_factory, engine):
    client, platega = paid_client(client_factory)
    # Whatever the browser adds is rejected, so it cannot name an amount or a return address.
    assert client.post("/api/billing/checkout", json={"plan": "pro", "amount": 1}).status_code == 422
    transaction_id = open_checkout(client, "pro")
    body = platega.transactions[transaction_id]["request"]
    assert body["paymentDetails"] == {"amount": 3900.0, "currency": "RUB"}
    assert body["return"] == "http://127.0.0.1:8740/start" and body["paymentMethod"] == 2
    assert body["payload"] == checkout_row(engine).token and len(body["payload"]) == 48
    row = checkout_row(engine)
    assert (row.user_id, row.kind, row.amount, row.status) == (1, "pro", "3900.00", CheckoutStatus.PENDING)
    assert row.transaction_id == transaction_id


def test_the_person_picks_among_the_servers_methods_only(client_factory, engine):
    client, platega = paid_client(client_factory)
    assert client.get("/api/billing/config").json()["charge"]["methods"] == [2, 11, 12, 13]
    for method in (3, 14, 0, -1, 999):
        assert client.post("/api/billing/checkout", json={"plan": "start", "method": method}).status_code == 422
    assert client.post("/api/billing/checkout", json={"plan": "start", "method": "11"}).status_code == 422
    assert client.post("/api/billing/checkout", json={"plan": "start", "method": True}).status_code == 422
    assert not platega.requests
    cards = client.post("/api/billing/checkout", json={"plan": "start", "method": 11}).json()["redirect"]
    crypto = client.post("/api/billing/checkout", json={"plan": "start", "method": 13}).json()["redirect"]
    assert cards != crypto
    assert [t["request"]["paymentMethod"] for t in platega.transactions.values()] == [11, 13]
    assert checkout_row(engine).payment_method == 13


def test_methods_come_from_the_settings(client_factory):
    client, _ = paid_client(client_factory, PLATEGA_PAYMENT_METHODS="3,2")
    assert client.get("/api/billing/config").json()["charge"]["methods"] == [3, 2]
    assert client.post("/api/billing/checkout", json={"plan": "start", "method": 11}).status_code == 422


def test_the_same_open_payment_link_is_handed_out_again(client_factory):
    client, platega = paid_client(client_factory)
    assert open_checkout(client) == open_checkout(client)
    assert len(platega.transactions) == 1


def test_trial_is_free_once_and_never_touches_platega(client_factory, engine):
    client, platega = paid_client(client_factory)
    assert client.post("/api/billing/checkout", json={"plan": "trial"}).json() == {"started": "trial"}
    assert [(s.plan, s.status) for s in subscriptions(engine)] == [("trial", "trialing")]
    assert balance(engine) == 5 * 3600
    assert client.post("/api/billing/checkout", json={"plan": "trial"}).status_code == 403
    with session_scope(client.app.state.session_factory) as db:
        db.scalar(select(Subscription)).status = "cancelled"
    assert client.post("/api/billing/checkout", json={"plan": "trial"}).status_code == 403
    assert not platega.requests


def unreachable(client) -> None:
    import httpx

    def down(request):
        raise httpx.ConnectError("down")

    client.app.state.http_transport = httpx.MockTransport(down)


def test_platega_being_down_leaves_nothing_behind(client_factory):
    client, _ = paid_client(client_factory)
    unreachable(client)
    assert client.post("/api/billing/checkout", json={"plan": "start"}).status_code == 502
    with session_scope(client.app.state.session_factory) as db:
        assert db.scalar(select(BillingCheckout)) is None


GOOD_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"


@pytest.mark.parametrize("answer", [{"transactionId": "nope", "redirect": "https://pay.platega.io/x"},
                                    {"transactionId": GOOD_ID, "redirect": "javascript:x"},
                                    {"transactionId": GOOD_ID, "redirect": "http://pay.platega.io/x"}, {}])
def test_an_unexpected_platega_answer_is_refused_and_forgotten(client_factory, engine, answer):
    import httpx

    client, _ = paid_client(client_factory)
    client.app.state.http_transport = httpx.MockTransport(lambda request: httpx.Response(200, json=answer))
    assert client.post("/api/billing/checkout", json={"plan": "start"}).status_code == 502
    with session_scope(client.app.state.session_factory) as db:
        assert db.scalar(select(BillingCheckout)) is None


# -- fulfilment ------------------------------------------------------------------------------------


def test_confirmed_payment_starts_the_plan_and_its_hours(client_factory, engine):
    client, platega = paid_client(client_factory)
    pay_and_notify(client, platega, "pro")
    (row,) = subscriptions(engine)
    assert (row.plan, row.status, row.user_id) == ("pro", "active", 1)
    assert (row.current_period_end - row.current_period_start) == timedelta(days=30)
    assert balance(engine) == 150 * 3600
    assert checkout_row(engine).status == CheckoutStatus.PAID
    view = client.get("/api/billing/subscription").json()
    assert (view["plan"], view["balance_seconds"]) == ("pro", 150 * 3600)


def test_repeated_and_reordered_callbacks_change_nothing(client_factory, engine):
    client, platega = paid_client(client_factory)
    transaction_id = pay_and_notify(client, platega)
    for status in ("CONFIRMED", "CANCELED", "DISPUTED", "CONFIRMED", "PARTIALLY_REFUNDED"):
        assert callback(client, transaction_id, status=status).status_code == 200
    assert len(subscriptions(engine)) == 1 and balance(engine) == 50 * 3600


def test_hour_pack_adds_hours_to_the_current_period(client_factory, engine):
    client, platega = paid_client(client_factory)
    pay_and_notify(client, platega, "start")
    pay_and_notify(client, platega, "hours")
    assert balance(engine) == 60 * 3600
    assert client.get("/api/billing/subscription").json()["purchased_seconds"] == 10 * 3600


def test_hour_pack_needs_a_plan(client_factory):
    client, platega = paid_client(client_factory)
    assert client.post("/api/billing/checkout", json={"plan": "hours"}).status_code == 402
    assert not platega.requests


def test_paying_replaces_the_trial(client_factory, engine):
    client, platega = paid_client(client_factory)
    client.post("/api/billing/checkout", json={"plan": "trial"})
    pay_and_notify(client, platega, "start")
    rows = subscriptions(engine)
    assert [(s.plan, s.status) for s in rows] == [("trial", "cancelled"), ("start", "active")]
    assert balance(engine) == 50 * 3600


def test_a_plan_cannot_be_bought_twice_but_can_be_renewed_near_its_end(client_factory, engine):
    client, platega = paid_client(client_factory)
    pay_and_notify(client, platega, "start")
    assert client.post("/api/billing/checkout", json={"plan": "pro"}).status_code == 409
    with session_scope(client.app.state.session_factory) as db:
        first = db.scalar(select(Subscription))
        first.current_period_end = utcnow() + timedelta(days=2)
    assert client.get("/api/billing/subscription").json()["can_renew"] is True
    pay_and_notify(client, platega, "pro")
    first, second = subscriptions(engine)
    assert second.plan == "pro" and second.current_period_start == first.current_period_end
    # The next month's hours are not spendable yet, and the current month's are untouched.
    assert balance(engine) == 50 * 3600
    # And it cannot be queued twice.
    assert client.post("/api/billing/checkout", json={"plan": "max"}).status_code == 409


def test_a_missed_callback_is_caught_by_the_page_asking_again(client_factory, engine):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client, "start")
    assert client.get("/api/billing/eligibility").json()["payment_pending"] is True
    assert client.post("/api/billing/sync").json()["status"] == "none"
    platega.pay(transaction_id)
    assert client.post("/api/billing/sync").json()["plan"] == "start"
    assert client.get("/api/billing/eligibility").json()["payment_pending"] is False
    assert balance(engine) == 50 * 3600


def test_the_background_pass_catches_payments_too(client_factory, engine):
    client, platega = paid_client(client_factory)
    platega.pay(open_checkout(client, "max"))
    client.portal.call(billing.sync_pending, client.app)
    assert balance(engine) == 450 * 3600


def test_sync_survives_platega_being_unreachable(client_factory):
    client, _ = paid_client(client_factory)
    open_checkout(client, "start")
    unreachable(client)
    assert client.post("/api/billing/sync").json()["status"] == "none"
    assert checkout_row_status(client) == CheckoutStatus.PENDING


def test_a_platega_outage_during_a_callback_asks_for_a_retry(client_factory):
    import httpx

    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client)
    client.app.state.http_transport = httpx.MockTransport(lambda request: httpx.Response(503, text="boom"))
    assert callback(client, transaction_id).status_code == 502
    client.app.state.http_transport = httpx.MockTransport(platega)
    platega.pay(transaction_id)
    assert callback(client, transaction_id).status_code == 200


# -- reversals -------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["CHARGEBACKED", "CANCELED"])
def test_a_charged_back_plan_stops_working_and_cannot_come_back(client_factory, engine, status):
    client, platega = paid_client(client_factory)
    transaction_id = pay_and_notify(client, platega, "max")
    platega.pay(transaction_id, status)
    callback(client, transaction_id, status=status)
    assert balance(engine) == 0 and client.get("/api/billing/subscription").json()["status"] == "none"
    assert checkout_row(engine).status == CheckoutStatus.REVOKED
    platega.pay(transaction_id)
    callback(client, transaction_id)
    assert balance(engine) == 0


def test_a_charged_back_hour_pack_takes_its_hours_once(client_factory, engine):
    client, platega = paid_client(client_factory)
    pay_and_notify(client, platega, "start")
    transaction_id = pay_and_notify(client, platega, "hours")
    platega.pay(transaction_id, "CHARGEBACKED")
    for _ in range(3):
        callback(client, transaction_id, status="CHARGEBACKED")
    assert balance(engine) == 50 * 3600


# -- dev stub, cancel, reminders --------------------------------------------------------------------


def test_dev_stub_starts_the_trial_without_platega(client_factory, engine):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    assert client.get("/api/billing/config").json()["enabled"] is False
    view = client.post("/api/billing/dev/skip-checkout").json()
    assert (view["status"], view["plan"], view["balance_seconds"], view["price_after_trial_usd"]) == (
        "trialing", "trial", 5 * 3600, 15)
    assert view["purchased_seconds"] == 0
    client.post("/api/billing/dev/skip-checkout")
    assert len(subscriptions(engine)) == 1
    bought = client.post("/api/billing/dev/buy-hours").json()
    assert (bought["balance_seconds"], bought["purchased_seconds"]) == (15 * 3600, 10 * 3600)


def test_dev_stub_is_off_when_billing_is_on(client_factory):
    client = client_factory(**PLATEGA)
    dev_login(client)
    assert client.post("/api/billing/dev/skip-checkout").status_code == 404


def test_billing_off_means_no_checkout(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    assert client.post("/api/billing/checkout", json={"plan": "start"}).status_code == 503


def test_config_shows_the_charged_prices_but_no_credentials(client_factory):
    client, _ = paid_client(client_factory)
    config = client.get("/api/billing/config")
    assert config.json()["charge"] == {"currency": "RUB", "methods": [2, 11, 12, 13], "prices": PRICES}
    assert SECRET not in config.text and MERCHANT not in config.text


def test_cancel_is_one_call_and_access_lasts_the_period(client_factory, engine):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    client.post("/api/billing/dev/skip-checkout")
    view = client.post("/api/billing/cancel").json()
    assert (view["status"], view["cancel_at_period_end"]) == ("trialing", True)
    # A cancelled trial ends at its end, and is never turned into a paid month.
    with session_scope(make_session_factory(engine)) as db:
        billing.roll_dev_subscriptions(db, now=utcnow() + timedelta(days=8))
    assert subscriptions(engine)[0].status == SubscriptionStatus.CANCELLED


def test_cancel_with_billing_on_does_not_call_platega(client_factory):
    client, platega = paid_client(client_factory)
    pay_and_notify(client, platega)
    calls = len(platega.requests)
    assert client.post("/api/billing/cancel").json()["cancel_at_period_end"] is True
    assert len(platega.requests) == calls


def test_dev_trial_turns_into_start(db, monkeypatch):
    user = make_user(db)
    billing.create_trial(db, user)
    later = utcnow() + timedelta(days=7, minutes=1)
    billing.roll_dev_subscriptions(db, now=later)
    monkeypatch.setattr(ledger, "utcnow", lambda: later)
    row = ledger.current_subscription(db, user.id)
    assert (row.plan, row.status) == ("start", "active")
    assert ledger.balance_seconds(db, user.id) == 50 * 3600


def test_trial_reminder_two_days_before_promises_no_automatic_charge(db):
    user = make_user(db)
    user.locale = "ru"
    trial = billing.create_trial(db, user)
    assert billing.queue_trial_reminders(db) == 0
    later = trial.trial_ends_at - timedelta(days=2) + timedelta(minutes=1)
    assert billing.subscription_view(db, user, now=later)["trial_reminder"] is True
    assert billing.queue_trial_reminders(db, now=later) == 1
    assert billing.queue_trial_reminders(db, now=later) == 0
    mail = db.scalars(select(Outbox)).one()
    assert mail.subject == "Через 2 дня закончится пробный период"
    assert "сама ничего не спишет" in mail.body


def test_no_reminder_after_cancel(db):
    user = make_user(db)
    trial = billing.create_trial(db, user)
    trial.cancel_at_period_end = True
    later = trial.trial_ends_at - timedelta(days=1)
    assert billing.queue_trial_reminders(db, now=later) == 0
    assert billing.subscription_view(db, user, now=later)["trial_reminder"] is False


def test_no_subscription_view(db):
    user = make_user(db)
    assert billing.subscription_view(db, user) == {"status": "none", "balance_seconds": 0}


def test_request_body_reaches_platega_as_json(client_factory):
    client, platega = paid_client(client_factory)
    pay_and_notify(client, platega, "start")
    open_checkout(client, "hours")
    request = [r for r in platega.requests if r.method == "POST"][-1]
    assert json.loads(request.content)["paymentDetails"]["amount"] == 500.0
