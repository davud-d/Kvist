# -*- coding: utf-8 -*-
"""Platega billing, behind BILLING_ENABLED (false by default).

The rule of this module: nothing the browser or a callback *says* is believed. A purchase exists only as
a ``BillingCheckout`` row our server created for a signed-in user, with the amount we chose. Platega's
callback is authenticated by two static headers and carries no signature, so it is treated as a doorbell:
the only thing taken from it is a transaction id, and the truth (status, amount, currency) is then fetched
from Platega with our own credentials and compared with the row. Only that comparison can credit anything.

Disabled: the card form is replaced by a stub with a "skip (dev)" button that creates the trial directly.
No request ever reaches Platega in that mode, nor in any test.
"""
from __future__ import annotations

import asyncio
import calendar
import hmac
import json
import logging
import re
import secrets
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import StrictInt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import ledger, plans
from .auth import app_http_client, current_user
from .config import Settings
from .db import get_db, lock_account, session_scope, utcnow
from .errors import ApiError
from .funnel import record_step
from .models import (
    BillingCheckout,
    CheckoutStatus,
    LedgerEntry,
    LedgerKind,
    Outbox,
    Subscription,
    SubscriptionStatus,
    User,
)
from .validation import InputModel

log = logging.getLogger("kvist_cloud.billing")
router = APIRouter()
UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
CALLBACK_MAX_BYTES = 64_000
# A checkout row without a transaction is a request to Platega still in flight; an older one is abandoned.
CHECKOUT_CREATE_TIMEOUT = timedelta(minutes=2)
# Platega's payment link lives 15 minutes; within this the same link is handed out again.
CHECKOUT_REUSE_WINDOW = timedelta(minutes=14)
MAX_NEW_CHECKOUTS_PER_HOUR = 5
# Pending checkouts the user's own "check my payment" asks Platega about, and how far back they reach.
SYNC_MAX_AGE = timedelta(days=2)
SYNC_MAX_COUNT = 5
# The background pass only chases payments that are still likely to arrive.
SWEEP_PAYMENT_AGE = timedelta(hours=1)
SWEEP_MAX_COUNT = 50
# Platega takes one-off payments, so a plan is renewed by paying again; this late in the period it may be.
RENEW_WINDOW = timedelta(days=3)
REMINDER_BEFORE = timedelta(days=2)
SWEEP_SECONDS = 300.0


class CheckoutIn(InputModel):
    plan: Literal["trial", "start", "pro", "max", "remote", "hours"] = "trial"
    # One of the server's own methods (/api/billing/config); left out, the first one is used.
    method: StrictInt | None = None


def _unavailable() -> ApiError:
    return ApiError(502, "billing_unavailable", "Payments are unavailable. / Оплата недоступна.")


# -- talking to Platega -------------------------------------------------------------------------


def callback_authentic(settings: Settings, merchant_id: str, secret: str) -> bool:
    """Platega's only proof of origin is the pair of headers it sends; compared in constant time."""
    if not settings.platega_merchant_id or not settings.platega_secret:
        return False
    merchant_ok = hmac.compare_digest(merchant_id.encode(), settings.platega_merchant_id.encode())
    secret_ok = hmac.compare_digest(secret.encode(), settings.platega_secret.encode())
    return merchant_ok and secret_ok


async def platega_request(app: FastAPI, method: str, path: str, **kwargs) -> dict:
    settings: Settings = app.state.settings
    headers = {"X-MerchantId": settings.platega_merchant_id, "X-Secret": settings.platega_secret}
    try:
        async with app_http_client(app) as client:
            response = await client.request(method, settings.platega_base_url + path, headers=headers, **kwargs)
    except httpx.HTTPError as error:
        log.warning("platega %s failed: %s", method, type(error).__name__)
        raise _unavailable() from error
    if response.status_code >= 300:
        # Only the code: a body could echo what we sent, credentials included.
        log.error("platega %s answered HTTP %s", method, response.status_code)
        raise _unavailable()
    try:
        data = response.json()
    except ValueError as error:
        raise _unavailable() from error
    if not isinstance(data, dict):
        raise _unavailable()
    return data


def _money(raw: object) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value.quantize(Decimal("0.01")) if value.is_finite() else None


