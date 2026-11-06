# -*- coding: utf-8 -*-
"""Paddle billing, behind BILLING_ENABLED (false by default).

Enabled: the browser opens the Paddle.js overlay; Paddle tells us what happened through signed
webhooks, which are the only thing that creates or changes a subscription.
Disabled: the overlay is replaced by a stub with the same form and a "skip (dev)" button, which
creates the trial directly. No request ever reaches Paddle in that mode, nor in any test.
"""
from __future__ import annotations

import asyncio
import calendar
import hashlib
import hmac
import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import ledger, plans
from .auth import current_user, http_client
from .config import Settings
from .db import get_db, lock_account, session_scope, utcnow
from .errors import ApiError
from .funnel import record_step
from .models import (
    BillingCheckout,
    BillingPayment,
    LedgerEntry,
    LedgerKind,
    Outbox,
    PaddleEvent,
    Subscription,
    SubscriptionStatus,
    User,
)
from .validation import InputModel

log = logging.getLogger("kvist_cloud.billing")
router = APIRouter()
SIGNATURE_TOLERANCE_SECONDS = 300
# Well past the two 10 s Paddle calls a checkout makes; an older row without a transaction is abandoned.
CHECKOUT_CREATE_TIMEOUT = timedelta(minutes=2)
REMINDER_BEFORE = timedelta(days=2)
SWEEP_SECONDS = 300.0
PADDLE_API = {"sandbox": "https://sandbox-api.paddle.com", "production": "https://api.paddle.com"}
PADDLE_STATUS = {
    "trialing": SubscriptionStatus.TRIALING,
    "active": SubscriptionStatus.ACTIVE,
    "past_due": SubscriptionStatus.PAST_DUE,
    "paused": SubscriptionStatus.CANCELLED,
    "canceled": SubscriptionStatus.CANCELLED,
}


class CheckoutIn(InputModel):
    plan: Literal["trial", "start", "pro", "max", "hours"] = "trial"


# -- signatures ----------------------------------------------------------------------------


def verify_signature(secret: str, header: str, body: bytes, now: float | None = None) -> bool:
    """Paddle-Signature: ``ts=<unix>;h1=<hex hmac-sha256 of "<ts>:<raw body>">``."""
    if not secret or not header:
        return False
    stamp = ""
    digests: list[str] = []
    for item in header.split(";"):
        name, _, value = item.strip().partition("=")
        if name == "ts":
            stamp = value
        elif name == "h1":
            digests.append(value)
    if not stamp.isascii() or not stamp.isdigit() or len(stamp) > 12 or not digests:
        return False
    current = time.time() if now is None else now
    if abs(current - int(stamp)) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{stamp}:".encode() + body, hashlib.sha256).hexdigest()
    return any(digest.isascii() and hmac.compare_digest(expected, digest) for digest in digests)


def sign(secret: str, body: bytes, stamp: int) -> str:
    """What Paddle sends; used by the tests and nowhere else."""
    digest = hmac.new(secret.encode(), f"{stamp}:".encode() + body, hashlib.sha256).hexdigest()
    return f"ts={stamp};h1={digest}"


# -- subscription state ----------------------------------------------------------------------


def _parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (ValueError, AttributeError):
        return None


def plan_for_price(settings: Settings, price_id: str) -> str:
    prices = {settings.paddle_price_start: "start", settings.paddle_price_pro: "pro", settings.paddle_price_max: "max"}
    if settings.paddle_price_trial:
        prices[settings.paddle_price_trial] = "start"
    return prices.get(price_id, "") if price_id else ""


def _start_period(db: Session, subscription: Subscription, start: datetime, end: datetime) -> None:
    """A new billing period begins: its plan hours are granted once."""
    subscription.current_period_start = start
    subscription.current_period_end = end
    db.flush()
    plan = plans.get_plan(subscription.plan)
    ledger.grant_period(db, subscription, plan.seconds, f"{plan.id} plan hours")


