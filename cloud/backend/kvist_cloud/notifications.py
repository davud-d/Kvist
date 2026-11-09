# -*- coding: utf-8 -*-
"""The bell: short notices for the signed-in person (hours or a plan from an admin, a machine stopped, ...).

A row holds a kind and its parameters; the page words it in the reader's language. Email is not involved:
the outbox only queues until a mail provider exists.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .auth import current_user
from .db import get_db, utcnow
from .models import Notification, User

router = APIRouter()
LIST_LIMIT = 30


def notify(db: Session, user_id: int, kind: str, **params: object) -> None:
    db.add(Notification(user_id=user_id, kind=kind, params=params))


def _unread(db: Session, user_id: int) -> int:
    return int(db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == user_id, Notification.read_at.is_(None))) or 0)


@router.get("/api/notifications")
def list_notifications(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, object]:
    rows = db.scalars(select(Notification).where(Notification.user_id == user.id)
                      .order_by(Notification.id.desc()).limit(LIST_LIMIT))
    return {
        "unread": _unread(db, user.id),
        "items": [{"id": row.id, "kind": row.kind, "params": row.params, "at": row.created_at.isoformat(),
                   "read": row.read_at is not None} for row in rows],
    }


@router.post("/api/notifications/read")
def mark_read(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, object]:
    db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
               .values(read_at=utcnow()))
    db.commit()
    return {"unread": 0}
