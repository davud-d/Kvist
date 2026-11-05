# -*- coding: utf-8 -*-
"""Hours ledger. Append-only; the rules are in docs/ARCHITECTURE.md, "Hours ledger rules".

Billing happens in exactly one place, ``record_heartbeat``: the interval between two consecutive
heartbeats of one machine is charged once, however many tasks ran in it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import current_user
from .db import get_db
from .errors import not_found
from .models import (
    Dispute,
    LedgerEntry,
    LedgerKind,
    Machine,
    Subscription,
    SubscriptionStatus,
    Task,
    TaskStatus,
    User,
)

router = APIRouter()


class LedgerError(ValueError):
    pass


@dataclass(slots=True)
class BeatResult:
    billed_seconds: int = 0
    per_task: dict[int, int] = field(default_factory=dict)
    balance_seconds: int = 0
    exhausted: bool = False


def minutes(seconds: int) -> int:
    """Per-second accounting, shown in whole minutes rounded down."""
    return abs(seconds) // 60


def _at(second: int) -> datetime:
    return datetime.fromtimestamp(second, UTC)


def current_subscription(db: Session, user_id: int) -> Subscription | None:
    return db.scalars(
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.status.in_(SubscriptionStatus.USABLE))
        .order_by(Subscription.id.desc())
    ).first()


def balance_seconds(db: Session, user_id: int, period_start: datetime | None = None) -> int:
    if period_start is None:
        subscription = current_subscription(db, user_id)
        if subscription is None:
            return 0
        period_start = subscription.current_period_start
    total = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.seconds), 0)).where(
            LedgerEntry.user_id == user_id, LedgerEntry.period_start == period_start
        )
    )
    return int(total or 0)


def grant_period(db: Session, subscription: Subscription, seconds: int, reason: str) -> LedgerEntry | None:
    """The plan's hours for the subscription's current period, once per period."""
    exists = db.scalar(
        select(LedgerEntry.id).where(
            LedgerEntry.user_id == subscription.user_id,
            LedgerEntry.period_start == subscription.current_period_start,
            LedgerEntry.kind == LedgerKind.GRANT,
        )
    )
    if exists is not None:
        return None
    entry = LedgerEntry(
        user_id=subscription.user_id,
        period_start=subscription.current_period_start,
        kind=LedgerKind.GRANT,
        seconds=seconds,
        reason=reason,
    )
    db.add(entry)
    db.flush()
    return entry


def purchase(db: Session, user_id: int, seconds: int, reason: str) -> LedgerEntry:
    subscription = current_subscription(db, user_id)
    if subscription is None:
        raise LedgerError("no active subscription to add hours to")
    if seconds <= 0:
        raise LedgerError("a purchase adds time")
    entry = LedgerEntry(
        user_id=user_id,
        period_start=subscription.current_period_start,
        kind=LedgerKind.PURCHASE,
        seconds=seconds,
        reason=reason,
    )
    db.add(entry)
    db.flush()
    return entry


def refund(
    db: Session,
    user_id: int,
    seconds: int,
    reason: str,
    refers_to: int | None = None,
    task_id: int | None = None,
) -> LedgerEntry:
    """A correction is a new row with a reason, never an edit of the old one."""
    if seconds <= 0:
        raise LedgerError("a refund gives time back")
    if not reason.strip():
        raise LedgerError("a refund needs a reason")
    subscription = current_subscription(db, user_id)
    if subscription is None:
        raise LedgerError("no active subscription to refund into")
    entry = LedgerEntry(
        user_id=user_id,
        period_start=subscription.current_period_start,
        kind=LedgerKind.REFUND,
        seconds=seconds,
        reason=reason.strip(),
        refers_to=refers_to,
        task_id=task_id,
    )
    db.add(entry)
    db.flush()
    return entry


def split_seconds(seconds: int, task_ids: list[int]) -> dict[int, int]:
    """Shares for display; they add up to exactly ``seconds``."""
    ordered = sorted(set(task_ids))
    if not ordered:
        return {}
    base, remainder = divmod(seconds, len(ordered))
    return {task_id: base + (1 if index < remainder else 0) for index, task_id in enumerate(ordered)}


def _running_on_server(db: Session, machine_id: int, task_ids: list[int]) -> list[int]:
    if not task_ids:
        return []
    return sorted(
        db.scalars(
            select(Task.id).where(
                Task.machine_id == machine_id,
                Task.id.in_(task_ids),
                Task.status == TaskStatus.RUNNING,
            )
        )
    )


def record_heartbeat(
    db: Session,
    machine: Machine,
    running_task_ids: list[int],
    now: float,
    max_gap_seconds: float,
) -> BeatResult:
    """Bill the interval since the previous heartbeat and remember this one.

    ``now`` is the server's clock. The tasks billed for an interval are the ones the previous
    heartbeat named that were also ``running`` on the server at that moment.
    """
    second = math.floor(now)
    result = BeatResult()
    previous = machine.last_beat_second
    billable = list(machine.last_beat_running or [])
    subscription = current_subscription(db, machine.user_id)
    if previous is not None and billable and 0 < second - previous <= max_gap_seconds:
        result = _bill(db, machine, subscription, billable, previous, second)
    elif subscription is not None:
        result.balance_seconds = balance_seconds(db, machine.user_id, subscription.current_period_start)
        result.exhausted = result.balance_seconds <= 0
    else:
        result.exhausted = True
    machine.last_beat_second = second
    machine.last_beat_running = _running_on_server(db, machine.id, running_task_ids)
    db.flush()
    return result


def _bill(
    db: Session,
    machine: Machine,
    subscription: Subscription | None,
    task_ids: list[int],
    start: int,
    end: int,
) -> BeatResult:
    if subscription is None:
        return BeatResult(exhausted=True)
    remaining = balance_seconds(db, machine.user_id, subscription.current_period_start)
    # Never charge past the balance: the grace minutes after it runs out are free.
    billed = max(0, min(end - start, remaining))
    shares = split_seconds(billed, task_ids)
    for task_id, share in shares.items():
        if share == 0:
            continue
        db.add(
            LedgerEntry(
                user_id=machine.user_id,
                period_start=subscription.current_period_start,
                kind=LedgerKind.USAGE,
                seconds=-share,
                machine_id=machine.id,
                task_id=task_id,
                interval_start=_at(start),
                interval_end=_at(end),
            )
        )
    balance = remaining - billed
    return BeatResult(billed_seconds=billed, per_task=shares, balance_seconds=balance, exhausted=balance <= 0)


def stop_meter(machine: Machine) -> None:
    """Heartbeats are gone (the runner disconnected): billing stopped at the last one."""
    machine.last_beat_second = None
    machine.last_beat_running = []


def task_used_seconds(db: Session, task_id: int) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.seconds), 0)).where(
            LedgerEntry.task_id == task_id, LedgerEntry.kind == LedgerKind.USAGE
        )
    )
    return -int(total or 0)


def period_feed(db: Session, user_id: int, period_start: datetime) -> list[dict[str, object]]:
    """Charges of the period grouped by task, newest first, plus every non-usage row."""
    rows = db.execute(
        select(
            LedgerEntry.task_id,
            func.sum(LedgerEntry.seconds),
            func.max(LedgerEntry.created_at),
        )
        .where(
            LedgerEntry.user_id == user_id,
            LedgerEntry.period_start == period_start,
            LedgerEntry.kind == LedgerKind.USAGE,
        )
        .group_by(LedgerEntry.task_id)
    ).all()
    goals = dict(db.execute(select(Task.id, Task.goal).where(Task.id.in_([row[0] for row in rows]))).all())
    feed: list[dict[str, object]] = [
        {
            "kind": LedgerKind.USAGE,
            "task_id": task_id,
            "goal": goals.get(task_id, ""),
            "seconds": int(total),
            "minutes": minutes(int(total)),
            "at": last_at.isoformat(),
        }
        for task_id, total, last_at in rows
    ]
    others = db.scalars(
        select(LedgerEntry).where(
            LedgerEntry.user_id == user_id,
            LedgerEntry.period_start == period_start,
            LedgerEntry.kind != LedgerKind.USAGE,
        )
    )
    feed.extend(
        {
            "kind": entry.kind,
            "task_id": entry.task_id,
            "goal": goals.get(entry.task_id or 0, ""),
            "seconds": entry.seconds,
            "minutes": minutes(entry.seconds),
            "reason": entry.reason,
            "at": entry.created_at.isoformat(),
        }
        for entry in others
    )
    feed.sort(key=lambda item: str(item["at"]), reverse=True)
    return feed


# -- API: the person's own feed, and disputing a charge ------------------------------------


class DisputeIn(BaseModel):
    task_id: int | None = None
    message: str = Field(min_length=3, max_length=4000)


@router.get("/api/ledger")
def get_ledger(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, object]:
    from .billing import subscription_view

    subscription = current_subscription(db, user.id)
    feed = period_feed(db, user.id, subscription.current_period_start) if subscription is not None else []
    disputed = set(db.scalars(select(Dispute.task_id).where(Dispute.user_id == user.id)))
    for item in feed:
        item["disputed"] = item.get("task_id") in disputed
    return {"subscription": subscription_view(db, user), "feed": feed}


@router.post("/api/disputes", status_code=201)
def open_dispute(payload: DisputeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A request for a person to look at a charge. A refund, if any, is a new ledger row."""
    if payload.task_id is not None:
        task = db.get(Task, payload.task_id)
        if task is None or task.user_id != user.id:
            raise not_found("task")
    dispute = Dispute(user_id=user.id, task_id=payload.task_id, message=payload.message.strip())
    db.add(dispute)
    db.commit()
    return {"id": dispute.id, "status": dispute.status}