def remote_matches(checkout: BillingCheckout, remote: dict) -> bool:
    """Is Platega's own record of the transaction the purchase our server created?"""
    details = remote.get("paymentDetails")
    if not isinstance(details, dict) or str(remote.get("id") or "").lower() != (checkout.transaction_id or "").lower():
        return False
    if str(details.get("currency") or "").upper() != checkout.currency:
        return False
    if _money(details.get("amount")) != _money(checkout.amount):
        return False
    # The payload is our own random token; Platega may omit it, but never may it differ.
    return remote.get("payload") in (None, "", checkout.token)


# -- subscription state ----------------------------------------------------------------------


def _start_period(db: Session, subscription: Subscription, start: datetime, end: datetime) -> None:
    """A new billing period begins: its plan hours are granted once."""
    subscription.current_period_start = start
    subscription.current_period_end = end
    db.flush()
    plan = plans.get_plan(subscription.plan)
    if plan.metered:
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


def _renewal_queued(db: Session, user_id: int, now: datetime) -> bool:
    return db.scalar(select(Subscription.id).where(
        Subscription.user_id == user_id, Subscription.status.in_(SubscriptionStatus.USABLE),
        Subscription.current_period_start > now)) is not None


def plan_start(db: Session, user_id: int, now: datetime) -> datetime | None:
    """When a plan bought now would begin, or None while the current one has too long to run."""
    if _renewal_queued(db, user_id, now):
        return None
    current = ledger.current_subscription(db, user_id)
    if current is None or current.plan == plans.TRIAL.id:
        return now
    if current.current_period_end - now <= RENEW_WINDOW:
        return current.current_period_end
    return None


def _grant_plan(db: Session, checkout: BillingCheckout, now: datetime) -> bool:
    start = plan_start(db, checkout.user_id, now)
    if start is None:
        return False
    current = ledger.current_subscription(db, checkout.user_id)
    if current is not None and current.plan == plans.TRIAL.id:
        # Paying replaces the trial; its unused hours do not carry over.
        current.status = SubscriptionStatus.CANCELLED
        current.current_period_end = now
    end = start + timedelta(days=plans.get_plan(checkout.kind).period_days)
    subscription = Subscription(user_id=checkout.user_id, plan=checkout.kind, status=SubscriptionStatus.ACTIVE,
                                current_period_start=start, current_period_end=end)
    db.add(subscription)
    _start_period(db, subscription, start, end)
    checkout.subscription_row_id = subscription.id
    record_step(db, "card_added", user_id=checkout.user_id)
    return True


def _grant_hours(db: Session, checkout: BillingCheckout) -> bool:
    try:
        entry = ledger.purchase(db, checkout.user_id, plans.HOUR_PACK_HOURS * 3600, "Hour pack (Platega)")
    except ledger.LedgerError:
        return False
    checkout.ledger_entry_id = entry.id
    return True


def fulfill(db: Session, checkout: BillingCheckout, now: datetime) -> None:
    """Platega confirmed exactly what we asked for: apply it, once."""
    applied = _grant_hours(db, checkout) if checkout.kind == "hours" else _grant_plan(db, checkout, now)
    if not applied:
        # The money arrived but the purchase no longer fits (the plan ended, or one was bought twice).
        checkout.status = CheckoutStatus.NEEDS_REVIEW
        log.error("billing: confirmed payment cannot be applied, checkout %s needs an operator", checkout.id)
        return
    checkout.status = CheckoutStatus.PAID
    checkout.paid_at = now


def revoke(db: Session, checkout: BillingCheckout) -> None:
    """A paid purchase was cancelled or charged back: take back what it gave."""
    if checkout.ledger_entry_id is not None:
        original = db.get(LedgerEntry, checkout.ledger_entry_id)
        db.add(LedgerEntry(user_id=original.user_id, period_start=original.period_start, kind=LedgerKind.REVERSAL,
                           seconds=-original.seconds, refers_to=original.id,
                           reason=f"Platega reversal of checkout {checkout.id}"))
    if checkout.subscription_row_id is not None:
        db.get(Subscription, checkout.subscription_row_id).status = SubscriptionStatus.CANCELLED
    checkout.status = CheckoutStatus.REVOKED
    log.warning("billing: checkout %s revoked", checkout.id)


def apply_remote(db: Session, checkout: BillingCheckout, remote: dict, now: datetime | None = None) -> None:
    """Act on Platega's own record of a transaction; idempotent, so duplicates and any order are harmless."""
    now = now or utcnow()
    status = remote.get("status")
    if status == "CONFIRMED":
        if checkout.status != CheckoutStatus.PENDING:
            return
        if not remote_matches(checkout, remote):
            checkout.status = CheckoutStatus.MISMATCH
            log.error("billing: confirmed payment does not match checkout %s", checkout.id)
            return
        fulfill(db, checkout, now)
    elif status in {"CANCELED", "CHARGEBACKED"} and checkout.status == CheckoutStatus.PAID:
        revoke(db, checkout)


