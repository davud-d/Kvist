# -*- coding: utf-8 -*-
"""Google Pay and Apple Pay, from the phone's own payment sheet; the money goes through Stripe.

The apps open the operating system's sheet (Android's PaymentsClient, iOS's PassKit) with the amount this
server chose, and send back the wallet's token. This module turns that token into a charge. It follows the
same rule as billing.py: a purchase exists only as a ``BillingCheckout`` row our server made, and only what
Stripe itself reports about the charge (status, amount, currency, our token in the metadata) can credit it.
A webhook is signed by Stripe, but it is still only used to look at the PaymentIntent again.

Off unless WALLET_ENABLED is true; no request reaches Stripe in that mode, nor in any test.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from datetime import timedelta
from decimal import Decimal
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import billing, plans
from .auth import app_http_client, current_user
from .config import Settings, WalletSettings
from .db import get_db, lock_account, session_scope, utcnow
from .errors import ApiError
from .models import BillingCheckout, CheckoutStatus, User
from .validation import InputModel

log = logging.getLogger("kvist_cloud.wallet")
router = APIRouter()

WEBHOOK_MAX_BYTES = 256_000
WEBHOOK_TOLERANCE_SECONDS = 300
# A sheet the person closed leaves a checkout behind; the next tap on the same plan picks it up again.
REUSE_WINDOW = timedelta(minutes=30)
TOKEN_MAX_CHARS = 20_000
STRIPE_TOKEN = re.compile(r"tok_[A-Za-z0-9]{8,64}")
INTENT_ID = re.compile(r"pi_[A-Za-z0-9]{8,32}")
GOOGLE_NETWORKS = ["AMEX", "DISCOVER", "JCB", "MASTERCARD", "VISA"]
APPLE_NETWORKS = ["amex", "discover", "masterCard", "visa"]
# Google's own sample value for Stripe as the gateway: it names the API version the token is made for.
STRIPE_GATEWAY_VERSION = "2018-10-31"

WalletName = Literal["google_pay", "apple_pay"]


class WalletCheckoutIn(InputModel):
    plan: Literal["start", "pro", "max", "hours"]
    wallet: WalletName


class WalletPayIn(InputModel):
    checkout: str
    wallet: WalletName
    # Google Pay: paymentMethodData.tokenizationData.token. Apple Pay: the PKPaymentToken as JSON (see apple_token).
    token: str


def _unavailable() -> ApiError:
    return ApiError(502, "billing_unavailable", "Payments are unavailable. / Оплата недоступна.")


def _settings(request: Request) -> WalletSettings:
    wallet = request.app.state.settings.wallet
    if wallet is None:
        raise ApiError(503, "wallet_unavailable", "Pay with Apple Pay or Google Pay is unavailable. "
                       "/ Оплата через Apple Pay и Google Pay недоступна.")
    return wallet


def cents(amount: str | Decimal) -> int:
    """Two-decimal currencies only (config.WALLET_CURRENCIES): 39.00 is 3900."""
    return int(Decimal(amount).scaleb(2).to_integral_exact())


# -- the sheets' requests ------------------------------------------------------------------------------


def label_of(kind: str) -> str:
    if kind == "hours":
        return f"{plans.HOUR_PACK_HOURS} hours"
    return f"{kind.capitalize()}, {plans.get_plan(kind).period_days} days"


def google_request(wallet: WalletSettings, checkout: BillingCheckout) -> dict[str, object]:
    """The PaymentDataRequest the Android sheet is opened with (the same JSON the web API takes)."""
    merchant: dict[str, str] = {"merchantName": wallet.merchant_name}
    if wallet.google_merchant_id:
        merchant["merchantId"] = wallet.google_merchant_id
    return {
        "apiVersion": 2,
        "apiVersionMinor": 0,
        "merchantInfo": merchant,
        "allowedPaymentMethods": [{
            "type": "CARD",
            "parameters": {"allowedAuthMethods": ["PAN_ONLY", "CRYPTOGRAM_3DS"], "allowedCardNetworks": GOOGLE_NETWORKS},
            "tokenizationSpecification": {
                "type": "PAYMENT_GATEWAY",
                "parameters": {"gateway": "stripe", "stripe:version": STRIPE_GATEWAY_VERSION,
                               "stripe:publishableKey": wallet.stripe_publishable_key},
            },
        }],
        "transactionInfo": {
            "countryCode": wallet.country, "currencyCode": wallet.currency, "totalPriceStatus": "FINAL",
            "totalPrice": checkout.amount, "checkoutOption": "COMPLETE_IMMEDIATE_PURCHASE",
            "transactionId": checkout.token[:32],
        },
    }


def apple_request(wallet: WalletSettings, checkout: BillingCheckout) -> dict[str, object]:
    """What the iOS app turns into a PKPaymentRequest. The last summary item is the total, named after the seller."""
    return {
        "merchantIdentifier": wallet.apple_merchant_id,
        "countryCode": wallet.country,
        "currencyCode": wallet.currency,
        "supportedNetworks": APPLE_NETWORKS,
        "merchantCapabilities": ["3DS"],
        "summaryItems": [{"label": label_of(checkout.kind), "amount": checkout.amount},
                         {"label": wallet.merchant_name, "amount": checkout.amount}],
    }


# -- talking to Stripe ------------------------------------------------------------------------------------


async def stripe_request(app: FastAPI, method: str, path: str, *, key: str, data: dict[str, str] | None = None,
                         idempotency_key: str = "") -> dict:
    wallet: WalletSettings = app.state.settings.wallet
    headers = {"Authorization": f"Bearer {key}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        async with app_http_client(app) as client:
            response = await client.request(method, wallet.base_url + path, headers=headers, data=data)
    except httpx.HTTPError as error:
        log.warning("stripe %s failed: %s", method, type(error).__name__)
        raise _unavailable() from error
    try:
        body = response.json()
    except ValueError as error:
        log.error("stripe %s answered HTTP %s with no JSON", method, response.status_code)
        raise _unavailable() from error
    if not isinstance(body, dict):
        raise _unavailable()
    if response.status_code >= 400:
        failure = body.get("error") if isinstance(body.get("error"), dict) else {}
        # Only the codes: a message can echo what we sent.
        log.warning("stripe %s answered HTTP %s (%s/%s)", method, response.status_code, failure.get("type"),
                    failure.get("code"))
        if response.status_code == 402 or failure.get("type") == "card_error":
            raise ApiError(402, "card_declined", "The card was declined. Try another card or pay another way. "
                           "/ Карта отклонена. Попробуй другую карту или другой способ оплаты.")
        raise _unavailable()
    return body


def google_stripe_token(token: str) -> str:
    """The Stripe token Google Pay made: with the stripe gateway the sheet's token is the token object itself."""
    try:
        parsed = json.loads(token)
    except ValueError:
        parsed = None
    identity = parsed.get("id") if isinstance(parsed, dict) else None
    if not isinstance(identity, str) or not STRIPE_TOKEN.fullmatch(identity):
        raise ApiError(400, "bad_wallet_token", "The wallet's token is not valid. / Токен кошелька не подходит.")
    return identity


