# -*- coding: utf-8 -*-
"""Paddle contract fake. Never sends a request or handles a real card."""
from __future__ import annotations

import json
from datetime import timedelta

import httpx
from conftest import dev_login
from sqlalchemy import select

from kvist_cloud import plans
from kvist_cloud.db import session_scope, utcnow
from kvist_cloud.models import User

PADDLE = {"BILLING_ENABLED": "true", "PADDLE_WEBHOOK_SECRET": "pdl_ntfset_test_secret_value",
          "PADDLE_API_KEY": "pdl_api_key_test_value", "PADDLE_PRICE_TRIAL": "pri_trial",
          "PADDLE_PRICE_START": "pri_start", "PADDLE_PRICE_PRO": "pri_pro", "PADDLE_PRICE_MAX": "pri_max",
          "PADDLE_PRICE_HOURS": "pri_hours", "AUTH_DEV_LOGIN": "true"}


class PaddleFake:
    def __init__(self):
        self.transactions = {}
        self.subscriptions = {}
        self.adjustments = []
        self.requests = []
        self.refund_status = "approved"
        self.price_override = {}

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path.startswith("/prices/"):
            kind = path.removeprefix("/prices/pri_")
            plan = plans.get_plan("start" if kind in {"trial", "hours"} else kind)
            amount = plans.HOUR_PACK_PRICE_USD if kind == "hours" else plan.price_usd
            price = {"id": f"pri_{kind}", "status": "active", "tax_mode": "internal",
                     "unit_price": {"amount": str(amount * 100), "currency_code": "USD"},
                     "billing_cycle": None if kind == "hours" else {"interval": "month", "frequency": 1},
                     "trial_period": None}
            if kind == "trial":
                price["trial_period"] = {"interval": "day", "frequency": 7, "requires_payment_method": True,
                                         "unit_price": {"amount": "100", "currency_code": "USD"}}
            return httpx.Response(200, json={"data": price | self.price_override})
        if path == "/transactions" and request.method == "POST":
            body = json.loads(request.content)
            identity = f"txn_{len(self.transactions) + 1}"
            assert "custom_data" not in body
            self.transactions[identity] = body
            return httpx.Response(201, json={"data": {"id": identity}})
        if path.startswith("/subscriptions/"):
            if path.endswith("/cancel"):
                return httpx.Response(200, json={"data": {}})
            return httpx.Response(200, json={"data": self.subscriptions[path.split("/")[-1]]})
        if path == "/adjustments":
            if request.method == "GET":
                rows = [a for a in self.adjustments if a["transaction_id"] == request.url.params["transaction_id"]]
                return httpx.Response(200, json={"data": rows})
            body = json.loads(request.content)
            row = body | {"id": f"adj_{len(self.adjustments) + 1}", "status": self.refund_status}
            self.adjustments.append(row)
            return httpx.Response(201, json={"data": row})
        raise AssertionError(f"unexpected Paddle request: {request.method} {path}")

    def payment(self, transaction_id, kind="trial", sub_id="sub_01", start=None):
        now = start or utcnow() - timedelta(seconds=10)
        end = now + timedelta(days=7 if kind == "trial" else 30)
        period = {"starts_at": now.isoformat(), "ends_at": end.isoformat()}
        price = f"pri_{kind}"
        plan = plans.get_plan("start" if kind in {"trial", "hours"} else kind)
        cents = 100 if kind == "trial" else (plans.HOUR_PACK_PRICE_USD if kind == "hours" else plan.price_usd) * 100
        if kind != "hours":
            self.subscriptions[sub_id] = {
                "id": sub_id, "status": "trialing" if kind == "trial" else "active", "customer_id": "ctm_01",
                "items": [{"price": {"id": price}, "quantity": 1}], "current_billing_period": period,
                "updated_at": utcnow().isoformat(),
            }
        return {"id": transaction_id, "status": "completed", "currency_code": "USD",
                "subscription_id": sub_id if kind != "hours" else None, "billing_period": period,
                "items": [{"price": {"id": price}, "quantity": 1}],
                "details": {"totals": {"total": str(cents), "credit": "0"}},
                "payments": [{"status": "captured", "amount": str(cents)}]}


def paid_client(client_factory):
    paddle = PaddleFake()
    client = client_factory(paddle, **PADDLE)
    dev_login(client)
    with session_scope(client.app.state.session_factory) as db:
        user = db.scalar(select(User))
        user.github_created_at = utcnow() - timedelta(days=365)
    return client, paddle


def open_checkout(client, kind="trial"):
    response = client.post("/api/billing/checkout", json={"plan": kind})
    assert response.status_code == 200, response.text
    return response.json()["transaction_id"]