async def sync_transaction(app: FastAPI, transaction_id: str) -> None:
    """Ask Platega what became of one of our transactions and apply the answer."""
    with session_scope(app.state.session_factory) as db:
        checkout = db.scalar(select(BillingCheckout).where(BillingCheckout.transaction_id == transaction_id.lower()))
        if checkout is None:
            log.warning("billing: notification for a transaction we never created")
            return
        checkout_id = checkout.id
    remote = await platega_request(app, "GET", f"/transaction/{transaction_id}")
    with session_scope(app.state.session_factory) as db:
        checkout = db.get(BillingCheckout, checkout_id)
        lock_account(db, checkout.user_id)
        db.refresh(checkout)
        apply_remote(db, checkout, remote)


async def sync_pending(app: FastAPI, user_id: int | None = None) -> None:
    """Look up unfinished checkouts (one person's, or recent ones of everybody) without waiting for a callback."""
    now = utcnow()
    query = select(BillingCheckout.transaction_id).where(
        BillingCheckout.status == CheckoutStatus.PENDING, BillingCheckout.transaction_id.is_not(None))
    if user_id is not None:
        query = query.where(BillingCheckout.user_id == user_id, BillingCheckout.created_at > now - SYNC_MAX_AGE)
        limit = SYNC_MAX_COUNT
    else:
        query = query.where(BillingCheckout.created_at > now - SWEEP_PAYMENT_AGE)
        limit = SWEEP_MAX_COUNT
    with session_scope(app.state.session_factory) as db:
        ids = list(db.scalars(query.order_by(BillingCheckout.id.desc()).limit(limit)))
    for transaction_id in ids:
        try:
            await sync_transaction(app, transaction_id)
        except ApiError:
            log.warning("billing: could not look up a pending payment, will retry")


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
        "can_renew": plan_start(db, user.id, now) is not None and plan.id != plans.TRIAL.id,
        "own_machine": plan.own_machine,
        "unmetered": not plan.metered,
    }


# -- background: reminders, and the dev stand-in for renewals -------------------------------------


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
        if user.locale == "ru":
            subject = "Через 2 дня закончится пробный период"
            body = ("Пробный период Kvist Cloud заканчивается через 2 дня. Чтобы продолжить, выбери тариф в "
                    "настройках: карта сама ничего не спишет, платёж только когда ты его подтвердишь.")
        else:
            subject = "Your trial ends in 2 days"
            body = ("Your Kvist Cloud trial ends in 2 days. To keep going, pick a plan in Settings: "
                    "nothing is charged automatically, only a payment you confirm.")
        db.add(Outbox(user_id=user.id, kind="trial_reminder", subject=subject, body=body))
        subscription.trial_reminder_sent_at = now
        count += 1
    return count


def roll_dev_subscriptions(db: Session, now: datetime | None = None) -> None:
    """Billing off: let periods turn over locally, so the dev stand-in behaves like a renewing plan."""
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
        _start_period(db, subscription, start, start + timedelta(days=plans.get_plan(subscription.plan).period_days))


