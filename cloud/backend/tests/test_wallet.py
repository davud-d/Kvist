# -*- coding: utf-8 -*-
"""Apple Pay / Google Pay: the server's amount, the wallet's token, and only Stripe's own word crediting anything."""
from __future__ import annotations

from urllib.parse import parse_qsl

import pytest
from conftest import dev_login, make_user
from sqlalchemy import select
from stripe_fake import (
    APPLE_TOKEN,
    PUBLISHABLE,
    SECRET,
    WALLET,
    WEBHOOK_SECRET,
    event,
    open_checkout,
    pay,
    signed,
    wallet_client,
)

from kvist_cloud import billing, ledger, plans
from kvist_cloud.config import ConfigError, load_settings
from kvist_cloud.db import session_scope
from kvist_cloud.models import BillingCheckout, CheckoutStatus, Subscription, User


def rows(client) -> list[BillingCheckout]:
    with session_scope(client.app.state.session_factory) as db:
        found = list(db.scalars(select(BillingCheckout).order_by(BillingCheckout.id)))
        db.expunge_all()
        return found


def hours(client) -> int:
    with session_scope(client.app.state.session_factory) as db:
        return ledger.balance_seconds(db, 1) // 3600


def webhook(client, body: dict, **kwargs):
    raw, headers = signed(body, **kwargs)
    return client.post("/billing/stripe/webhook", content=raw, headers=headers)


# -- configuration -----------------------------------------------------------------------------------


def test_wallet_is_off_by_default_and_needs_every_key(env):
    assert load_settings(env).wallet is None
    on = {**env, **WALLET}
    for name in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRET"):
        with pytest.raises(ConfigError, match=name):
            load_settings({k: v for k, v in on.items() if k != name})
    wallet = load_settings(on).wallet
    assert wallet.prices["pro"] == 39 and wallet.prices["hours"] == 5 and wallet.currency == "USD"
    assert SECRET not in repr(wallet) and WEBHOOK_SECRET not in repr(wallet)


@pytest.mark.parametrize("name,value", [
    ("STRIPE_SECRET_KEY", "sk_" + "live_0123456789abcdefghijklmnop"),  # a live key beside a test publishable key
    ("WALLET_CURRENCY", "JPY"), ("WALLET_PRICE_START", "0"), ("WALLET_PRICE_PRO", "12.345"),
    ("GOOGLE_PAY_ENVIRONMENT", "PRODUCTION"), ("WALLET_COUNTRY", "usa"), ("STRIPE_BASE_URL", "http://x")])
def test_nonsense_wallet_settings_are_refused(env, name, value):
    with pytest.raises(ConfigError):
        load_settings({**env, **WALLET, name: value})


def test_config_endpoint_says_what_the_apps_may_offer(client_factory):
    off = client_factory().get("/api/wallet/config").json()
    assert off == {"enabled": False, "google_pay": False, "apple_pay": False}
    client, _ = wallet_client(client_factory)
    config = client.get("/api/wallet/config").json()
    assert config["apple_pay"] is True and config["google_environment"] == "TEST"
    bare = {k: v for k, v in WALLET.items() if k != "APPLE_PAY_MERCHANT_ID"}
    other = client_factory(**bare)
    assert other.get("/api/wallet/config").json()["apple_pay"] is False


# -- the checkout: the server's amount, nothing else ---------------------------------------------------


def test_checkout_makes_the_sheet_requests_from_the_servers_own_price(client_factory):
    client, _ = wallet_client(client_factory)
    google = open_checkout(client, "pro")
    sheet = google["request"]
    assert google["amount"] == "39.00" and google["currency"] == "USD"
    assert sheet["transactionInfo"]["totalPrice"] == "39.00" and sheet["transactionInfo"]["totalPriceStatus"] == "FINAL"
    tokenization = sheet["allowedPaymentMethods"][0]["tokenizationSpecification"]["parameters"]
    assert tokenization["gateway"] == "stripe" and tokenization["stripe:publishableKey"] == PUBLISHABLE
    assert SECRET not in str(google)
    apple = open_checkout(client, "pro", "apple_pay")["request"]
    assert apple["merchantIdentifier"] == "merchant.site.kvist.cloud" and apple["merchantCapabilities"] == ["3DS"]
    assert apple["summaryItems"][-1] == {"label": "Kvist Cloud", "amount": "39.00"}


def test_the_phone_cannot_name_its_own_price_or_plan(client_factory):
    client, stripe = wallet_client(client_factory)
    for body in ({"plan": "start", "wallet": "google_pay", "amount": "0.01"}, {"plan": "trial", "wallet": "google_pay"},
                 {"plan": "start", "wallet": "paypal"}):
        assert client.post("/api/wallet/checkout", json=body).status_code == 422
    assert stripe.requests == []