async def apple_stripe_token(app: FastAPI, token: str) -> str:
    """Stripe makes a card token out of the PKPaymentToken, the way its own iOS library does.

    The app sends {"paymentData": "<the token's JSON as text>", "transactionIdentifier", "network",
    "displayName"}. The call is made with the publishable key: the secret one is never needed for this step.
    """
    try:
        parsed = json.loads(token)
    except ValueError:
        parsed = None
    fields = ("paymentData", "transactionIdentifier", "network", "displayName")
    if not isinstance(parsed, dict) or not all(isinstance(parsed.get(name), str) for name in fields) \
            or not parsed["paymentData"]:
        raise ApiError(400, "bad_wallet_token", "The wallet's token is not valid. / Токен кошелька не подходит.")
    created = await stripe_request(app, "POST", "/v1/tokens", key=app.state.settings.wallet.stripe_publishable_key, data={
        "pk_token": parsed["paymentData"],
        "pk_token_instrument_name": parsed["displayName"],
        "pk_token_payment_network": parsed["network"],
        "pk_token_transaction_id": parsed["transactionIdentifier"],
    })
    identity = created.get("id")
    if not isinstance(identity, str) or not STRIPE_TOKEN.fullmatch(identity):
        raise _unavailable()
    return identity


# -- what Stripe says decides -----------------------------------------------------------------------------


