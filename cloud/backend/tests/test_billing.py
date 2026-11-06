# -*- coding: utf-8 -*-
"""Paddle webhooks (signed, idempotent), the dev stub, cancel, the trial reminder. Paddle is never called."""
from __future__ import annotations

import json
import time
from datetime import timedelta

import httpx
import pytest
from conftest import dev_login, make_user, settle
from sqlalchemy import select

from kvist_cloud import billing, ledger
from kvist_cloud.config import ConfigError, load_settings
from kvist_cloud.db import make_session_factory, session_scope, utcnow
from kvist_cloud.models import FunnelEvent, Outbox, Subscription, SubscriptionStatus

SECRET = "pdl_ntfset_test_secret_value"
PADDLE = {"BILLING_ENABLED": "true", "PADDLE_WEBHOOK_SECRET": SECRET, "PADDLE_API_KEY": "pdl_api_key_test_value",
          "PADDLE_PRICE_START": "pri_start", "PADDLE_PRICE_PRO": "pri_pro", "PADDLE_PRICE_MAX": "pri_max",
          "AUTH_DEV_LOGIN": "true"}


def webhook(client, event_id: str, event_type: str, data: dict, secret: str = SECRET, stamp: int | None = None):
    body = json.dumps({"event_id": event_id, "event_type": event_type, "data": data}).encode()
    signature = billing.sign(secret, body, stamp or int(time.time()))
    return client.post("/billing/paddle/webhook", content=body,
                       headers={"Paddle-Signature": signature, "Content-Type": "application/json"})


def paddle_subscription(status: str, start: str, end: str, price: str = "pri_start", **extra) -> dict:
    return {"id": "sub_01", "status": status, "customer_id": "ctm_01", "custom_data": {"user_id": "1"},
            "items": [{"price": {"id": price}}],
            "current_billing_period": {"starts_at": start, "ends_at": end}, **extra}


def subscription(engine) -> Subscription:
    with session_scope(make_session_factory(engine)) as db:
        row = db.scalars(select(Subscription)).one()
        db.expunge(row)
        return row


def balance(engine) -> int:
    with session_scope(make_session_factory(engine)) as db:
        return ledger.balance_seconds(db, 1)


def test_signature_rules():
    body = b'{"a":1}'
    now = 1_800_000_000
    good = billing.sign(SECRET, body, now)
    assert billing.verify_signature(SECRET, good, body, now=now + 10)
    assert not billing.verify_signature(SECRET, good, body + b" ", now=now)
    assert not billing.verify_signature("other", good, body, now=now)
    assert not billing.verify_signature(SECRET, good, body, now=now + 3600)
    assert not billing.verify_signature(SECRET, "garbage", body, now=now)
    assert not billing.verify_signature(SECRET, "", body, now=now)


def test_billing_needs_a_webhook_secret(env):
    with pytest.raises(ConfigError):
        load_settings({**env, "BILLING_ENABLED": "true"})


def test_webhook_starts_the_trial_and_its_hours(client_factory, engine):
    client = client_factory(**PADDLE)
    dev_login(client)
    settle(client)
    response = webhook(client, "evt_1", "subscription.created",
                       paddle_subscription("trialing", "2026-11-05T00:00:00Z", "2026-11-12T00:00:00Z"))
    assert response.json() == {"ok": True}
    row = subscription(engine)
    assert (row.plan, row.status, row.paddle_subscription_id) == ("trial", "trialing", "sub_01")
    assert row.trial_ends_at.isoformat().startswith("2026-11-12")
    assert balance(engine) == 5 * 3600
    with session_scope(make_session_factory(engine)) as db:
        assert "card_added" in [row.step for row in db.scalars(select(FunnelEvent))]


def test_trial_turns_into_start_with_a_new_period(client_factory, engine):
    client = client_factory(**PADDLE)
    dev_login(client)
    webhook(client, "evt_1", "subscription.created",
            paddle_subscription("trialing", "2026-11-05T00:00:00Z", "2026-11-12T00:00:00Z"))
    webhook(client, "evt_2", "subscription.updated",
            paddle_subscription("active", "2026-11-12T00:00:00Z", "2026-12-12T00:00:00Z"))
    row = subscription(engine)
    assert (row.plan, row.status) == ("start", "active")
    # The trial's hours belong to the trial period; the new period starts with 50 h.
    assert balance(engine) == 50 * 3600


def test_a_retried_webhook_changes_nothing(client_factory, engine):
    client = client_factory(**PADDLE)
    dev_login(client)
    data = paddle_subscription("active", "2026-11-05T00:00:00Z", "2026-12-05T00:00:00Z", price="pri_pro")
    for _ in range(3):
        assert webhook(client, "evt_same", "subscription.created", data).status_code == 200
    assert balance(engine) == 150 * 3600


