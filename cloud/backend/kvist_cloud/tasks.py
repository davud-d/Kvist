# -*- coding: utf-8 -*-
"""Tasks: their status, derived from Kvist's own events, and the events kept for history."""
from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.orm import Session

from . import ledger
from .db import utcnow
from .models import Task, TaskEvent, TaskStatus
from .secrets import redact_obj

# Kept in task_events for the history; everything else (snapshots, deltas) is live only,
# the same split Kvist's own session journal makes.
PERSISTED = frozenset(
    {
        "hello", "route", "plan", "step", "action_result", "job_started", "job_finished", "job_notice",
        "message", "final", "error", "input_request", "input_closed", "stream_start", "stream_commit",
        "reasoning_start", "reasoning_commit", "usage", "session_title", "artifact", "helper", "skill",
        "turn_end", "wake", "command_result", "session", "notice", "sandbox_notice", "task_state",
    }
)
# Reasons the runner gives for stopping a task that can be continued later.
PAUSE_REASONS = frozenset(
    {"hours_exhausted", "provider_unavailable", "invalid_key", "command_time_limit", "machine_restarted",
     "machine_sleeping"}
)


def _finish(task: Task, status: str, reason: str = "") -> None:
    task.status = status
    if reason:
        task.status_reason = reason
    if status in TaskStatus.FINISHED:
        task.finished_at = utcnow()


def apply_event(task: Task, event: dict) -> None:
    """Move the task's status the way the event says. Unknown events change nothing."""
    kind = event.get("type")
    if kind == "wake" and task.status == TaskStatus.DONE:
        # Background work the answer left running ended and the agent took a turn about it.
        task.status = TaskStatus.RUNNING
        task.finished_at = None
        return
    if task.status in TaskStatus.FINISHED:
        return
    if kind == "hello":
        if task.status in (TaskStatus.STARTING, TaskStatus.PAUSED):
            task.status = TaskStatus.RUNNING
            task.outcome = ""
            task.resume_requested = False
        return
    if kind == "task_state":
        _apply_task_state(task, str(event.get("state", "")))
        return
    if kind == "final":
        task.final_message = str(event.get("message", ""))
        task.outcome = str(event.get("outcome", "")) or "done"
        if task.status_reason in PAUSE_REASONS or event.get("paused"):
            _finish(task, TaskStatus.PAUSED, task.status_reason or "user")
        elif task.status_reason == "cancelled":
            _finish(task, TaskStatus.CANCELLED)
        else:
            _finish(task, TaskStatus.DONE)
        return
    if kind == "turn_end" and task.status in (TaskStatus.RUNNING, TaskStatus.WAITING_FOR_USER):
        # A turn that ended without a final answer ended on an error.
        _finish(task, TaskStatus.FAILED, "agent_error")


def _apply_task_state(task: Task, state: str) -> None:
    if state in ("running", "resuming"):
        task.status = TaskStatus.RUNNING
    elif state == "awaiting_input":
        task.status = TaskStatus.WAITING_FOR_USER
    elif state == "paused":
        _finish(task, TaskStatus.PAUSED, task.status_reason or "user")
    elif state == "cancelled":
        if task.status_reason in PAUSE_REASONS:
            _finish(task, TaskStatus.PAUSED)
        else:
            _finish(task, TaskStatus.CANCELLED)
    elif state == "failed":
        _finish(task, TaskStatus.FAILED, "agent_error")


def apply_exit(task: Task, reason: str) -> None:
    """The Kvist process ended; settle a status the events did not."""
    if task.status in TaskStatus.FINISHED:
        return
    if task.status == TaskStatus.PAUSED:
        if reason in PAUSE_REASONS and task.status_reason in ("", "user"):
            task.status_reason = reason
        return
    if reason in PAUSE_REASONS:
        _finish(task, TaskStatus.PAUSED, reason)
    elif reason == "cancelled":
        _finish(task, TaskStatus.CANCELLED)
    elif reason == "finished" and task.outcome:
        _finish(task, TaskStatus.DONE)
    elif reason == "finished":
        _finish(task, TaskStatus.FAILED, "agent_error")
    else:
        _finish(task, TaskStatus.FAILED, reason)


def task_view(db: Session, task: Task) -> dict[str, object]:
    used = ledger.task_used_seconds(db, task.id)
    return {
        "id": task.id,
        "machine_id": task.machine_id,
        "goal": task.goal,
        "repo_url": task.repo_url,
        "status": task.status,
        "reason": task.status_reason,
        "outcome": task.outcome,
        "final_message": task.final_message,
        "used_seconds": used,
        "used_minutes": ledger.minutes(used),
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


def ingest_event(app: FastAPI, db: Session, task: Task, event: dict) -> str:
    """Store (if it is kept), update the status, fan out. Returns the status before the event."""
    before = task.status
    # Second line of defence after the runner's own scrub: every secret this process has seen.
    event = redact_obj(event)
    kind = str(event.get("type", ""))
    seq = None
    if kind in PERSISTED:
        seq = task.next_seq
        task.next_seq += 1
        db.add(TaskEvent(task_id=task.id, seq=seq, type=kind[:40], payload=event))
    apply_event(task, event)
    db.flush()
    app.state.hub.publish(task.id, {"type": "event", "seq": seq, "event": event})
    if task.status != before:
        app.state.hub.publish(task.id, {"type": "task", "task": task_view(db, task)})
    return before
