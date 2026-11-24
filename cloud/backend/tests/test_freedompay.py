# -*- coding: utf-8 -*-
"""Freedom Pay's hosted page: signatures, the server's amount, only a signed, matching callback crediting."""
from __future__ import annotations

import httpx
import pytest
from freedompay_fake import FREEDOM, SECRET, callback, freedom_client, open_checkout, sign, xml
from sqlalchemy import select

from kvist_cloud import billing, ledger
from kvist_cloud.config import ConfigError, load_settings
from kvist_cloud.db import session_scope
from kvist_cloud.models import BillingCheckout, CheckoutStatus


def hours(client) -> int:
    with session_scope(client.app.state.session_factory) as db:
        return ledger.balance_seconds(db, 1) // 3600


def row(client) -> BillingCheckout:
    with session_scope(client.app.state.session_factory) as db:
        found = db.scalars(select(BillingCheckout).order_by(BillingCheckout.id.desc())).first()
        if found is not None:
            db.expunge(found)
        return found


def test_the_signature_is_the_documented_one():
    # docs.freedompay.kz, Create payment: this request and this secret give this signature.
    params = {"pg_merchant_id": "12345", "pg_amount": "100", "pg_currency": "KZT", "pg_description": "Order #1001",
              "pg_order_id": "order_1001", "pg_salt": "a1b2c3d4"}
    assert sign("init_payment.php", params, "test_secret_key") == "b9ad75871668ca040f80f5f6b530e6ed"


def test_configuration_needs_everything_and_hides_the_secret(env):
    assert load_settings(env).freedompay is None
    on = {**env, **FREEDOM}
    for name in ("FREEDOMPAY_MERCHANT_ID", "FREEDOMPAY_SECRET", "FREEDOMPAY_PRICE_PRO"):
        with pytest.raises(ConfigError, match=name):
            load_settings({k: v for k, v in on.items() if k != name})
    with pytest.raises(ConfigError):
        load_settings({**on, "FREEDOMPAY_BASE_URL": "http://x"})
    settings = load_settings(on).freedompay
    assert settings.currency == "KGS" and SECRET not in repr(settings)


def test_a_checkout_asks_freedom_pay_with_the_servers_amount_and_signs_it(client_factory):
    client, fake = freedom_client(client_factory)
    payment_id = open_checkout(client, "pro")
    sent = fake.payments[payment_id]["request"]
    assert sent["pg_amount"] == "3400.00" and sent["pg_currency"] == "KGS"
    assert sent["pg_result_url"].endswith("/billing/freedompay/result") and "pg_testing_mode" not in sent
    stored = row(client)
    assert stored.payment_method == billing.FREEDOMPAY_METHOD and stored.transaction_id == payment_id
    assert sent["pg_order_id"] == stored.token


def test_the_page_cannot_choose_the_price_or_the_plan(client_factory):
    client, fake = freedom_client(client_factory)
    for body in ({"plan": "start", "amount": "1"}, {"plan": "trial"}, {"plan": "remote"}):
        assert client.post("/api/freedompay/checkout", json=body).status_code == 422
    assert fake.requests == []


def test_the_same_link_is_handed_out_again(client_factory):
    client, fake = freedom_client(client_factory)
    assert open_checkout(client) == open_checkout(client) and len(fake.requests) == 1


def test_a_signed_matching_callback_buys_the_plan_once(client_factory):
    client, fake = freedom_client(client_factory)
    payment_id = open_checkout(client)
    response = callback(client, fake, payment_id)
    assert response.status_code == 200 and "<pg_status>ok</pg_status>" in response.text
    assert hours(client) == 50 and row(client).status == CheckoutStatus.PAID
    callback(client, fake, payment_id)
    assert hours(client) == 50


def test_a_failed_payment_changes_nothing(client_factory):
    client, fake = freedom_client(client_factory)
    callback(client, fake, open_checkout(client), pg_result="0")
    assert hours(client) == 0 and row(client).status == CheckoutStatus.PENDING


