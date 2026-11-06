# -*- coding: utf-8 -*-
"""Plan limits on the server side: parallel tasks (a queue) and running out of hours (a pause).

Command time and the background window are enforced by the runner, from the limits sent with
every task (gateway.start_message).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import ledger, plans
from .db import lock_account, session_scope
from .models import Task, TaskStatus

log = logging.getLogger("kvist_cloud.limits")


def user_plan(db: Session, user_id: int) -> plans.Plan:
    subscription = ledger.current_subscription(db, user_id)
    return plans.get_plan(subscription.plan) if subscription is not None else plans.TRIAL


def active_count(db: Session, user_id: int, exclude: int | None = None) -> int:
    query = select(func.count()).select_from(Task).where(Task.user_id == user_id, Task.status.in_(TaskStatus.ACTIVE))
    if exclude is not None:
        query = query.where(Task.id != exclude)
    return int(db.scalar(query) or 0)


def admit(db: Session, task: Task) -> None:
    """Start now if the plan has a free slot, otherwise wait in the queue."""
    lock_account(db, task.user_id)
    if ledger.balance_seconds(db, task.user_id) <= 0:
        task.status = TaskStatus.PAUSED
        task.status_reason = "hours_exhausted"
        return
    limit = user_plan(db, task.user_id).max_parallel_tasks
    if active_count(db, task.user_id, exclude=task.id) >= limit:
        task.status = TaskStatus.QUEUED
        return
    task.status = TaskStatus.STARTING


def promote(db: Session, user_id: int) -> list[int]:
    """Move queued tasks to starting, oldest first, while slots are free. Returns their machines."""
    lock_account(db, user_id)
    free = user_plan(db, user_id).max_parallel_tasks - active_count(db, user_id)
    if free <= 0:
        return []
    queued = list(
        db.scalars(
            select(Task).where(Task.user_id == user_id, Task.status == TaskStatus.QUEUED).order_by(Task.id).limit(free)
        )
    )
    if queued and ledger.balance_seconds(db, user_id) <= 0:
        # Nothing to run them on: they wait, paused, for the person to add hours.
        for task in queued:
            task.status = TaskStatus.PAUSED
            task.status_reason = "hours_exhausted"
        return []
    for task in queued:
        task.status = TaskStatus.STARTING
        task.started_at = None
    return sorted({task.machine_id for task in queued})


async def admit_queued(app: FastAPI, machine_id: int) -> None:
    """A slot may have freed up on this machine's account; start what is waiting."""
    from .gateway import dispatch
    from .models import Machine

    with session_scope(app.state.session_factory) as db:
        machine = db.get(Machine, machine_id)
        if machine is None:
            return
        machine_ids = set(promote(db, machine.user_id)) | {machine_id}
    for target in sorted(machine_ids):
        await dispatch(app, target)


async def on_hours_exhausted(app: FastAPI, machine_id: int) -> None:
    """Hours are over: every running task gets a pause with the grace for its current command."""
    with session_scope(app.state.session_factory) as db:
        running = list(
            db.scalars(
                select(Task).where(
                    Task.machine_id == machine_id,
                    Task.status.in_(TaskStatus.ACTIVE),
                    Task.status_reason != "hours_exhausted",
                )
            )
        )
        for task in running:
            task.status_reason = "hours_exhausted"
        task_ids = [task.id for task in running]
    for task_id in task_ids:
        log.info("task %s: hours are over, pausing", task_id)
        await app.state.gateway.send(machine_id, {"type": "control", "task_id": task_id, "action": "hours_exhausted"})