def intent_matches(checkout: BillingCheckout, intent: dict) -> bool:
    """Is Stripe's own record of the charge the purchase our server created?"""
    amount = cents(checkout.amount)
    metadata = intent.get("metadata") if isinstance(intent.get("metadata"), dict) else {}
    return (intent.get("amount") == amount and intent.get("amount_received") == amount
            and str(intent.get("currency") or "").upper() == checkout.currency
            and metadata.get("checkout") == checkout.token
            and intent.get("id") == checkout.transaction_id)


def apply_intent(db: Session, checkout: BillingCheckout, intent: dict) -> None:
    """Act on Stripe's PaymentIntent; idempotent, so a retry, the webhook and the app may all arrive."""
    if intent.get("status") != "succeeded" or checkout.status != CheckoutStatus.PENDING:
        return
    if not intent_matches(checkout, intent):
        checkout.status = CheckoutStatus.MISMATCH
        log.error("wallet: a succeeded payment does not match checkout %s", checkout.id)
        return
    billing.fulfill(db, checkout, utcnow())


def _intent_id(raw: object) -> str:
    if not isinstance(raw, str) or not INTENT_ID.fullmatch(raw):
        raise _unavailable()
    return raw


async def sync_intent(app: FastAPI, intent_id: str, token: object = None) -> None:
    """The webhook's use: ask Stripe about the PaymentIntent and apply the answer.

    ``token`` is the checkout token the event carries in its metadata: it finds the purchase of an app that
    was closed after paying and never reported back. The answer Stripe gives about the intent decides, not the event.
    """
    with session_scope(app.state.session_factory) as db:
        row = db.scalar(select(BillingCheckout).where(BillingCheckout.transaction_id == intent_id))
        if row is None and isinstance(token, str):
            row = db.scalar(select(BillingCheckout).where(
                BillingCheckout.token == token, BillingCheckout.payment_method == billing.WALLET_METHOD,
                BillingCheckout.transaction_id.is_(None)))
            if row is not None:
                row.transaction_id = intent_id
        known = row.id if row is not None else None
    if known is None:
        # A charge that is not ours (the account may sell other things).
        log.info("wallet: notification for a payment this server has no checkout for")
        return
    intent = await stripe_request(app, "GET", f"/v1/payment_intents/{intent_id}",
                                  key=app.state.settings.wallet.stripe_secret_key)
    with session_scope(app.state.session_factory) as db:
        checkout = db.get(BillingCheckout, known)
        lock_account(db, checkout.user_id)
        db.refresh(checkout)
        apply_intent(db, checkout, intent)


def reverse_intent(app: FastAPI, intent_id: str) -> None:
    """A refund or a dispute on a purchase: what it gave is taken back."""
    with session_scope(app.state.session_factory) as db:
        checkout = db.scalar(select(BillingCheckout).where(BillingCheckout.transaction_id == intent_id))
        if checkout is None:
            return
        lock_account(db, checkout.user_id)
        db.refresh(checkout)
        if checkout.status == CheckoutStatus.PAID:
            billing.revoke(db, checkout)


# -- API ---------------------------------------------------------------------------------------------------


@router.get("/api/wallet/config")
def wallet_config(request: Request) -> dict[str, object]:
    wallet: WalletSettings | None = request.app.state.settings.wallet
    if wallet is None:
        return {"enabled": False, "google_pay": False, "apple_pay": False}
    return {
        "enabled": True,
        "google_pay": True,
        "apple_pay": bool(wallet.apple_merchant_id),
        "currency": wallet.currency,
        "prices": {key: str(price) for key, price in wallet.prices.items()},
    }