@pytest.mark.parametrize("change", [{"pg_amount": "1.00"}, {"pg_currency": "USD"}, {"pg_order_id": "x" * 48},
                                    {"pg_payment_id": "999"}])
def test_a_callback_that_is_not_the_purchase_credits_nothing(client_factory, change):
    client, fake = freedom_client(client_factory)
    payment_id = open_checkout(client)
    callback(client, fake, payment_id, **change)
    assert hours(client) == 0


def test_an_unsigned_or_wrongly_signed_callback_is_refused(client_factory):
    client, fake = freedom_client(client_factory)
    payment_id = open_checkout(client)
    assert callback(client, fake, payment_id, secret="not-the-secret").status_code == 401
    unsigned = {"pg_order_id": row(client).token, "pg_result": "1"}
    assert client.post("/billing/freedompay/result", data=unsigned).status_code == 401
    # A valid signature made for another script name (the callback's own path is "result") does not pass.
    order = fake.payments[payment_id]["request"]
    fields = {"pg_order_id": order["pg_order_id"], "pg_payment_id": payment_id, "pg_amount": order["pg_amount"],
              "pg_currency": "KGS", "pg_result": "1", "pg_salt": "z"}
    fields["pg_sig"] = sign("init_payment.php", fields)
    assert client.post("/billing/freedompay/result", data=fields).status_code == 401
    assert hours(client) == 0


def test_a_callback_for_an_order_we_never_made_is_answered_and_ignored(client_factory):
    client, fake = freedom_client(client_factory)
    fields = {"pg_order_id": "f" * 48, "pg_payment_id": "1", "pg_amount": "1.00", "pg_currency": "KGS",
              "pg_result": "1", "pg_salt": "z"}
    fields["pg_sig"] = sign("result", fields)
    assert client.post("/billing/freedompay/result", data=fields).status_code == 200


def test_a_missed_callback_is_made_up_for_by_asking_for_the_status(client_factory):
    client, fake = freedom_client(client_factory)
    payment_id = open_checkout(client)
    client.post("/api/billing/sync")
    assert hours(client) == 0, "not paid yet: nothing credited"
    fake.pay(payment_id)
    client.post("/api/billing/sync")
    assert hours(client) == 50
    client.post("/api/billing/sync")
    assert hours(client) == 50


def test_a_status_that_is_not_what_we_asked_for_credits_nothing(client_factory):
    client, fake = freedom_client(client_factory)
    payment_id = open_checkout(client)
    fake.pay(payment_id)
    fake.tamper = {"pg_amount": "1.00"}
    client.post("/api/billing/sync")
    assert hours(client) == 0 and row(client).status == CheckoutStatus.MISMATCH


def test_an_error_answer_or_a_bad_signature_is_unavailable_and_leaves_no_row(client_factory):
    client, fake = freedom_client(client_factory)
    fake.error = "1200"
    assert client.post("/api/freedompay/checkout", json={"plan": "start"}).status_code == 502
    assert row(client) is None
    unsigned = xml({"pg_status": "ok", "pg_payment_id": "5", "pg_redirect_url": "https://x.example/pay",
                    "pg_sig": "0" * 32})
    client.app.state.http_transport = httpx.MockTransport(lambda request: unsigned)
    assert client.post("/api/freedompay/checkout", json={"plan": "start"}).status_code == 502
    assert row(client) is None


def test_freedom_pay_rows_are_never_looked_up_at_platega(client_factory):
    client, _ = freedom_client(client_factory)
    open_checkout(client)
    client.portal.call(billing.sync_pending, client.app)


def test_the_phone_page_is_told_what_it_may_offer(client_factory):
    client, _ = freedom_client(client_factory)
    offer = client.get("/api/billing/config").json()["freedompay"]
    assert offer["currency"] == "KGS" and offer["prices"]["start"] == "1300.00"
    assert client_factory().get("/api/billing/config").json()["freedompay"] is None
