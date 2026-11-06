# -*- coding: utf-8 -*-
"""Paddle billing, behind BILLING_ENABLED (false by default).

Enabled: the browser opens the Paddle.js overlay; Paddle tells us what happened through signed
webhooks, which are the only thing that creates or changes a subscription.
Disabled: the overlay is replaced by a stub with the same form and a "skip (dev)" button, which
creates the trial directly. No request ever reaches Paddle in that mode, nor in any test.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import ledger, plans
from .auth import current_user, http_client
from .config import Settings
from .db import get_db, session_scope, utcnow
from .errors import ApiError
from .funnel import record_step
from .models import Outbox, PaddleEvent, Subscription, SubscriptionStatus, User

log = logging.getLogger("kvist_cloud.billing")
router = APIRouter()
SIGNATURE_TOLERANCE_SECONDS = 300
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


class CheckoutIn(BaseModel):
    plan: str = "trial"


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
    if not stamp.isdigit() or not digests:
        return False
    current = time.time() if now is None else now
    if abs(current - int(stamp)) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{stamp}:".encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, digest) for digest in digests)


def sign(secret: str, body: bytes, stamp: int) -> str:
    """What Paddle sends; used by the tests and nowhere else."""
    digest = hmac.new(secret.encode(), f"{stamp}:".encode() + body, hashlib.sha256).hexdigest()
    return f"ts={stamp};h1={digest}"


# -- subscription state ----------------------------------------------------------------------


def _parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def plan_for_price(settings: Settings, price_id: str) -> str:
    prices = {settings.paddle_price_start: "start", settings.paddle_price_pro: "pro", settings.paddle_price_max: "max"}
    return prices.get(price_id, "")


def _start_period(db: Session, subscription: Subscription, start: datetime, end: datetime) -> None:
    """A new billing period begins: its plan hours are granted once."""
    subscription.current_period_start = start
    subscription.current_period_end = end
    db.flush()
    plan = plans.get_plan(subscription.plan)
    ledger.grant_period(db, subscription, plan.seconds, f"{plan.id} plan hours")


def create_trial(db: Session, user: User, now: datetime | None = None) -> Subscription:
    now = now or utcnow()
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


def apply_paddle_subscription(db: Session, settings: Settings, data: dict) -> Subscription | None:
    """Create or update our row from Paddle's subscription object."""
    paddle_id = str(data.get("id", ""))
    custom = data.get("custom_data") or {}
    subscription = db.scalars(select(Subscription).where(Subscription.paddle_subscription_id == paddle_id)).first()
    if subscription is None:
        user = db.get(User, int(custom.get("user_id") or 0))
        if user is None:
            log.warning("paddle subscription %s names no user of ours", paddle_id)
            return None
        subscription = Subscription(user_id=user.id, paddle_subscription_id=paddle_id, plan="", status="")
        db.add(subscription)
        record_step(db, "card_added", user_id=user.id)
    status = PADDLE_STATUS.get(str(data.get("status", "")), SubscriptionStatus.PAST_DUE)
    items = data.get("items") or [{}]
    paid_plan = plan_for_price(settings, str(((items[0] or {}).get("price") or {}).get("id", ""))) or "start"
    subscription.status = status
    subscription.plan = plans.TRIAL.id if status == SubscriptionStatus.TRIALING else paid_plan
    subscription.paddle_customer_id = str(data.get("customer_id", ""))
    period = data.get("current_billing_period") or {}
    start, end = _parse_time(period.get("starts_at")), _parse_time(period.get("ends_at"))
    if status == SubscriptionStatus.TRIALING:
        subscription.trial_ends_at = end
    scheduled = data.get("scheduled_change") or {}
    subscription.cancel_at_period_end = scheduled.get("action") == "cancel"
    if start is not None and end is not None and start != subscription.current_period_start:
        _start_period(db, subscription, start, end)
    db.flush()
    return subscription


def apply_transaction(db: Session, data: dict) -> None:
    """A one-off purchase of hours."""
    custom = data.get("custom_data") or {}
    if custom.get("kind") != "hours":
        return
    user = db.get(User, int(custom.get("user_id") or 0))
    if user is None:
        return
    ledger.purchase(db, user.id, plans.HOUR_PACK_HOURS * 3600, f"{plans.HOUR_PACK_HOURS} h pack (Paddle)")


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
                if not app.state.settings.billing_enabled:
                    roll_dev_subscriptions(db)
        except Exception:  # noqa: BLE001 - the loop must survive one bad pass; the error is logged
            log.exception("billing sweep failed")


# -- API -------------------------------------------------------------------------------------


@router.get("/api/billing/config")
def billing_config(request: Request) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    return {
        "enabled": settings.billing_enabled,
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


@router.post("/api/billing/checkout-opened")
def checkout_opened(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    record_step(db, "checkout_open", user_id=user.id, visitor_id=request.cookies.get("kc_visitor", ""))
    db.commit()
    return {"ok": True}


def _dev_only(request: Request) -> None:
    if request.app.state.settings.billing_enabled:
        raise ApiError(404, "not_found", "Not found. / Не найдено.")


@router.post("/api/billing/dev/skip-checkout")
def dev_skip_checkout(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The stub's "skip (dev)": the trial starts as if the card had been attached."""
    _dev_only(request)
    if ledger.current_subscription(db, user.id) is None:
        create_trial(db, user)
        record_step(db, "card_added", user_id=user.id)
    db.commit()
    return subscription_view(db, user)


@router.post("/api/billing/dev/buy-hours")
def dev_buy_hours(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _dev_only(request)
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
    settings: Settings = request.app.state.settings
    if not settings.billing_enabled:
        raise ApiError(404, "not_found", "Not found. / Не найдено.")
    body = await request.body()
    if not verify_signature(settings.paddle_webhook_secret, request.headers.get("paddle-signature", ""), body):
        raise ApiError(400, "bad_signature", "Bad signature.")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise ApiError(400, "bad_payload", "Not JSON.") from error
    event_id = str(payload.get("event_id", ""))
    event_type = str(payload.get("event_type", ""))
    data = payload.get("data") or {}
    with session_scope(request.app.state.session_factory) as db:
        if db.scalars(select(PaddleEvent).where(PaddleEvent.event_id == event_id)).first() is not None:
            return {"ok": True}
        db.add(PaddleEvent(event_id=event_id, event_type=event_type[:100]))
        try:
            db.flush()
        except IntegrityError:
            # The same event delivered twice at once: the other delivery handles it.
            db.rollback()
            return {"ok": True}
        if event_type.startswith("subscription."):
            apply_paddle_subscription(db, settings, data)
        elif event_type == "transaction.completed":
            apply_transaction(db, data)
    return {"ok": True}