def create_trial(db: Session, user: User, now: datetime | None = None) -> Subscription:
    now = now or utcnow()
    user.trial_used_at = now
    ends = now + timedelta(days=plans.TRIAL.trial_days)
    subscription = Subscription(
        user_id=user.id,
        plan=plans.TRIAL.id,
        status=SubscriptionStatus.TRIALING,
        current_period_start=now,
        current_period_end=ends,
        trial_ends_at=ends,
    )
    db.add(subscription)
    _start_period(db, subscription, now, ends)
    return subscription


def trial_eligible(user: User, now: datetime | None = None) -> bool:
    """Strictly older than one calendar month, using GitHub's authenticated profile."""
    now = now or utcnow()
    year, month = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    cutoff = now.replace(year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1]))
    return user.trial_used_at is None and user.github_created_at is not None and user.github_created_at < cutoff


def _single_price(data: dict) -> str:
    items = data.get("items") or []
    if len(items) != 1 or items[0].get("quantity") != 1:
        return ""
    return str((items[0].get("price") or {}).get("id") or "")


def _period(data: dict, key: str) -> tuple[datetime | None, datetime | None]:
    period = data.get(key) or {}
    start, end = _parse_time(period.get("starts_at")), _parse_time(period.get("ends_at"))
    if start is None or end is None or end <= start:
        return None, None
    return start, end


def _checkout_for_subscription(db: Session, subscription_id: str) -> BillingCheckout | None:
    return db.scalar(select(BillingCheckout).where(BillingCheckout.subscription_id == subscription_id))


def apply_paddle_subscription(db: Session, settings: Settings, data: dict) -> Subscription | None:
    """Apply an authoritative Paddle snapshot, never browser metadata or an unconfirmed invoice."""
    paddle_id = str(data.get("id") or "")
    checkout = _checkout_for_subscription(db, paddle_id)
    if checkout is None or not checkout.completed:
        return None
    lock_account(db, checkout.user_id)
    user = db.get(User, checkout.user_id, populate_existing=True)
    subscription = db.scalar(select(Subscription).where(Subscription.paddle_subscription_id == paddle_id)
                             .execution_options(populate_existing=True))
    updated = _parse_time(data.get("updated_at"))
    if updated is None or (subscription and subscription.provider_updated_at
                           and updated < subscription.provider_updated_at):
        return subscription
    status = PADDLE_STATUS.get(data.get("status"), SubscriptionStatus.PAST_DUE)
    price = _single_price(data)
    paid_plan = plan_for_price(settings, price)
    start, end = _period(data, "current_billing_period")
    if status not in SubscriptionStatus.USABLE or not paid_plan or start is None:
        if subscription:
            subscription.status = status if status not in SubscriptionStatus.USABLE else SubscriptionStatus.PAST_DUE
            subscription.provider_updated_at = updated
        return subscription
    if status == SubscriptionStatus.TRIALING:
        proof = db.get(BillingPayment, checkout.transaction_id)
        if proof is None or proof.revoked:
            return None
        if checkout.kind != "trial" or not checkout.refunded or price != checkout.price_id:
            return None
        if subscription is None and not trial_eligible(user):
            return None
        if end - start > timedelta(days=plans.TRIAL.trial_days):
            return None
    else:
        proof = db.scalar(select(BillingPayment).where(
            BillingPayment.subscription_id == paddle_id, BillingPayment.price_id == price,
            BillingPayment.period_start == start, BillingPayment.period_end == end, BillingPayment.revoked.is_(False)))
        if proof is None:
            # An 'active' notification can arrive before its payment notification.
            return subscription
    if subscription is None:
        if ledger.current_subscription(db, user.id) is not None:
            return None
        subscription = Subscription(user_id=user.id, paddle_subscription_id=paddle_id,
                                    current_period_start=start, current_period_end=end)
        db.add(subscription)
        record_step(db, "card_added", user_id=user.id)
    if status == SubscriptionStatus.TRIALING:
        user.trial_used_at = user.trial_used_at or utcnow()
        subscription.trial_ends_at = end
    subscription.status = status
    subscription.plan = "trial" if status == SubscriptionStatus.TRIALING else paid_plan
    subscription.paddle_customer_id = str(data.get("customer_id") or "")
    subscription.provider_updated_at = updated
    subscription.cancel_at_period_end = (data.get("scheduled_change") or {}).get("action") == "cancel"
    _start_period(db, subscription, start, end)
    return subscription


