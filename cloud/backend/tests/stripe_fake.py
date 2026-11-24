# -*- coding: utf-8 -*-
"""Stripe contract fake for the wallet tests. Never sends a request; knows only what wallet.py is meant to ask."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import timedelta
from urllib.parse import parse_qsl

import httpx
from conftest import dev_login
from sqlalchemy import select

from kvist_cloud.db import session_scope, utcnow
from kvist_cloud.models import User

SECRET = "sk_" + "test_0123456789abcdefghijklmnop"
PUBLISHABLE = "pk_" + "test_0123456789abcdefghijklmnop"
WEBHOOK_SECRET = "whsec_" + "0123456789abcdefghijklmnop"
WALLET = {"WALLET_ENABLED": "true", "STRIPE_SECRET_KEY": SECRET, "STRIPE_PUBLISHABLE_KEY": PUBLISHABLE,
          "STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET, "APPLE_PAY_MERCHANT_ID": "merchant.site.kvist.cloud",
          "AUTH_DEV_LOGIN": "true"}
GOOD_TOKEN = json.dumps({"id": "tok_goodGooglePayToken01", "object": "token", "type": "card"})
APPLE_TOKEN = json.dumps({"paymentData": '{"version":"EC_v1","data":"x"}', "transactionIdentifier": "tx-1",
                          "network": "Visa", "displayName": "Visa 4242"})


class StripeFake:
    """``intents`` is what Stripe would hold; ``tamper`` bends what a PaymentIntent reports."""

    def __init__(self):
        self.intents: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []
        self.replays: dict[str, dict] = {}
        self.tamper: dict = {}
        self.cards = {"tok_goodGooglePayToken01": "succeeded", "tok_appleMadeToken0001": "succeeded",
                      "tok_declinedToken0001": "declined", "tok_needsActionToken1": "requires_action"}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.host == "api.stripe.com"
        path = request.url.path
        form = dict(parse_qsl(request.content.decode())) if request.content else {}
        auth = request.headers["Authorization"]
        if path == "/v1/tokens":
            assert auth == f"Bearer {PUBLISHABLE}", "tokens are made with the publishable key"
            assert form["pk_token"] and form["pk_token_payment_network"] == "Visa"
            return httpx.Response(200, json={"id": "tok_appleMadeToken0001", "object": "token"})
        assert auth == f"Bearer {SECRET}", "intents are made with the secret key"
        if path == "/v1/payment_intents" and request.method == "POST":
            key = request.headers["Idempotency-Key"]
            if key in self.replays:
                return httpx.Response(self.replays[key]["code"], json=self.replays[key]["body"])
            reply = self._create(form)
            self.replays[key] = reply
            return httpx.Response(reply["code"], json=reply["body"])
        if path.endswith("/cancel") and request.method == "POST":
            intent = self.intents[path.split("/")[3]]
            intent["status"] = "canceled"
            return httpx.Response(200, json=intent)
        if path.startswith("/v1/payment_intents/") and request.method == "GET":
            intent = self.intents.get(path.rsplit("/", 1)[1])
            if intent is None:
                return httpx.Response(404, json={"error": {"type": "invalid_request_error"}})
            return httpx.Response(200, json=intent | self.tamper)
        raise AssertionError(f"unexpected Stripe request: {request.method} {path}")

    def _create(self, form: dict[str, str]) -> dict:
        assert form["confirm"] == "true" and form["payment_method_data[type]"] == "card"
        assert form["automatic_payment_methods[allow_redirects]"] == "never"
        outcome = self.cards[form["payment_method_data[card][token]"]]
        if outcome == "declined":
            return {"code": 402, "body": {"error": {"type": "card_error", "code": "card_declined",
                                                    "message": "echoes the token tok_declinedToken0001"}}}
        identity = f"pi_{len(self.intents) + 1:024d}"
        amount = int(form["amount"])
        intent = {"id": identity, "object": "payment_intent", "amount": amount, "currency": form["currency"],
                  "amount_received": amount if outcome == "succeeded" else 0, "status": outcome,
                  "metadata": {"checkout": form["metadata[checkout]"], "user_id": form["metadata[user_id]"]}}
        self.intents[identity] = intent
        return {"code": 200, "body": intent | self.tamper}

    def paid(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/v1/payment_intents" and r.method == "POST"]

    def charge_behind_the_apps_back(self, token: str, amount: int = 1500) -> str:
        """A PaymentIntent that succeeded while the app that paid it never reported back."""
        identity = f"pi_{len(self.intents) + 1:024d}"
        self.intents[identity] = {"id": identity, "object": "payment_intent", "amount": amount, "currency": "usd",
                                  "amount_received": amount, "status": "succeeded",
                                  "metadata": {"checkout": token, "user_id": "1"}}
        return identity


def wallet_client(client_factory, **overrides):
    stripe = StripeFake()
    client = client_factory(stripe, **(WALLET | overrides))
    dev_login(client)
    with session_scope(client.app.state.session_factory) as db:
        db.scalar(select(User)).github_created_at = utcnow() - timedelta(days=365)
    return client, stripe


def open_checkout(client, plan="start", wallet="google_pay") -> dict:
    response = client.post("/api/wallet/checkout", json={"plan": plan, "wallet": wallet})
    assert response.status_code == 200, response.text
    return response.json()


def pay(client, checkout: dict, token: str = GOOD_TOKEN, wallet: str = "google_pay"):
    return client.post("/api/wallet/pay", json={"checkout": checkout["checkout"], "wallet": wallet, "token": token})


def signed(body: dict, secret: str = WEBHOOK_SECRET, stamp: float | None = None) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    stamp = int(time.time() if stamp is None else stamp)
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + raw, hashlib.sha256).hexdigest()
    return raw, {"Stripe-Signature": f"t={stamp},v1={digest}", "Content-Type": "application/json"}


def event(kind: str, **obj) -> dict:
    return {"id": "evt_1", "type": kind, "data": {"object": obj}}