def _reusable(db: Session, user: User, plan: str) -> BillingCheckout | None:
    return db.scalar(select(BillingCheckout).where(
        BillingCheckout.user_id == user.id, BillingCheckout.kind == plan,
        BillingCheckout.payment_method == billing.WALLET_METHOD, BillingCheckout.status == CheckoutStatus.PENDING,
        BillingCheckout.transaction_id.is_(None), BillingCheckout.created_at > utcnow() - REUSE_WINDOW,
    ).order_by(BillingCheckout.id.desc()))


@router.post("/api/wallet/checkout")
def wallet_checkout(payload: WalletCheckoutIn, request: Request, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict[str, object]:
    wallet = _settings(request)
    if payload.wallet == "apple_pay" and not wallet.apple_merchant_id:
        raise ApiError(503, "wallet_unavailable", "Apple Pay is not set up. / Apple Pay не настроен.")
    lock_account(db, user.id)
    db.refresh(user)
    billing._check_purchase_allowed(db, user, payload.plan)
    row = _reusable(db, user, payload.plan)
    if row is None:
        recent = db.scalar(select(func.count()).select_from(BillingCheckout).where(
            BillingCheckout.user_id == user.id, BillingCheckout.created_at > utcnow() - timedelta(hours=1)))
        if recent >= billing.MAX_NEW_CHECKOUTS_PER_HOUR:
            raise ApiError(429, "too_many_checkouts", "Too many payment attempts, try again later. "
                           "/ Слишком много попыток оплаты, попробуй позже.")
        row = BillingCheckout(user_id=user.id, kind=payload.plan, amount=str(wallet.prices[payload.plan]),
                              currency=wallet.currency, payment_method=billing.WALLET_METHOD,
                              token=secrets.token_hex(24))
        db.add(row)
        db.commit()
    build = google_request if payload.wallet == "google_pay" else apple_request
    return {"checkout": row.token, "amount": row.amount, "currency": row.currency, "label": label_of(row.kind),
            "request": build(wallet, row)}


def _own_checkout(db: Session, user: User, token: str) -> BillingCheckout:
    row = db.scalar(select(BillingCheckout).where(
        BillingCheckout.token == token, BillingCheckout.user_id == user.id,
        BillingCheckout.payment_method == billing.WALLET_METHOD))
    if row is None:
        raise ApiError(404, "not_found", "Not found. / Не найдено.")
    return row


@router.post("/api/wallet/pay")
async def wallet_pay(payload: WalletPayIn, request: Request, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict[str, object]:
    """The wallet's token for a checkout this server made: charge it, and credit only what Stripe confirms."""
    wallet = _settings(request)
    app = request.app
    if len(payload.token) > TOKEN_MAX_CHARS:
        raise ApiError(400, "bad_wallet_token", "The wallet's token is not valid. / Токен кошелька не подходит.")
    lock_account(db, user.id)
    db.refresh(user)
    row = _own_checkout(db, user, payload.checkout)
    if row.status == CheckoutStatus.PAID:
        return billing.subscription_view(db, user)
    if row.status != CheckoutStatus.PENDING:
        raise ApiError(409, "checkout_closed", "This payment is closed. / Этот платёж закрыт.")
    billing._check_purchase_allowed(db, user, row.kind)
    row_id, amount, currency, token, kind = row.id, row.amount, row.currency, row.token, row.kind
    db.commit()
    if payload.wallet == "apple_pay" and not wallet.apple_merchant_id:
        raise ApiError(503, "wallet_unavailable", "Apple Pay is not set up. / Apple Pay не настроен.")
    card = (google_stripe_token(payload.token) if payload.wallet == "google_pay"
            else await apple_stripe_token(app, payload.token))
    try:
        intent = await stripe_request(
            app, "POST", "/v1/payment_intents", key=wallet.stripe_secret_key, idempotency_key=f"kvist-wallet-{token}",
            data={
                "amount": str(cents(amount)), "currency": currency.lower(), "confirm": "true",
                "payment_method_data[type]": "card", "payment_method_data[card][token]": card,
                "automatic_payment_methods[enabled]": "true", "automatic_payment_methods[allow_redirects]": "never",
                "metadata[checkout]": token, "metadata[user_id]": str(user.id),
                "description": f"Kvist Cloud: {label_of(kind)}",
            })
    except ApiError as error:
        if error.status == 402:
            # Stripe replays the same answer to the same idempotency key: a new try needs a new checkout.
            with session_scope(app.state.session_factory) as other:
                stale = other.get(BillingCheckout, row_id)
                if stale is not None and stale.status == CheckoutStatus.PENDING and stale.transaction_id is None:
                    other.delete(stale)
        raise
    intent_id = _intent_id(intent.get("id"))
    if intent.get("status") == "requires_action":
        # A card that wants its bank's extra step cannot do it inside the sheet; nothing was charged.
        await stripe_request(app, "POST", f"/v1/payment_intents/{intent_id}/cancel", key=wallet.stripe_secret_key)
        with session_scope(app.state.session_factory) as other:
            stale = other.get(BillingCheckout, row_id)
            if stale is not None and stale.status == CheckoutStatus.PENDING:
                other.delete(stale)
        raise ApiError(402, "wallet_needs_authentication", "This card needs a confirmation the wallet cannot do. "
                       "Pay another way. / Эта карта требует подтверждения, которого кошелёк не умеет. "
                       "Оплати другим способом.")
    db.refresh(row)
    lock_account(db, user.id)
    row.transaction_id = intent_id
    db.flush()
    apply_intent(db, row, intent)
    db.commit()
    if row.status == CheckoutStatus.PAID:
        return billing.subscription_view(db, user)
    if row.status in (CheckoutStatus.NEEDS_REVIEW, CheckoutStatus.MISMATCH):
        raise ApiError(409, "payment_needs_review", "The payment arrived but cannot be applied yet; "
                       "we will sort it out. / Платёж прошёл, но пока не применён; мы разберёмся.")
    raise ApiError(402, "card_declined", "The payment did not go through. / Платёж не прошёл.")


# -- Stripe's doorbell -------------------------------------------------------------------------------------


def signature_valid(secret: str, header: str, body: bytes, now: float | None = None) -> bool:
    """Stripe-Signature: ``t=<unix>,v1=<hex HMAC-SHA256 of "<t>.<body>">`` (v1 may repeat); five minutes of slack."""
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        name, _, value = item.strip().partition("=")
        parts.setdefault(name, []).append(value)
    try:
        stamp = int(parts.get("t", [""])[0])
    except ValueError:
        return False
    if abs((time.time() if now is None else now) - stamp) > WEBHOOK_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in parts.get("v1", []))


@router.post("/billing/stripe/webhook")
async def stripe_webhook(request: Request) -> dict[str, bool]:
    settings: Settings = request.app.state.settings
    if settings.wallet is None:
        raise ApiError(404, "not_found", "Not found. / Не найдено.")
    body = await request.body()
    if len(body) > WEBHOOK_MAX_BYTES:
        raise ApiError(413, "bad_payload", "Payload too large.")
    if not signature_valid(settings.wallet.webhook_secret, request.headers.get("stripe-signature", ""), body):
        raise ApiError(401, "bad_signature", "Access denied.")
    try:
        event = json.loads(body)
    except (ValueError, UnicodeDecodeError) as error:
        raise ApiError(400, "bad_payload", "Not JSON.") from error
    kind = event.get("type") if isinstance(event, dict) else None
    obj = event.get("data", {}).get("object") if isinstance(event, dict) and isinstance(event.get("data"), dict) else None
    if not isinstance(obj, dict):
        return {"ok": True}
    if kind == "payment_intent.succeeded":
        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
        await sync_intent(request.app, _intent_id(obj.get("id")), metadata.get("checkout"))
    elif kind in {"charge.refunded", "charge.dispute.created"}:
        # A refund counts only when it is the whole charge; a dispute always takes the purchase back.
        intent = obj.get("payment_intent")
        if isinstance(intent, str) and INTENT_ID.fullmatch(intent) and (kind == "charge.dispute.created" or obj.get("refunded") is True):
            reverse_intent(request.app, intent)
    return {"ok": True}
