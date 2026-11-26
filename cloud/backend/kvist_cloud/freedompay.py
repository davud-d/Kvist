# -*- coding: utf-8 -*-
"""Freedom Pay's hosted payment page (Merchant API, https://docs.freedompay.kz), behind FREEDOMPAY_ENABLED.

The person pays on Freedom Pay's own page, opened in the system browser; there Freedom Pay shows its cards and, where
the merchant has them connected, Apple Pay and Google Pay. (Its documentation shows no way to hand a wallet token to a
merchant's server without a PCI DSS certificate, which is why the phone apps' own wallet sheets go through wallet.py.)

The rule is billing.py's: a purchase exists only as a ``BillingCheckout`` row our server made, with the amount we chose.
Freedom Pay signs its callbacks (MD5 over the script name, the sorted values and the secret key), so a callback is
checked by its signature and then against the row (order, payment id, amount, currency) before it credits anything;
a missed callback is made up for by asking Freedom Pay for the status.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import xml.etree.ElementTree as ET
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import parse_qsl

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import billing
from .auth import app_http_client, current_user
from .config import FreedomPaySettings
from .db import get_db, lock_account, session_scope, utcnow
from .errors import ApiError
from .models import BillingCheckout, CheckoutStatus, User
from .validation import InputModel

log = logging.getLogger("kvist_cloud.freedompay")
router = APIRouter()

CALLBACK_MAX_BYTES = 64_000
# Freedom Pay's payment page lives long enough for the same link to be handed out again within this.
REUSE_WINDOW = timedelta(minutes=14)
SYNC_MAX_AGE = timedelta(days=2)
SYNC_MAX_COUNT = 5
OK_ANSWER = '<?xml version="1.0" encoding="utf-8"?><response><pg_status>ok</pg_status></response>'


class CheckoutIn(InputModel):
    plan: Literal["start", "pro", "max", "hours", "searches"]


def _unavailable() -> ApiError:
    return ApiError(502, "billing_unavailable", "Payments are unavailable. / Оплата недоступна.")


def _settings(request: Request) -> FreedomPaySettings:
    settings = request.app.state.settings.freedompay
    if settings is None:
        raise ApiError(503, "billing_unavailable", "Billing is unavailable. / Оплата недоступна.")
    return settings


# -- the signature ---------------------------------------------------------------------------------------------


def sign(script: str, params: dict[str, str], secret: str) -> str:
    """MD5 of: the script name (the last path segment of the URL), every value but pg_sig in the order of the
    parameter names, the secret key; joined with ';' (docs.freedompay.kz, Merchant API overview)."""
    values = [params[name] for name in sorted(params) if name != "pg_sig"]
    return hashlib.md5(";".join([script, *values, secret]).encode()).hexdigest()  # noqa: S324 - Freedom Pay's scheme


def signature_valid(script: str, params: dict[str, str], secret: str) -> bool:
    given = params.get("pg_sig", "")
    return bool(given) and hmac.compare_digest(sign(script, params, secret).encode(), given.lower().encode())


def _answer(text: str) -> dict[str, str]:
    """A Freedom Pay XML answer as {tag: text}; anything that is not that XML is an unavailable provider."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise _unavailable() from error
    return {child.tag: (child.text or "") for child in root}


async def _call(app: FastAPI, script: str, params: dict[str, str]) -> dict[str, str]:
    """One signed request; the answer's own signature is checked, and an error answer (HTTP 200, unsigned) raises."""
    fp: FreedomPaySettings = app.state.settings.freedompay
    body = {**params, "pg_merchant_id": fp.merchant_id, "pg_salt": secrets.token_hex(8)}
    body["pg_sig"] = sign(script, body, fp.secret)
    try:
        async with app_http_client(app) as client:
            response = await client.post(f"{fp.base_url}/{script}", data=body)
    except httpx.HTTPError as error:
        log.warning("freedompay %s failed: %s", script, type(error).__name__)
        raise _unavailable() from error
    if response.status_code != 200:
        # Anything but 200 means the result is unknown (the docs): never a reason to credit or to retry blindly.
        log.error("freedompay %s answered HTTP %s", script, response.status_code)
        raise _unavailable()
    answer = _answer(response.text)
    if answer.get("pg_status") != "ok":
        log.warning("freedompay %s refused (%s)", script, answer.get("pg_error_code") or answer.get("pg_failure_code"))
        raise _unavailable()
    if not signature_valid(script, answer, fp.secret):
        log.error("freedompay %s answered with a bad signature", script)
        raise _unavailable()
    return answer


def _amount(raw: object) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value.quantize(Decimal("0.01")) if value.is_finite() else None


def matches(checkout: BillingCheckout, fields: dict[str, str]) -> bool:
    """Is Freedom Pay's record of the payment the purchase our server created?"""
    return (fields.get("pg_order_id") == checkout.token
            and fields.get("pg_payment_id") == checkout.transaction_id
            and fields.get("pg_currency", "").upper() == checkout.currency
            and _amount(fields.get("pg_amount")) == _amount(checkout.amount))


def apply_result(db: Session, checkout: BillingCheckout, fields: dict[str, str]) -> None:
    """Act on a signed callback or a status answer; idempotent, so duplicates and any order are harmless."""
    if checkout.status != CheckoutStatus.PENDING:
        return
    paid = fields.get("pg_result") == "1" or fields.get("pg_payment_status") == "success"
    if not paid:
        return
    if not matches(checkout, fields):
        checkout.status = CheckoutStatus.MISMATCH
        log.error("freedompay: a paid payment does not match checkout %s", checkout.id)
        return
    billing.fulfill(db, checkout, utcnow())