def _captured(data: dict, cents: int) -> bool:
    if data.get("status") != "completed" or data.get("currency_code") != "USD":
        return False
    totals = (data.get("details") or {}).get("totals") or {}
    if totals.get("total") != str(cents) or totals.get("credit", "0") != "0":
        return False
    payments = data.get("payments") or []
    return sum(int(p.get("amount") or 0) for p in payments if p.get("status") == "captured") == cents


def _apply_transaction(db: Session, settings: Settings, data: dict) -> str:
    """Return the linked subscription ID; each transaction can grant at most once."""
    transaction_id = str(data.get("id") or "")
    checkout = db.scalar(select(BillingCheckout).where(BillingCheckout.transaction_id == transaction_id))
    sub_id = str(data.get("subscription_id") or "")
    owner = checkout or (_checkout_for_subscription(db, sub_id) if sub_id else None)
    if owner is None:
        return ""
    lock_account(db, owner.user_id)
    db.refresh(owner)
    if checkout is not None and checkout.completed:
        return checkout.subscription_id or ""
    if db.get(BillingPayment, transaction_id) is not None:
        return sub_id
    price = _single_price(data)
    kind = checkout.kind if checkout is not None else plan_for_price(settings, price)
    if not price or (checkout and price != checkout.price_id):
        return ""
    if kind == "hours":
        if not _captured(data, plans.HOUR_PACK_PRICE_USD * 100):
            return ""
        if ledger.current_subscription(db, owner.user_id) is None:
            # Do not acknowledge a paid pack until it can be durably credited.
            raise ApiError(409, "no_subscription", "No active subscription for this purchase.")
        entry = ledger.purchase(db, owner.user_id, plans.HOUR_PACK_HOURS * 3600, "Hour pack (Paddle)")
        checkout.purchase_entry_id = entry.id
        checkout.completed = True
        return ""
    if not sub_id or not kind:
        return ""
    cents = 100 if kind == "trial" else plans.get_plan(kind).price_usd * 100
    if not _captured(data, cents):
        return ""
    start, end = _period(data, "billing_period")
    if start is None:
        return ""
    if checkout is not None:
        if checkout.subscription_id not in (None, sub_id):
            return ""
        checkout.subscription_id = sub_id
        checkout.completed = True
    db.add(BillingPayment(transaction_id=transaction_id, subscription_id=sub_id, price_id=price,
                          period_start=start, period_end=end))
    db.flush()
    return sub_id


def apply_transaction(db: Session, settings: Settings, data: dict) -> str:
    sub_id = _apply_transaction(db, settings, data)
    db.flush()
    pending = list(db.scalars(select(PaddleEvent).where(
        PaddleEvent.event_type == "adjustment.pending",
        PaddleEvent.payload["transaction_id"].as_string() == str(data.get("id") or ""))))
    for row in pending:
        apply_adjustment(db, row.payload)
    return sub_id


def remember_adjustment(db: Session, data: dict) -> None:
    identity = str(data.get("id") or "")
    if (data.get("status") != "approved" or data.get("action") not in {"refund", "chargeback"}
            or not re.fullmatch(r"adj_[a-zA-Z0-9]+", identity)):
        return
    if db.scalar(select(PaddleEvent.id).where(PaddleEvent.event_id == f"pending:{identity}")) is None:
        db.add(PaddleEvent(event_id=f"pending:{identity}", event_type="adjustment.pending", payload=data))
        db.flush()