async def sweep_forever(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            with session_scope(app.state.session_factory) as db:
                queue_trial_reminders(db)
                if app.state.settings.dev_billing:
                    roll_dev_subscriptions(db)
            if app.state.settings.billing_enabled:
                await sync_pending(app)
        except Exception:  # noqa: BLE001 - the loop must survive one bad pass; the error is logged
            log.exception("billing sweep failed")


# -- API -------------------------------------------------------------------------------------


@router.get("/api/billing/config")
def billing_config(request: Request) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    return {
        "enabled": settings.billing_enabled,
        "dev": settings.dev_billing,
        # What is really charged; the plan table's dollars are only the catalogue.
        "charge": {"currency": settings.platega_currency, "methods": list(settings.platega_payment_methods),
                   "prices": {key: str(price) for key, price in settings.platega_prices.items()}}
        if settings.billing_enabled
        else None,
        "plans": plans.public_catalog(),
        "hour_pack": {"hours": plans.HOUR_PACK_HOURS, "price_usd": plans.HOUR_PACK_PRICE_USD},
        "remote": plans.remote_offer(),
    }


@router.get("/api/billing/subscription")
def get_subscription(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, object]:
    return subscription_view(db, user)


@router.get("/api/billing/eligibility")
def eligibility(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, bool]:
    pending = db.scalar(select(BillingCheckout.id).where(
        BillingCheckout.user_id == user.id, BillingCheckout.status == CheckoutStatus.PENDING,
        BillingCheckout.transaction_id.is_not(None), BillingCheckout.created_at > utcnow() - SYNC_MAX_AGE))
    return {"trial_eligible": trial_eligible(user), "trial_used": user.trial_used_at is not None,
            "payment_pending": pending is not None}


@router.post("/api/billing/sync")
async def sync_my_payments(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The page asks this after the person comes back from paying; the answer comes from Platega, not the page."""
    if request.app.state.settings.billing_enabled:
        user_id = user.id
        await sync_pending(request.app, user_id)
        db.expire_all()
    return subscription_view(db, user)


def _check_purchase_allowed(db: Session, user: User, plan: str) -> None:
    now = utcnow()
    if plan == "hours":
        current = ledger.current_subscription(db, user.id)
        if current is None:
            raise ApiError(402, "no_subscription", "An active plan is required. / Нужен активный тариф.")
        if not plans.get_plan(current.plan).metered:
            raise ApiError(409, "hours_not_counted",
                           "Your plan has no hours to add. / В твоём тарифе часы не считаются.")
    elif plan_start(db, user.id, now) is None:
        raise ApiError(409, "already_subscribed", "A subscription already exists. / Подписка уже есть.")


def _transaction_body(settings: Settings, user: User, checkout: BillingCheckout) -> dict[str, object]:
    page = "/dashboard" if checkout.kind == "hours" else "/start"
    label = ("10 hour pack" if checkout.kind == "hours"
             else f"{checkout.kind.capitalize()} plan, {plans.get_plan(checkout.kind).period_days} days")
    return {
        "paymentMethod": checkout.payment_method,
        "paymentDetails": {"amount": float(Decimal(checkout.amount)), "currency": checkout.currency},
        "description": f"Kvist Cloud: {label}",
        "return": settings.public_url + page,
        "failedUrl": settings.public_url + page + "?payment=failed",
        "payload": checkout.token,
        # Required by Platega; GitHub may hide the real address, then a stable id on a noreply domain stands in.
        "metadata": {"user_email": user.email or f"{user.github_id}@users.noreply.github.com",
                     "userId": str(user.id)},
    }


def _new_checkout_allowed(db: Session, user: User, plan: str, method: int) -> str | None:
    """A payment link still good to use, else None; raises when a new one may not be made yet."""
    now = utcnow()
    pending = list(db.scalars(select(BillingCheckout).where(
        BillingCheckout.user_id == user.id, BillingCheckout.kind == plan, BillingCheckout.payment_method == method,
        BillingCheckout.status == CheckoutStatus.PENDING).order_by(BillingCheckout.id.desc())))
    for row in pending:
        if row.transaction_id and row.pay_url and now - row.created_at < CHECKOUT_REUSE_WINDOW:
            return row.pay_url
        if not row.transaction_id and now - row.created_at < CHECKOUT_CREATE_TIMEOUT:
            raise ApiError(409, "checkout_pending", "Checkout is being created. / Оплата подготавливается.")
        if not row.transaction_id:
            # The request that made it died before Platega answered; no payment can belong to it.
            db.delete(row)
    recent = db.scalar(select(func.count()).select_from(BillingCheckout).where(
        BillingCheckout.user_id == user.id, BillingCheckout.created_at > now - timedelta(hours=1)))
    if recent >= MAX_NEW_CHECKOUTS_PER_HOUR:
        raise ApiError(429, "too_many_checkouts", "Too many payment attempts, try again later. "
                       "/ Слишком много попыток оплаты, попробуй позже.")
    return None


@router.post("/api/billing/checkout")
async def checkout(payload: CheckoutIn, request: Request, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    if not settings.billing_enabled:
        raise ApiError(503, "billing_unavailable", "Billing is unavailable. / Оплата недоступна.")
    lock_account(db, user.id)
    db.refresh(user)
    if payload.plan == "trial":
        # Nothing to pay: the trial is the one thing the server grants without Platega, and only once.
        if not trial_eligible(user):
            raise ApiError(403, "trial_ineligible", "Trial is available once, for GitHub accounts older than one "
                           "month. / Пробный период доступен один раз, с GitHub-аккаунтом старше месяца.")
        if ledger.current_subscription(db, user.id) is not None:
            raise ApiError(409, "already_subscribed", "A subscription already exists. / Подписка уже есть.")
        create_trial(db, user)
        record_step(db, "card_added", user_id=user.id)
        db.commit()
        return {"started": "trial"}
    _check_purchase_allowed(db, user, payload.plan)
    method = payload.method if payload.method is not None else settings.platega_payment_methods[0]
    if method not in settings.platega_payment_methods:
        raise ApiError(422, "bad_method", "This payment method is not offered. / Такого способа оплаты нет.")
    link = _new_checkout_allowed(db, user, payload.plan, method)
    if link is not None:
        return {"redirect": link}
    amount = settings.platega_prices.get(payload.plan)
    if amount is None:
        raise ApiError(503, "billing_unavailable", "Price is not configured. / Цена не настроена.")
    row = BillingCheckout(user_id=user.id, kind=payload.plan, amount=str(amount), currency=settings.platega_currency,
                          payment_method=method, token=secrets.token_hex(24))
    db.add(row)
    db.commit()
    try:
        created = await platega_request(request.app, "POST", "/transaction/process",
                                        json=_transaction_body(settings, user, row))
        transaction_id, url = str(created.get("transactionId") or ""), str(created.get("redirect") or "")
        if not UUID_PATTERN.fullmatch(transaction_id) or not url.startswith("https://"):
            log.error("billing: unexpected answer to a transaction request")
            raise _unavailable()
    except ApiError:
        db.delete(row)
        db.commit()
        raise
    row.transaction_id, row.pay_url = transaction_id.lower(), url[:500]
    db.commit()
    record_step(db, "checkout_open", user_id=user.id, visitor_id=request.cookies.get("kc_visitor", ""))
    db.commit()
    return {"redirect": url}


def _dev_only(request: Request) -> None:
    if not request.app.state.settings.dev_billing:
        raise ApiError(404, "not_found", "Not found. / Не найдено.")


@router.post("/api/billing/checkout-opened")
def checkout_opened(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    record_step(db, "checkout_open", user_id=user.id, visitor_id=request.cookies.get("kc_visitor", ""))
    db.commit()
    return {"ok": True}


@router.post("/api/billing/dev/skip-checkout")
def dev_skip_checkout(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The stub's "skip (dev)": the trial starts as if the payment had gone through."""
    _dev_only(request)
    lock_account(db, user.id)
    db.refresh(user)
    if ledger.current_subscription(db, user.id) is None:
        if user.trial_used_at is not None:
            raise ApiError(403, "trial_used", "Trial already used. / Пробный период уже использован.")
        if request.app.state.settings.github_client_id and not trial_eligible(user):
            raise ApiError(403, "trial_ineligible", "Trial is available once, for GitHub accounts older than one "
                           "month. / Пробный период доступен один раз, с GitHub-аккаунтом старше месяца.")
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
def cancel_subscription(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Nothing renews by itself, so there is nothing to stop at the provider; this only marks the intent."""
    subscription = ledger.current_subscription(db, user.id)
    if subscription is None:
        raise ApiError(404, "no_subscription", "There is nothing to cancel. / Отменять нечего.")
    subscription.cancel_at_period_end = True
    db.commit()
    return subscription_view(db, user)


@router.post("/billing/platega/callback")
async def platega_callback(request: Request) -> dict[str, bool]:
    """Platega's doorbell. Only the transaction id is read from it; Platega is then asked what happened."""
    settings: Settings = request.app.state.settings
    if not settings.billing_enabled:
        raise ApiError(404, "not_found", "Not found. / Не найдено.")
    if not callback_authentic(settings, request.headers.get("x-merchantid", ""), request.headers.get("x-secret", "")):
        raise ApiError(401, "bad_credentials", "Access denied.")
    body = await request.body()
    if len(body) > CALLBACK_MAX_BYTES:
        raise ApiError(413, "bad_payload", "Payload too large.")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ApiError(400, "bad_payload", "Not JSON.") from error
    transaction_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(transaction_id, str) or not UUID_PATTERN.fullmatch(transaction_id):
        raise ApiError(400, "bad_payload", "Invalid transaction id.")
    # A failure here is a non-200 for Platega, which then retries the delivery.
    await sync_transaction(request.app, transaction_id)
    return {"ok": True}