# -- API ------------------------------------------------------------------------------------------------------


def _reusable(db: Session, user: User, plan: str) -> BillingCheckout | None:
    row = db.scalar(select(BillingCheckout).where(
        BillingCheckout.user_id == user.id, BillingCheckout.kind == plan,
        BillingCheckout.payment_method == billing.FREEDOMPAY_METHOD, BillingCheckout.status == CheckoutStatus.PENDING,
        BillingCheckout.pay_url != "").order_by(BillingCheckout.id.desc()))
    return row if row is not None and utcnow() - row.created_at < REUSE_WINDOW else None


@router.post("/api/freedompay/checkout")
async def checkout(payload: CheckoutIn, request: Request, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict[str, str]:
    fp = _settings(request)
    if payload.plan not in fp.prices:
        raise ApiError(503, "billing_unavailable", "Price is not configured. / Цена не настроена.")
    lock_account(db, user.id)
    db.refresh(user)
    billing._check_purchase_allowed(db, user, payload.plan)
    link = _reusable(db, user, payload.plan)
    if link is not None:
        return {"redirect": link.pay_url}
    billing._new_checkout_allowed(db, user, payload.plan, billing.FREEDOMPAY_METHOD)
    row = BillingCheckout(user_id=user.id, kind=payload.plan, amount=str(fp.prices[payload.plan]),
                          currency=fp.currency, payment_method=billing.FREEDOMPAY_METHOD, token=secrets.token_hex(24))
    db.add(row)
    db.commit()
    public = request.app.state.settings.public_url
    params = {
        "pg_order_id": row.token, "pg_amount": row.amount, "pg_currency": row.currency,
        "pg_description": f"Kvist Cloud: {row.kind}",
        "pg_result_url": f"{public}/billing/freedompay/result", "pg_request_method": "POST",
        "pg_success_url": f"{public}/m/", "pg_failure_url": f"{public}/m/?payment=failed",
        "pg_language": user.locale if user.locale in ("ru", "en") else "ru",
        **({"pg_testing_mode": "1"} if fp.testing else {}),
    }
    try:
        answer = await _call(request.app, "init_payment.php", params)
        link_url, payment_id = answer.get("pg_redirect_url", ""), answer.get("pg_payment_id", "")
        if not link_url.startswith("https://") or not payment_id.isdigit():
            log.error("freedompay: unexpected answer to a payment request")
            raise _unavailable()
    except ApiError:
        db.delete(row)
        db.commit()
        raise
    row.transaction_id, row.pay_url = payment_id, link_url[:500]
    db.commit()
    return {"redirect": link_url}


async def sync_row(app: FastAPI, row_id: int) -> None:
    """Ask Freedom Pay what became of one payment and apply the answer."""
    with session_scope(app.state.session_factory) as db:
        row = db.get(BillingCheckout, row_id)
        payment_id, order_id = row.transaction_id, row.token
    answer = await _call(app, "get_status3.php", {"pg_payment_id": payment_id})
    answer.setdefault("pg_order_id", order_id)
    with session_scope(app.state.session_factory) as db:
        row = db.get(BillingCheckout, row_id)
        lock_account(db, row.user_id)
        db.refresh(row)
        # The status answer carries no order id of its own: the row it was asked about stands for it.
        apply_result(db, row, {**answer, "pg_order_id": row.token, "pg_payment_id": answer.get("pg_payment_id", "")})


async def sync_user(app: FastAPI, user_id: int) -> None:
    """The page asks this when the person comes back from paying; a callback that never came is made up for here."""
    with session_scope(app.state.session_factory) as db:
        ids = list(db.scalars(select(BillingCheckout.id).where(
            BillingCheckout.user_id == user_id, BillingCheckout.payment_method == billing.FREEDOMPAY_METHOD,
            BillingCheckout.status == CheckoutStatus.PENDING, BillingCheckout.transaction_id.is_not(None),
            BillingCheckout.created_at > utcnow() - SYNC_MAX_AGE).order_by(BillingCheckout.id.desc())
            .limit(SYNC_MAX_COUNT)))
    for row_id in ids:
        try:
            await sync_row(app, row_id)
        except ApiError:
            log.warning("freedompay: could not look up a pending payment, will retry")


@router.post("/billing/freedompay/result")
async def result(request: Request) -> Response:
    """Freedom Pay's result notification: checked by its signature, then against the row we made."""
    fp = request.app.state.settings.freedompay
    if fp is None:
        raise ApiError(404, "not_found", "Not found. / Не найдено.")
    raw = await request.body()
    if len(raw) > CALLBACK_MAX_BYTES:
        raise ApiError(413, "bad_payload", "Payload too large.")
    fields = dict(parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True))
    fields.update({name: value for name, value in request.query_params.items() if name not in fields})
    # The script name is the last segment of the URL Freedom Pay was given: "result".
    if not signature_valid("result", fields, fp.secret):
        raise ApiError(401, "bad_signature", "Access denied.")
    with session_scope(request.app.state.session_factory) as db:
        row = db.scalar(select(BillingCheckout).where(
            BillingCheckout.token == fields.get("pg_order_id", ""),
            BillingCheckout.payment_method == billing.FREEDOMPAY_METHOD))
        if row is not None:
            lock_account(db, row.user_id)
            db.refresh(row)
            apply_result(db, row, fields)
        else:
            log.info("freedompay: notification for an order this server never made")
    # Answered ok either way: "rejected" would refund a payment, and a refusal is not ours to make from here.
    return Response(OK_ANSWER, media_type="application/xml")
