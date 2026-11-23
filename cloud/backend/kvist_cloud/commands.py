# -*- coding: utf-8 -*-
"""Which of Kvist's slash commands a customer can run in the cloud.

The list of commands is Kvist's own: the core announces what it carries out (its manifest), and the page offers
that. This module only says what the cloud takes away, and why:

* the cloud runs these itself or they would undo what it set up, so the core is started without them
  (``--commands-off``): the model key and provider, the machine, updates, the sandbox's rules, and the reports
  and resets that make sense only on a person's own computer;
* the runner needs ``new``, ``session`` and ``resume`` to keep a task in its conversation, so the core keeps
  them, but a person cannot send them;
* some belong to a plan: ``remote`` works on the person's own server, which is what the Remote plan is.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ledger
from .auth import current_user
from .db import get_db
from .models import Task, TaskEvent, User

router = APIRouter()

# Started without these: the cloud owns what they change.
CLOUD_OWNS = frozenset({
    "provider", "change-api", "models", "settings-api", "strime", "headers", "del-provider", "full-reset",
    "effort", "rename", "role", "update", "permissions", "machines", "export", "feedback", "bug", "rate",
})
# Kept in the core for the runner; never sent by a person.
RUNNER_ONLY = frozenset({"new", "session", "resume"})
# A command of one plan only: elsewhere the core is started without it.
PLAN_ONLY = {"remote": frozenset({"remote"})}


def commands_off(plan_id: str) -> list[str]:
    """What the core of a task on this plan is started without."""
    return sorted(CLOUD_OWNS | {name for name, plan_ids in PLAN_ONLY.items() if plan_id not in plan_ids})


def hidden(plan_id: str) -> list[str]:
    """What a person on this plan is not offered and cannot send."""
    return sorted({*commands_off(plan_id), *RUNNER_ONLY})


def allowed(plan_id: str, name: str) -> bool:
    return name not in hidden(plan_id)


def plan_of(db: Session, user: User) -> str:
    subscription = ledger.current_subscription(db, user.id)
    return subscription.plan if subscription is not None else ""


def latest_manifest(db: Session, user: User) -> dict | None:
    """The commands the person's Kvist announced last (kept from any of their tasks), for a page with no task open."""
    found = db.scalars(
        select(TaskEvent)
        .join(Task, Task.id == TaskEvent.task_id)
        .where(Task.user_id == user.id, TaskEvent.type == "manifest")
        .order_by(TaskEvent.id.desc())
        .limit(1)
    ).first()
    return found.payload if found is not None else None


@router.get("/api/commands")
def command_policy(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, object]:
    """What the page offers after "/": Kvist's own list (its last manifest), less what the cloud takes away."""
    return {"hidden": hidden(plan_of(db, user)), "manifest": latest_manifest(db, user)}
