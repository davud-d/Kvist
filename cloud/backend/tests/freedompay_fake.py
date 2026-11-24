# -*- coding: utf-8 -*-
"""Freedom Pay contract fake, built from docs.freedompay.kz. Never sends a request or touches a real merchant."""
from __future__ import annotations

import hashlib
from datetime import timedelta
from urllib.parse import parse_qsl

import httpx
from conftest import dev_login
from sqlalchemy import select

from kvist_cloud.db import session_scope, utcnow
from kvist_cloud.models import User

MERCHANT = "123456"
SECRET = "freedom_test_secret_value_0123456789"
FREEDOM = {"FREEDOMPAY_ENABLED": "true", "FREEDOMPAY_MERCHANT_ID": MERCHANT, "FREEDOMPAY_SECRET": SECRET,
           "FREEDOMPAY_PRICE_START": "1300.00", "FREEDOMPAY_PRICE_PRO": "3400.00", "FREEDOMPAY_PRICE_MAX": "8600.00",
           "FREEDOMPAY_PRICE_HOURS": "430.00", "AUTH_DEV_LOGIN": "true"}


def sign(script: str, params: dict[str, str], secret: str = SECRET) -> str:
    values = [params[name] for name in sorted(params) if name != "pg_sig"]
    return hashlib.md5(";".join([script, *values, secret]).encode()).hexdigest()  # noqa: S324


def xml(fields: dict[str, str], script: str | None = None, secret: str = SECRET) -> httpx.Response:
    if script:
        fields = {**fields, "pg_salt": "s4lt"}
        fields["pg_sig"] = sign(script, fields, secret)
    body = "".join(f"<{name}>{value}</{name}>" for name, value in fields.items())
    return httpx.Response(200, text=f'<?xml version="1.0" encoding="utf-8"?><response>{body}</response>')


class FreedomFake:
    """``payments`` is what Freedom Pay would hold; ``tamper`` bends what its status endpoint reports."""

    def __init__(self):
        self.payments: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []
        self.tamper: dict = {}
        self.error: str | None = None

    def __call__(self, request):
        self.requests.append(request)
        assert request.url.host == "api.freedompay.kg"
        script = request.url.path.rsplit("/", 1)[-1]
        form = dict(parse_qsl(request.content.decode()))
        assert form["pg_merchant_id"] == MERCHANT and form["pg_sig"] == sign(script, form), "bad request signature"
        if self.error:
            return xml({"pg_status": "error", "pg_error_code": self.error})
        if script == "init_payment.php":
            payment_id = str(1022123800000 + len(self.payments))
            self.payments[payment_id] = {"request": form, "status": "new"}
            return xml({"pg_status": "ok", "pg_payment_id": payment_id,
                        "pg_redirect_url": f"https://customer.freedompay.kg/pay?id={payment_id}",
                        "pg_redirect_url_type": "need data"}, script)
        if script == "get_status3.php":
            record = self.payments.get(form["pg_payment_id"])
            if record is None:
                return xml({"pg_status": "error", "pg_error_code": "11068"})
            order = record["request"]
            return xml({"pg_status": "ok", "pg_payment_id": form["pg_payment_id"],
                        "pg_payment_status": record["status"], "pg_currency": order["pg_currency"],
                        "pg_amount": order["pg_amount"]} | self.tamper, script)
        raise AssertionError(f"unexpected Freedom Pay request: {script}")

    def pay(self, payment_id: str) -> None:
        self.payments[payment_id]["status"] = "success"


def freedom_client(client_factory, **overrides):
    fake = FreedomFake()
    client = client_factory(fake, **(FREEDOM | overrides))
    dev_login(client)
    with session_scope(client.app.state.session_factory) as db:
        db.scalar(select(User)).github_created_at = utcnow() - timedelta(days=365)
    return client, fake


def open_checkout(client, plan="start") -> str:
    response = client.post("/api/freedompay/checkout", json={"plan": plan})
    assert response.status_code == 200, response.text
    return response.json()["redirect"].rsplit("=", 1)[1]


def callback(client, fake: FreedomFake, payment_id: str, secret: str = SECRET, **changes):
    """What Freedom Pay posts to pg_result_url once the person has paid."""
    order = fake.payments[payment_id]["request"]
    fields = {"pg_order_id": order["pg_order_id"], "pg_payment_id": payment_id, "pg_amount": order["pg_amount"],
              "pg_currency": order["pg_currency"], "pg_result": "1", "pg_can_reject": "1", "pg_salt": "abc"} | changes
    fields["pg_sig"] = sign("result", fields, secret)
    return client.post("/billing/freedompay/result", data=fields)
