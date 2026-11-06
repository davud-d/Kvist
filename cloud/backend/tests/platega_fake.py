# -*- coding: utf-8 -*-
"""Platega contract fake, built from its published API. Never sends a request or touches a real merchant."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import httpx
from conftest import dev_login
from sqlalchemy import select

from kvist_cloud.db import session_scope, utcnow
from kvist_cloud.models import User

MERCHANT = "1a021d91-9b26-4762-b303-5d4aac74e921"
SECRET = "platega_test_secret_value_0123456789"
PRICES = {"start": "1500.00", "pro": "3900.00", "max": "9900.00", "hours": "500.00"}
PLATEGA = {"BILLING_ENABLED": "true", "PLATEGA_MERCHANT_ID": MERCHANT, "PLATEGA_SECRET": SECRET,
           "PLATEGA_PRICE_START": PRICES["start"], "PLATEGA_PRICE_PRO": PRICES["pro"],
           "PLATEGA_PRICE_MAX": PRICES["max"], "PLATEGA_PRICE_HOURS": PRICES["hours"], "AUTH_DEV_LOGIN": "true"}


class PlategaFake:
    """``transactions`` is what Platega would hold; ``tamper`` bends what its status endpoint reports."""

    def __init__(self):
        self.transactions: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []
        self.tamper: dict = {}

    def __call__(self, request):
        self.requests.append(request)
        assert request.url.host == "app.platega.io"
        assert request.headers["X-MerchantId"] == MERCHANT and request.headers["X-Secret"] == SECRET
        path = request.url.path
        if path == "/transaction/process" and request.method == "POST":
            body = json.loads(request.content)
            assert body["metadata"]["user_email"] and "id" not in body
            identity = str(uuid.uuid4())
            self.transactions[identity] = {"request": body, "status": "PENDING"}
            return httpx.Response(200, json={"paymentMethod": "SBPQR", "transactionId": identity,
                                             "redirect": f"https://pay.platega.io/?id={identity}",
                                             "status": "PENDING", "expiresIn": "00:15:00", "merchantId": MERCHANT})
        if path.startswith("/transaction/") and request.method == "GET":
            record = self.transactions.get(path.removeprefix("/transaction/"))
            if record is None:
                return httpx.Response(404, json={"code": "Common:NF_0001"})
            body = record["request"]
            return httpx.Response(200, json={
                "id": path.removeprefix("/transaction/"), "status": record["status"],
                "paymentDetails": body["paymentDetails"], "payload": body["payload"],
                "paymentMethod": "SBPQR", "return": body["return"]} | self.tamper)
        raise AssertionError(f"unexpected Platega request: {request.method} {path}")

    def pay(self, transaction_id: str, status: str = "CONFIRMED") -> None:
        self.transactions[transaction_id]["status"] = status

    def gets(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "GET"]


def paid_client(client_factory, **overrides):
    platega = PlategaFake()
    client = client_factory(platega, **(PLATEGA | overrides))
    dev_login(client)
    with session_scope(client.app.state.session_factory) as db:
        user = db.scalar(select(User))
        user.github_created_at = utcnow() - timedelta(days=365)
    return client, platega


def open_checkout(client, kind="start") -> str:
    """The transaction id of a checkout our server created for the signed-in user."""
    response = client.post("/api/billing/checkout", json={"plan": kind})
    assert response.status_code == 200, response.text
    return response.json()["redirect"].rsplit("=", 1)[1]


def callback(client, transaction_id: str, merchant: str = MERCHANT, secret: str = SECRET, **body):
    headers = {"Content-Type": "application/json; charset=utf-8", "User-Agent": "Platega Payment"}
    if merchant is not None:
        headers["X-MerchantId"] = merchant
    if secret is not None:
        headers["X-Secret"] = secret
    payload = {"id": transaction_id, "amount": 1500, "currency": "RUB", "status": "CONFIRMED",
               "paymentMethod": 2, "payload": ""} | body
    return client.post("/billing/platega/callback", json=payload, headers=headers)


def pay_and_notify(client, platega, kind="start") -> str:
    transaction_id = open_checkout(client, kind)
    platega.pay(transaction_id)
    assert callback(client, transaction_id).status_code == 200
    return transaction_id
