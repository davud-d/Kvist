# -*- coding: utf-8 -*-
"""Plan limits on the server side: parallel tasks (a queue) and running out of hours (a pause).

Command time and the background window are enforced by the runner, from the limits sent with
every task (gateway.start_message).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import session_scope
from .models import Task, TaskStatus

log = logging.getLogger("kvist_cloud.limits")


def admit(db: Session, task: Task) -> None:
    """A new or resumed task is ready to go to its machine."""
    task.status = TaskStatus.STARTING


async def on_hours_exhausted(app: FastAPI, machine_id: int) -> None:
    """Hours are over: every running task gets a pause with the grace for its current command."""
    with session_scope(app.state.session_factory) as db:
        running = list(
            db.scalars(
                select(Task).where(
                    Task.machine_id == machine_id,
                    Task.status == TaskStatus.RUNNING,
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


async def admit_queued(app: FastAPI, machine_id: int) -> None:
    """A slot may have freed up; start what is waiting."""
    from .gateway import dispatch

    await dispatch(app, machine_id)
