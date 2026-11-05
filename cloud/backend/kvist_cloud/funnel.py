# -*- coding: utf-8 -*-
"""Funnel steps in their own table. First-party only: no third-party tracker, no analytics script."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .models import FunnelEvent

STEPS = (
    "pricing_view",
    "github_login",
    "checkout_open",
    "card_added",
    "key_valid",
    "first_task_started",
    "first_task_done",
)


def record_step(db: Session, step: str, user_id: int | None = None, visitor_id: str = "") -> None:
    if step not in STEPS:
        raise ValueError(f"unknown funnel step: {step}")
    db.add(FunnelEvent(step=step, user_id=user_id, visitor_id=visitor_id[:64]))