def test_bad_or_stale_signature_is_refused(client_factory, engine):
    client = client_factory(**PADDLE)
    dev_login(client)
    data = paddle_subscription("active", "2026-11-05T00:00:00Z", "2026-12-05T00:00:00Z")
    assert webhook(client, "evt_x", "subscription.created", data, secret="forged").status_code == 400
    assert webhook(client, "evt_y", "subscription.created", data, stamp=int(time.time()) - 3600).status_code == 400
    with session_scope(make_session_factory(engine)) as db:
        assert db.scalars(select(Subscription)).first() is None


def test_hour_pack_purchase(client_factory, engine):
    client = client_factory(**PADDLE)
    dev_login(client)
    webhook(client, "evt_1", "subscription.created",
            paddle_subscription("active", "2026-11-05T00:00:00Z", "2026-12-05T00:00:00Z"))
    webhook(client, "evt_2", "transaction.completed", {"id": "txn_1", "custom_data": {"kind": "hours", "user_id": 1}})
    assert balance(engine) == 60 * 3600


def test_webhook_is_off_while_billing_is_disabled(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    response = webhook(client, "evt_1", "subscription.created", {})
    assert response.status_code == 404


def test_dev_stub_starts_the_trial_without_paddle(client_factory, engine):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    assert client.get("/api/billing/config").json()["enabled"] is False
    view = client.post("/api/billing/dev/skip-checkout").json()
    assert (view["status"], view["plan"], view["balance_seconds"], view["price_after_trial_usd"]) == (
        "trialing", "trial", 5 * 3600, 15)
    assert view["purchased_seconds"] == 0
    client.post("/api/billing/dev/skip-checkout")
    with session_scope(make_session_factory(engine)) as db:
        assert len(list(db.scalars(select(Subscription)))) == 1
    bought = client.post("/api/billing/dev/buy-hours").json()
    assert (bought["balance_seconds"], bought["purchased_seconds"]) == (15 * 3600, 10 * 3600)


def test_dev_stub_is_off_when_billing_is_on(client_factory):
    client = client_factory(**PADDLE)
    dev_login(client)
    assert client.post("/api/billing/dev/skip-checkout").status_code == 404


def test_cancel_is_one_call_and_access_lasts_the_period(client_factory, engine):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    client.post("/api/billing/dev/skip-checkout")
    view = client.post("/api/billing/cancel").json()
    assert (view["status"], view["cancel_at_period_end"]) == ("trialing", True)
    # A cancelled trial ends at its end, and is never turned into a paid month.
    with session_scope(make_session_factory(engine)) as db:
        billing.roll_dev_subscriptions(db, now=utcnow() + timedelta(days=8))
    assert subscription(engine).status == SubscriptionStatus.CANCELLED


def test_cancel_with_paddle_asks_paddle(client_factory, engine):
    seen: list[httpx.Request] = []

    def paddle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {}})

    client = client_factory(paddle, **PADDLE)
    dev_login(client)
    webhook(client, "evt_1", "subscription.created",
            paddle_subscription("trialing", "2026-11-05T00:00:00Z", "2026-11-12T00:00:00Z"))
    assert client.post("/api/billing/cancel").json()["cancel_at_period_end"] is True
    assert str(seen[0].url) == "https://sandbox-api.paddle.com/subscriptions/sub_01/cancel"
    assert json.loads(seen[0].content) == {"effective_from": "next_billing_period"}


def test_dev_trial_turns_into_start(db):
    user = make_user(db)
    billing.create_trial(db, user)
    billing.roll_dev_subscriptions(db, now=utcnow() + timedelta(days=7, minutes=1))
    row = ledger.current_subscription(db, user.id)
    assert (row.plan, row.status) == ("start", "active")
    assert ledger.balance_seconds(db, user.id) == 50 * 3600


def test_trial_reminder_two_days_before(db):
    user = make_user(db)
    user.locale = "ru"
    trial = billing.create_trial(db, user)
    assert billing.queue_trial_reminders(db) == 0
    later = trial.trial_ends_at - timedelta(days=2) + timedelta(minutes=1)
    assert billing.subscription_view(db, user, now=later)["trial_reminder"] is True
    assert billing.queue_trial_reminders(db, now=later) == 1
    assert billing.queue_trial_reminders(db, now=later) == 0
    mail = db.scalars(select(Outbox)).one()
    assert mail.subject == "Через 2 дня спишем $15"


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
