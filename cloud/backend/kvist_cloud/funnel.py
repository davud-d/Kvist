# -*- coding: utf-8 -*-
"""Funnel steps in their own table, and the admin's view of conversion between them.

First-party only: no third-party tracker, no analytics script. Before sign-in a person is a random
id in the kc_visitor cookie; the github_login step carries both ids, which joins the two.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ledger
from .auth import admin_user
from .db import get_db, utcnow
from .errors import ApiError, not_found
from .models import Dispute, FunnelEvent, User
from .validation import InputModel

STEPS = (
    "pricing_view",
    "github_login",
    "checkout_open",
    "card_added",
    "key_valid",
    "first_task_started",
    "first_task_done",
)
VISITOR_COOKIE = "kc_visitor"
router = APIRouter()


def record_step(db: Session, step: str, user_id: int | None = None, visitor_id: str = "") -> None:
    if step not in STEPS:
        raise ValueError(f"unknown funnel step: {step}")
    db.add(FunnelEvent(step=step, user_id=user_id, visitor_id=visitor_id[:64]))


def record_pricing_view(db: Session, request: Request, response: Response) -> None:
    """Once per visitor; sets the visitor cookie if there is none."""
    visitor = request.cookies.get(VISITOR_COOKIE, "")
    if not visitor or len(visitor) > 64:
        visitor = secrets.token_urlsafe(16)
        response.set_cookie(VISITOR_COOKIE, visitor, max_age=365 * 24 * 3600, httponly=True, samesite="lax", path="/")
    seen = db.scalar(
        select(FunnelEvent.id).where(FunnelEvent.step == "pricing_view", FunnelEvent.visitor_id == visitor)
    )
    if seen is None:
        record_step(db, "pricing_view", visitor_id=visitor)
        db.commit()


def conversion(db: Session, days: int | None = None) -> list[dict[str, object]]:
    """Distinct people per step and the share of the previous step's people who got here."""
    query = select(FunnelEvent)
    if days:
        query = query.where(FunnelEvent.created_at >= utcnow() - timedelta(days=days))
    events = list(db.scalars(query))
    visitor_to_user = {e.visitor_id: e.user_id for e in events if e.visitor_id and e.user_id is not None}

    def person(event: FunnelEvent) -> str:
        user_id = event.user_id if event.user_id is not None else visitor_to_user.get(event.visitor_id)
        return f"u{user_id}" if user_id is not None else f"v{event.visitor_id}"

    people: dict[str, set[str]] = {step: set() for step in STEPS}
    for event in events:
        if event.step in people:
            people[event.step].add(person(event))
    rows: list[dict[str, object]] = []
    previous: int | None = None
    for step in STEPS:
        count = len(people[step])
        share = None if previous is None else (round(count / previous, 4) if previous else 0.0)
        rows.append({"step": step, "people": count, "from_previous": share})
        previous = count
    return rows


@router.get("/api/admin/funnel")
def admin_funnel(days: int | None = None, _admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    return {"steps": conversion(db, days), "days": days}


class RefundIn(InputModel):
    minutes: int = Field(gt=0, le=100_000)
    note: str = Field(min_length=3, max_length=300)


@router.get("/api/admin/disputes")
def admin_disputes(_admin: User = Depends(admin_user), db: Session = Depends(get_db)) -> list[dict[str, object]]:
    rows = db.scalars(select(Dispute).where(Dispute.status == "open").order_by(Dispute.id))
    return [
        {
            "id": row.id,
            "user": (db.get(User, row.user_id) or User(github_login="?")).github_login,
            "task_id": row.task_id,
            "message": row.message,
            "charged_seconds": ledger.task_used_seconds(db, row.task_id) if row.task_id else 0,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


def _open_dispute(db: Session, dispute_id: int) -> Dispute:
    dispute = db.get(Dispute, dispute_id)
    if dispute is None or dispute.status != "open":
        raise not_found("dispute")
    return dispute


@router.post("/api/admin/disputes/{dispute_id}/refund")
def admin_refund(dispute_id: int, payload: RefundIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """The correction is a new refund row with the reason; the charge itself is never edited."""
    dispute = _open_dispute(db, dispute_id)
    try:
        entry = ledger.refund(db, dispute.user_id, payload.minutes * 60, f"dispute #{dispute.id}: {payload.note}",
                              task_id=dispute.task_id)
    except ledger.LedgerError as error:
        raise ApiError(409, "refund_refused", str(error)) from error
    dispute.status = "refunded"
    dispute.resolution = f"{payload.minutes} min by {admin.github_login}: {payload.note}"
    db.commit()
    return {"ok": True, "refund_id": entry.id}


@router.post("/api/admin/disputes/{dispute_id}/close")
def admin_close(dispute_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    dispute = _open_dispute(db, dispute_id)
    dispute.status = "closed"
    dispute.resolution = f"closed by {admin.github_login}"
    db.commit()
    return {"ok": True}