def test_a_sheet_that_was_closed_is_picked_up_again(client_factory):
    client, _ = wallet_client(client_factory)
    first, second = open_checkout(client), open_checkout(client)
    assert first["checkout"] == second["checkout"] and len(rows(client)) == 1


def test_wallet_is_refused_when_switched_off(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    assert client.post("/api/wallet/checkout", json={"plan": "start", "wallet": "google_pay"}).status_code == 503


def test_apple_pay_needs_its_merchant_id(client_factory):
    client, _ = wallet_client(client_factory, APPLE_PAY_MERCHANT_ID="")
    assert client.post("/api/wallet/checkout", json={"plan": "start", "wallet": "apple_pay"}).status_code == 503


# -- paying ------------------------------------------------------------------------------------------------------


def test_google_pay_buys_the_plan(client_factory):
    client, stripe = wallet_client(client_factory)
    checkout = open_checkout(client, "start")
    response = pay(client, checkout)
    assert response.status_code == 200, response.text
    assert response.json()["plan"] == "start" and hours(client) == 50
    charge = stripe.paid()[0]
    form = dict(parse_qsl(charge.content.decode()))
    assert form["amount"] == "1500" and form["currency"] == "usd" and form["metadata[checkout]"] == checkout["checkout"]
    assert charge.headers["Idempotency-Key"] == f"kvist-wallet-{checkout['checkout']}"
    row = rows(client)[0]
    assert row.status == CheckoutStatus.PAID and row.payment_method == billing.WALLET_METHOD


def test_apple_pay_turns_the_payment_token_into_a_stripe_token_first(client_factory):
    client, stripe = wallet_client(client_factory)
    checkout = open_checkout(client, "pro", "apple_pay")
    assert pay(client, checkout, APPLE_TOKEN, "apple_pay").status_code == 200
    assert [r.url.path for r in stripe.requests] == ["/v1/tokens", "/v1/payment_intents"]
    assert hours(client) == 150


def test_the_hour_pack_adds_hours_to_a_running_plan(client_factory):
    client, _ = wallet_client(client_factory)
    assert pay(client, open_checkout(client, "start")).status_code == 200
    assert pay(client, open_checkout(client, "hours")).status_code == 200
    assert hours(client) == 60


def test_the_search_pack_is_sold_at_its_default_price(client_factory):
    client, stripe = wallet_client(client_factory)
    assert pay(client, open_checkout(client, "start")).status_code == 200
    checkout = open_checkout(client, "searches")
    assert (checkout["amount"], checkout["label"]) == ("4.50", f"{plans.SEARCH_PACK_SEARCHES} web searches")
    assert pay(client, checkout).status_code == 200
    with session_scope(client.app.state.session_factory) as db:
        assert db.scalar(select(User)).search_credits == plans.SEARCH_PACK_SEARCHES


def test_paying_the_same_checkout_twice_charges_once(client_factory):
    client, stripe = wallet_client(client_factory)
    checkout = open_checkout(client)
    assert pay(client, checkout).status_code == 200
    assert pay(client, checkout).status_code == 200
    assert len(stripe.paid()) == 1 and hours(client) == 50


def test_a_declined_card_charges_nothing_and_the_next_try_starts_fresh(client_factory):
    client, stripe = wallet_client(client_factory)
    first = open_checkout(client)
    stripe.cards["tok_goodGooglePayToken01"] = "declined"
    response = pay(client, first)
    assert response.status_code == 402 and response.json()["error"]["code"] == "card_declined"
    assert "tok_declined" not in response.text and hours(client) == 0
    stripe.cards["tok_goodGooglePayToken01"] = "succeeded"
    second = open_checkout(client)
    assert second["checkout"] != first["checkout"]
    assert pay(client, second).status_code == 200


def test_a_card_that_wants_its_banks_step_is_cancelled_not_charged(client_factory):
    client, stripe = wallet_client(client_factory)
    stripe.cards["tok_goodGooglePayToken01"] = "requires_action"
    response = pay(client, open_checkout(client))
    assert response.status_code == 402 and response.json()["error"]["code"] == "wallet_needs_authentication"
    assert [i["status"] for i in stripe.intents.values()] == ["canceled"] and hours(client) == 0


@pytest.mark.parametrize("token", ["", "not json", "[]", '{"id": "card_123"}', '{"id": "tok_x"}', "x" * 20_001])
def test_a_bad_wallet_token_never_reaches_stripe(client_factory, token):
    client, stripe = wallet_client(client_factory)
    response = pay(client, open_checkout(client), token)
    assert response.status_code == 400 and stripe.requests == []


def test_a_checkout_of_somebody_else_is_not_found(client_factory):
    client, stripe = wallet_client(client_factory)
    with session_scope(client.app.state.session_factory) as db:
        stranger = make_user(db, "stranger", 2)
        db.add(BillingCheckout(user_id=stranger.id, kind="start", amount="15.00", currency="USD", token="e" * 48,
                               payment_method=billing.WALLET_METHOD))
    assert pay(client, {"checkout": "e" * 48}).status_code == 404
    assert pay(client, {"checkout": "0" * 48}).status_code == 404
    assert stripe.requests == []


def test_a_charge_that_is_not_what_we_asked_for_credits_nothing(client_factory):
    client, stripe = wallet_client(client_factory)
    stripe.tamper = {"amount": 100, "amount_received": 100}
    response = pay(client, open_checkout(client))
    assert response.status_code == 409 and response.json()["error"]["code"] == "payment_needs_review"
    assert rows(client)[0].status == CheckoutStatus.MISMATCH and hours(client) == 0


def test_the_stripe_message_never_reaches_the_phone(client_factory):
    client, stripe = wallet_client(client_factory)
    stripe.cards["tok_goodGooglePayToken01"] = "declined"
    assert "echoes" not in pay(client, open_checkout(client)).text


def test_wallet_checkouts_are_never_looked_up_at_platega(client_factory):
    client, _ = wallet_client(client_factory)
    with session_scope(client.app.state.session_factory) as db:
        db.add(BillingCheckout(user_id=1, kind="start", amount="15.00", currency="USD", token="t" * 48,
                               payment_method=billing.WALLET_METHOD, transaction_id="pi_" + "a" * 24))
    # The sweep would ask Platega about every pending row that has a transaction id; this one it must skip,
    # or the Stripe fake (the only transport here) would fail on a request to another host.
    client.portal.call(billing.sync_pending, client.app)


# -- Stripe's doorbell --------------------------------------------------------------------------------------------


def test_the_webhook_refuses_what_is_not_signed_by_stripe(client_factory):
    client, _ = wallet_client(client_factory)
    body = event("payment_intent.succeeded", id="pi_" + "a" * 24)
    assert webhook(client, body, secret="whsec_" + "wrong_wrong_wrong_wrong").status_code == 401
    assert webhook(client, body, stamp=1_000_000).status_code == 401
    assert client.post("/billing/stripe/webhook", json=body).status_code == 401
    assert client_factory().post("/billing/stripe/webhook", json=body).status_code == 404


def test_a_payment_the_app_never_reported_is_credited_by_the_webhook_once(client_factory):
    client, stripe = wallet_client(client_factory)
    checkout = open_checkout(client)
    intent = stripe.charge_behind_the_apps_back(checkout["checkout"])
    body = event("payment_intent.succeeded", id=intent, metadata={"checkout": checkout["checkout"]})
    assert webhook(client, body).status_code == 200
    assert webhook(client, body).status_code == 200
    assert hours(client) == 50 and rows(client)[0].transaction_id == intent
    with session_scope(client.app.state.session_factory) as db:
        assert len(list(db.scalars(select(Subscription)))) == 1


def test_a_webhook_for_a_payment_that_is_not_ours_changes_nothing(client_factory):
    client, stripe = wallet_client(client_factory)
    body = event("payment_intent.succeeded", id="pi_" + "b" * 24, metadata={"checkout": "f" * 48})
    assert webhook(client, body).status_code == 200 and stripe.requests == [] and hours(client) == 0


def test_the_webhook_trusts_stripe_not_the_event(client_factory):
    client, stripe = wallet_client(client_factory)
    checkout = open_checkout(client)
    intent = stripe.charge_behind_the_apps_back(checkout["checkout"], amount=100)
    body = event("payment_intent.succeeded", id=intent, metadata={"checkout": checkout["checkout"]})
    webhook(client, body)
    assert hours(client) == 0 and rows(client)[0].status == CheckoutStatus.MISMATCH


def test_a_full_refund_or_a_dispute_takes_the_purchase_back_a_partial_one_does_not(client_factory):
    client, stripe = wallet_client(client_factory)
    checkout = open_checkout(client)
    assert pay(client, checkout).status_code == 200
    intent = rows(client)[0].transaction_id
    webhook(client, event("charge.refunded", payment_intent=intent, refunded=False))
    assert hours(client) == 50
    webhook(client, event("charge.refunded", payment_intent=intent, refunded=True))
    assert hours(client) == 0 and rows(client)[0].status == CheckoutStatus.REVOKED
    again = open_checkout(client)
    assert pay(client, again).status_code == 200
    webhook(client, event("charge.dispute.created", payment_intent=rows(client)[1].transaction_id))
    assert rows(client)[1].status == CheckoutStatus.REVOKED