def apply_adjustment(db: Session, data: dict) -> None:
    """Refunds and chargebacks cannot leave purchased entitlements spendable."""
    if data.get("status") != "approved" or data.get("action") not in {"refund", "chargeback"}:
        return
    identity = str(data.get("id") or "")
    if not re.fullmatch(r"adj_[a-zA-Z0-9]+", identity):
        return
    transaction_id = str(data.get("transaction_id") or "")
    checkout = db.scalar(select(BillingCheckout).where(BillingCheckout.transaction_id == transaction_id))
    payment = db.get(BillingPayment, transaction_id)
    owner = checkout or (_checkout_for_subscription(db, payment.subscription_id) if payment else None)
    if owner is None:
        return
    # The verification refund is intentional; chargebacks still revoke the trial.
    if checkout and checkout.kind == "trial" and data.get("action") == "refund":
        return
    lock_account(db, owner.user_id)
    if db.scalar(select(PaddleEvent.id).where(PaddleEvent.event_id == f"adjustment:{identity}")):
        return
    if checkout and not checkout.completed:
        return
    if checkout and checkout.kind == "hours":
        original = db.get(LedgerEntry, checkout.purchase_entry_id)
        if original is None or data.get("currency_code") != "USD":
            return
        cents = int((data.get("totals") or {}).get("total") or 0)
        cents = plans.HOUR_PACK_PRICE_USD * 100 if data.get("type") == "full" else cents
        if cents <= 0:
            return
        reversed_seconds = -int(db.scalar(select(func.coalesce(func.sum(LedgerEntry.seconds), 0)).where(
            LedgerEntry.refers_to == original.id, LedgerEntry.kind == LedgerKind.REVERSAL)) or 0)
        seconds = min(original.seconds - reversed_seconds,
                      (original.seconds * cents + plans.HOUR_PACK_PRICE_USD * 100 - 1)
                      // (plans.HOUR_PACK_PRICE_USD * 100))
        if seconds > 0:
            db.add(LedgerEntry(user_id=original.user_id, period_start=original.period_start,
                               kind=LedgerKind.REVERSAL, seconds=-seconds, refers_to=original.id,
                               reason=f"Paddle {data['action']} {identity}"))
    if payment:
        payment.revoked = True
        subscription = db.scalar(select(Subscription).where(
            Subscription.paddle_subscription_id == payment.subscription_id,
            Subscription.current_period_start == payment.period_start))
        if subscription:
            subscription.status = SubscriptionStatus.PAST_DUE
    db.add(PaddleEvent(event_id=f"adjustment:{identity}", event_type="adjustment.applied"))


async def paddle_request(request: Request, method: str, path: str, **kwargs) -> dict:
    settings = request.app.state.settings
    try:
        async with http_client(request) as client:
            response = await client.request(method, PADDLE_API[settings.paddle_environment] + path,
                                            headers={"Authorization": f"Bearer {settings.paddle_api_key}"}, **kwargs)
        if response.status_code >= 300:
            raise ApiError(502, "billing_unavailable", "Paddle could not complete the request. / Paddle недоступен.")
        return response.json()["data"]
    except (httpx.HTTPError, ValueError, KeyError) as error:
        raise ApiError(502, "billing_unavailable", "Paddle is unavailable. / Paddle недоступен.") from error


async def reconcile_subscription(request: Request, sub_id: str) -> None:
    if not re.fullmatch(r"sub_[a-zA-Z0-9]+", sub_id):
        return
    snapshot = await paddle_request(request, "GET", f"/subscriptions/{sub_id}")
    if snapshot.get("id") != sub_id:
        raise ApiError(502, "billing_unavailable", "Unexpected subscription.")
    with session_scope(request.app.state.session_factory) as db:
        apply_paddle_subscription(db, request.app.state.settings, snapshot)


async def refund_trial(request: Request, checkout_id: int) -> None:
    """Recover after a lost response by looking up the adjustment before creating another."""
    with session_scope(request.app.state.session_factory) as db:
        checkout = db.get(BillingCheckout, checkout_id)
        if not checkout.completed or checkout.kind != "trial" or checkout.refunded:
            return
        proof = db.get(BillingPayment, checkout.transaction_id)
        if proof is None or proof.revoked:
            return
        transaction_id = checkout.transaction_id
    adjustments = await paddle_request(request, "GET", "/adjustments", params={"transaction_id": transaction_id})
    existing = next((a for a in adjustments if a.get("action") == "refund" and a.get("type") == "full"
                     and a.get("transaction_id") == transaction_id), None)
    adjustment = existing or await paddle_request(request, "POST", "/adjustments", json={
        "action": "refund", "type": "full", "transaction_id": transaction_id,
        "reason": "Refund of the $1 Kvist trial verification payment",
    })
    with session_scope(request.app.state.session_factory) as db:
        checkout = db.get(BillingCheckout, checkout_id)
        lock_account(db, checkout.user_id)
        db.refresh(checkout)
        if (adjustment.get("transaction_id") != transaction_id or adjustment.get("action") != "refund"
                or adjustment.get("type") != "full"):
            raise ApiError(502, "billing_unavailable", "Unexpected refund.")
        checkout.refund_id = adjustment["id"]
        checkout.refunded = checkout.refunded or adjustment.get("status") == "approved"


def subscription_view(db: Session, user: User, now: datetime | None = None) -> dict[str, object]:
    now = now or utcnow()
    subscription = ledger.current_subscription(db, user.id)
    if subscription is None:
        return {"status": "none", "balance_seconds": 0}
    plan = plans.get_plan(subscription.plan)
    ends = subscription.trial_ends_at
    reminder = (
        subscription.status == SubscriptionStatus.TRIALING
        and not subscription.cancel_at_period_end
        and ends is not None
        and ends - now <= REMINDER_BEFORE
    )
    balance = ledger.balance_seconds(db, user.id, subscription.current_period_start)
    return {
        "status": subscription.status,
        "plan": plan.id,
        "converts_to": plan.converts_to,
        "price_after_trial_usd": plans.get_plan(plan.converts_to).price_usd if plan.converts_to else plan.price_usd,
        "plan_seconds": plan.seconds,
        "balance_seconds": balance,
        "purchased_seconds": ledger.purchased_seconds(db, user.id, subscription.current_period_start),
        "balance_minutes": ledger.minutes(max(balance, 0)),
        "period_start": subscription.current_period_start.isoformat(),
        "period_end": subscription.current_period_end.isoformat(),
        "trial_ends_at": ends.isoformat() if ends else None,
        "cancel_at_period_end": subscription.cancel_at_period_end,
        "trial_reminder": reminder,
    }


# -- background: reminders, and the dev stand-in for Paddle's renewals -----------------------


def queue_trial_reminders(db: Session, now: datetime | None = None) -> int:
    now = now or utcnow()
    due = db.scalars(
        select(Subscription).where(
            Subscription.status == SubscriptionStatus.TRIALING,
            Subscription.trial_reminder_sent_at.is_(None),
            Subscription.cancel_at_period_end.is_(False),
            Subscription.trial_ends_at <= now + REMINDER_BEFORE,
        )
    )
    count = 0
    for subscription in due:
        user = db.get(User, subscription.user_id)
        price = plans.get_plan(plans.TRIAL.converts_to).price_usd
        if user.locale == "ru":
            subject = f"Через 2 дня спишем ${price}"
            body = (f"Пробный период Kvist Cloud заканчивается через 2 дня. Потом спишем ${price} за месяц "
                    "тарифа Start. Отменить можно в настройках, это один клик.")
        else:
            subject = f"In 2 days we will charge ${price}"
            body = (f"Your Kvist Cloud trial ends in 2 days. Then we charge ${price} for a month of Start. "
                    "Cancelling is one click in Settings.")
        db.add(Outbox(user_id=user.id, kind="trial_reminder", subject=subject, body=body))
        subscription.trial_reminder_sent_at = now
        count += 1
    return count


def roll_dev_subscriptions(db: Session, now: datetime | None = None) -> None:
    """Billing off: do locally what Paddle's renewals would do, so periods still turn over."""
    now = now or utcnow()
    expired = db.scalars(
        select(Subscription).where(
            Subscription.status.in_(SubscriptionStatus.USABLE), Subscription.current_period_end <= now
        )
    )
    for subscription in expired:
        if subscription.cancel_at_period_end:
            subscription.status = SubscriptionStatus.CANCELLED
            continue
        if subscription.status == SubscriptionStatus.TRIALING:
            subscription.status = SubscriptionStatus.ACTIVE
            subscription.plan = plans.TRIAL.converts_to
        start = subscription.current_period_end
        _start_period(db, subscription, start, start + timedelta(days=30))


async def sweep_forever(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            with session_scope(app.state.session_factory) as db:
                queue_trial_reminders(db)
                if app.state.settings.dev_billing:
                    roll_dev_subscriptions(db)
        except Exception:  # noqa: BLE001 - the loop must survive one bad pass; the error is logged
            log.exception("billing sweep failed")


# -- API -------------------------------------------------------------------------------------


@router.get("/api/billing/config")
def billing_config(request: Request) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    return {
        "enabled": settings.billing_enabled,
        "dev": settings.dev_billing,
        "paddle": {
            "client_token": settings.paddle_client_token,
            "environment": settings.paddle_environment,
            "prices": {"start": settings.paddle_price_start, "pro": settings.paddle_price_pro,
                       "max": settings.paddle_price_max, "hours": settings.paddle_price_hours},
        }
        if settings.billing_enabled
        else None,
        "plans": plans.public_catalog(),
        "hour_pack": {"hours": plans.HOUR_PACK_HOURS, "price_usd": plans.HOUR_PACK_PRICE_USD},
    }


@router.get("/api/billing/subscription")
def get_subscription(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, object]:
    return subscription_view(db, user)


@router.get("/api/billing/eligibility")
def eligibility(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, bool]:
    pending = user.trial_used_at is None and db.scalar(select(BillingCheckout.id).where(
        BillingCheckout.user_id == user.id, BillingCheckout.kind == "trial", BillingCheckout.completed.is_(True)))
    return {"trial_eligible": trial_eligible(user), "trial_used": user.trial_used_at is not None,
            "trial_pending": bool(pending)}


def _valid_price(price: dict, kind: str) -> bool:
    trial = price.get("trial_period")
    unit = price.get("unit_price") or {}
    plan = plans.get_plan("start" if kind in {"trial", "hours"} else kind)
    amount = plans.HOUR_PACK_PRICE_USD if kind == "hours" else plan.price_usd
    if (price.get("status") != "active" or unit != {"amount": str(amount * 100), "currency_code": "USD"}
            or price.get("tax_mode") != "internal" or price.get("unit_price_overrides")):
        return False
    if kind == "hours":
        return not trial and price.get("billing_cycle") is None
    if price.get("billing_cycle") != {"interval": "month", "frequency": 1}:
        return False
    if kind != "trial":
        return trial is None
    return (isinstance(trial, dict) and trial.get("interval") == "day"
            and trial.get("frequency") == plans.TRIAL.trial_days and trial.get("requires_payment_method") is True
            and trial.get("unit_price") == {"amount": "100", "currency_code": "USD"}
            and not trial.get("unit_price_overrides"))


@router.post("/api/billing/checkout")
async def checkout(payload: CheckoutIn, request: Request, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict[str, str]:
    settings = request.app.state.settings
    if not settings.billing_enabled or not settings.paddle_api_key:
        raise ApiError(503, "billing_unavailable", "Billing is unavailable. / Оплата недоступна.")
    lock_account(db, user.id)
    db.refresh(user)
    if payload.plan == "trial" and not trial_eligible(user):
        raise ApiError(403, "trial_ineligible", "Trial is available once, for GitHub accounts older than one month. "
                       "/ Пробный период доступен один раз, с GitHub-аккаунтом старше месяца.")
    if payload.plan == "trial" and eligibility(user, db)["trial_pending"]:
        raise ApiError(409, "checkout_pending", "Verification is pending. / Проверка ещё не закончена.")
    active = ledger.current_subscription(db, user.id)
    if payload.plan == "hours" and active is None:
        raise ApiError(402, "no_subscription", "An active plan is required. / Нужен активный тариф.")
    if payload.plan != "hours" and active is not None:
        raise ApiError(409, "already_subscribed", "A subscription already exists. / Подписка уже есть.")
    pending = db.scalar(select(BillingCheckout).where(
        BillingCheckout.user_id == user.id, BillingCheckout.kind == payload.plan,
        BillingCheckout.completed.is_(False)).order_by(BillingCheckout.id.desc()))
    if pending is not None and pending.transaction_id:
        return {"transaction_id": pending.transaction_id}
    if pending is not None and utcnow() - pending.created_at < CHECKOUT_CREATE_TIMEOUT:
        raise ApiError(409, "checkout_pending", "Checkout is being created. / Оплата подготавливается.")
    if pending is not None:
        # The process creating it died before Paddle answered; no transaction exists to reuse.
        db.delete(pending)
    price_id = getattr(settings, f"paddle_price_{payload.plan}")
    if not re.fullmatch(r"pri_[a-zA-Z0-9]+", price_id):
        raise ApiError(503, "billing_unavailable", "Price is not configured. / Цена не настроена.")
    row = BillingCheckout(user_id=user.id, kind=payload.plan, price_id=price_id)
    db.add(row)
    db.commit()
    try:
        price = await paddle_request(request, "GET", f"/prices/{price_id}")
        if price.get("id") != price_id or not _valid_price(price, payload.plan):
            raise ApiError(503, "billing_unavailable", "Price configuration is invalid. / Неверная настройка цены.")
        transaction = await paddle_request(request, "POST", "/transactions", json={
            "items": [{"price_id": price_id, "quantity": 1}], "currency_code": "USD",
            "collection_mode": "automatic", "checkout": {"url": settings.public_url + "/start"},
        })
        transaction_id = str(transaction.get("id") or "")
        if not re.fullmatch(r"txn_[a-zA-Z0-9]+", transaction_id):
            raise ApiError(502, "billing_unavailable", "Unexpected transaction.")
    except ApiError:
        db.delete(row)
        db.commit()
        raise
    row.transaction_id = transaction_id
    db.commit()
    return {"transaction_id": transaction_id}


@router.post("/api/billing/checkout-opened")
def checkout_opened(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    record_step(db, "checkout_open", user_id=user.id, visitor_id=request.cookies.get("kc_visitor", ""))
    db.commit()
    return {"ok": True}


def _dev_only(request: Request) -> None:
    if not request.app.state.settings.dev_billing:
        raise ApiError(404, "not_found", "Not found. / Не найдено.")


@router.post("/api/billing/dev/skip-checkout")
def dev_skip_checkout(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The stub's "skip (dev)": the trial starts as if the card had been attached."""
    _dev_only(request)
    lock_account(db, user.id)
    db.refresh(user)
    if ledger.current_subscription(db, user.id) is None:
        if user.trial_used_at is not None:
            raise ApiError(403, "trial_used", "Trial already used. / Пробный период уже использован.")
        create_trial(db, user)
        record_step(db, "card_added", user_id=user.id)
    db.commit()
    return subscription_view(db, user)


@router.post("/api/billing/dev/buy-hours")
def dev_buy_hours(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _dev_only(request)
    lock_account(db, user.id)
    try:
        ledger.purchase(db, user.id, plans.HOUR_PACK_HOURS * 3600, f"{plans.HOUR_PACK_HOURS} h pack (dev)")
    except ledger.LedgerError as error:
        raise ApiError(402, "no_subscription", "Start the trial first. / Сначала начни пробный период.") from error
    db.commit()
    return subscription_view(db, user)


@router.post("/api/billing/cancel")
async def cancel_subscription(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """One click and one confirmation in the UI. Nothing more is charged; access lasts the period."""
    settings: Settings = request.app.state.settings
    subscription = ledger.current_subscription(db, user.id)
    if subscription is None:
        raise ApiError(404, "no_subscription", "There is nothing to cancel. / Отменять нечего.")
    if settings.billing_enabled and subscription.paddle_subscription_id:
        async with http_client(request) as client:
            response = await client.post(
                f"{PADDLE_API.get(settings.paddle_environment, PADDLE_API['sandbox'])}"
                f"/subscriptions/{subscription.paddle_subscription_id}/cancel",
                headers={"Authorization": f"Bearer {settings.paddle_api_key}"},
                json={"effective_from": "next_billing_period"},
            )
        if response.status_code >= 300:
            log.error("paddle refused the cancel of %s: HTTP %s", subscription.paddle_subscription_id,
                      response.status_code)
            raise ApiError(
                502,
                "billing_unavailable",
                "Could not cancel right now, try again in a minute. / Не вышло отменить, попробуй через минуту.",
            )
    subscription.cancel_at_period_end = True
    db.commit()
    return subscription_view(db, user)


@router.post("/billing/paddle/webhook")
async def paddle_webhook(request: Request) -> dict[str, bool]:
    settings = request.app.state.settings
    if not settings.billing_enabled:
        raise ApiError(404, "not_found", "Not found. / Не найдено.")
    body = await request.body()
    if len(body) > 1_000_000:
        raise ApiError(413, "bad_payload", "Payload too large.")
    if not verify_signature(settings.paddle_webhook_secret, request.headers.get("paddle-signature", ""), body):
        raise ApiError(400, "bad_signature", "Bad signature.")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ApiError(400, "bad_payload", "Not JSON.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise ApiError(400, "bad_payload", "Invalid envelope.")
    event_id, event_type, data = payload.get("event_id"), payload.get("event_type"), payload["data"]
    if not isinstance(event_id, str) or not event_id or len(event_id) > 100 or not isinstance(event_type, str):
        raise ApiError(400, "bad_payload", "Invalid event identity.")
    sub_id = ""
    checkout_id = None
    with session_scope(request.app.state.session_factory) as db:
        if db.scalar(select(PaddleEvent.id).where(PaddleEvent.event_id == event_id)) is not None:
            return {"ok": True}
        if event_type == "transaction.completed":
            sub_id = apply_transaction(db, settings, data)
            row = _checkout_for_subscription(db, sub_id) if sub_id else None
            if row and row.kind == "trial":
                checkout_id = row.id
        elif event_type.startswith("subscription."):
            sub_id = str(data.get("id") or "")
            row = _checkout_for_subscription(db, sub_id)
            if row is None or not row.completed:
                # transaction.completed will provision it, independent of delivery order.
                sub_id = ""
        elif event_type in {"adjustment.created", "adjustment.updated"}:
            remember_adjustment(db, data)
            apply_adjustment(db, data)
            row = db.scalar(select(BillingCheckout).where(
                BillingCheckout.transaction_id == str(data.get("transaction_id") or "")))
            if row and row.completed and row.kind == "trial" and data.get("action") == "refund":
                checkout_id = row.id
                sub_id = row.subscription_id or ""
    # HTTP calls happen after commit: no account/database lock spans a network wait.
    if checkout_id is not None:
        await refund_trial(request, checkout_id)
    if sub_id:
        await reconcile_subscription(request, sub_id)
    with session_scope(request.app.state.session_factory) as db:
        db.add(PaddleEvent(event_id=event_id, event_type=event_type[:100]))
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
    return {"ok": True}
