# -*- coding: utf-8 -*-
"""WebSocket for runners. The machine dials out; it never accepts a connection.

Runner → server: hello, heartbeat, event, task_reason, task_exit, export_done.
Server → runner: start_task, input_response, control, export.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import exports, ledger, limits, plans, projects, screens, tasks
from .db import lock_account, session_scope, utcnow
from .keys import kvist_environment
from .machines import spawn, wake
from .models import Machine, MachineStatus, Project, Task, TaskStatus, User
from .secrets import SecretError

log = logging.getLogger("kvist_cloud.gateway")
router = APIRouter()
POLICY_VIOLATION = 4401
REPLACED = 4000


class Gateway:
    """The live runner connections, one per machine."""

    def __init__(self) -> None:
        self.sockets: dict[int, WebSocket] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def attach(self, machine_id: int, socket: WebSocket) -> WebSocket | None:
        previous = self.sockets.get(machine_id)
        self.sockets[machine_id] = socket
        self._locks.setdefault(machine_id, asyncio.Lock())
        return previous

    def detach(self, machine_id: int, socket: WebSocket) -> bool:
        if self.sockets.get(machine_id) is socket:
            del self.sockets[machine_id]
            return True
        return False

    def connected(self, machine_id: int) -> bool:
        return machine_id in self.sockets

    async def send(self, machine_id: int, message: dict) -> bool:
        socket = self.sockets.get(machine_id)
        if socket is None:
            return False
        async with self._locks[machine_id]:
            try:
                await socket.send_json(message)
            except (WebSocketDisconnect, RuntimeError, OSError):
                return False
        return True


def start_message(app: FastAPI, db: Session, task: Task) -> dict | None:
    """Everything the runner needs for one task, the decrypted key included. None: cannot start."""
    user = db.get(User, task.user_id)
    if user is None or not user.model_key_enc:
        return None
    try:
        key = app.state.vault.decrypt(user.model_key_enc)
    except SecretError:
        log.error("task %s: the stored key cannot be decrypted", task.id)
        return None
    settings = app.state.settings
    subscription = ledger.current_subscription(db, user.id)
    if subscription is None or not ledger.has_hours(db, user.id):
        return None
    plan = plans.get_plan(subscription.plan)
    project = db.get(Project, task.project_id) if task.project_id else None
    if project is not None:
        # A project's tasks share its folder, so each keeps its conversation in a session of its own, named
        # for the task: a first run opens it, a later one switches back into it.
        session = f"task-{task.id}"
    else:
        session = tasks.core_session(db, task) if task.pending_text else ""
    return {
        "type": "start_task",
        "task_id": task.id,
        # The first goal starts the task; what was written later goes into the same conversation.
        "goal": task.pending_text or task.goal,
        "folder": project.folder if project is not None else "",
        "session": session,
        "follow_up": bool(task.pending_text),
        "repo_url": task.repo_url,
        "github_token": app.state.vault.decrypt(user.github_token_enc) if user.github_token_enc else "",
        "resume": task.resume_requested,
        "env": kvist_environment(user, key),
        "limits": {
            "command_seconds": plan.command_minutes * 60,
            "background_seconds": plan.background_minutes * 60,
            "vcpu": plan.vcpu,
            "memory_gb": plan.memory_gb,
            "hours_grace_seconds": settings.hours_grace_seconds,
            "provider_outage_seconds": settings.provider_outage_seconds,
            "allow_local_repos": settings.allow_local_endpoints,
        },
    }


async def dispatch(app: FastAPI, machine_id: int, known: set[int] | None = None) -> None:
    """Send every task waiting to start on this machine; wake the machine if it is not here."""
    if not app.state.gateway.connected(machine_id):
        spawn(app, wake(app, machine_id))
        return
    with session_scope(app.state.session_factory) as db:
        machine = db.get(Machine, machine_id)
        if machine is None:
            return
        lock_account(db, machine.user_id)
        waiting = list(
            db.scalars(
                select(Task).where(Task.machine_id == machine_id, Task.status == TaskStatus.STARTING).order_by(Task.id)
            )
        )
        messages = []
        for task in waiting:
            if task.started_at is not None and (known is None or task.id in known):
                continue
            if not ledger.has_hours(db, task.user_id):
                # Hours can run out between admission and start; Continue picks it up after a purchase.
                tasks.apply_exit(task, "hours_exhausted")
                app.state.hub.publish(task.id, {"type": "task", "task": tasks.task_view(db, task)})
                continue
            message = start_message(app, db, task)
            if message is None:
                tasks.apply_exit(task, "no_key")
                app.state.hub.publish(task.id, {"type": "task", "task": tasks.task_view(db, task)})
                continue
            task.started_at = utcnow()
            messages.append(message)
    for message in messages:
        if not await app.state.gateway.send(machine_id, message):
            log.warning("task %s: start not delivered, it goes again when the runner is back", message["task_id"])


def _machine_for_token(db: Session, token: str) -> Machine | None:
    if not token:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    machine = db.scalars(select(Machine).where(Machine.token_hash == digest)).first()
    if machine is None or machine.status == MachineStatus.DELETED:
        return None
    return machine


def _on_connect(app: FastAPI, machine_id: int) -> None:
    with session_scope(app.state.session_factory) as db:
        machine = db.get(Machine, machine_id)
        machine.connected = True
        machine.status = MachineStatus.AWAKE
        machine.status_detail = ""
        machine.last_active_at = utcnow()
        ledger.stop_meter(machine)


def _on_disconnect(app: FastAPI, machine_id: int) -> None:
    with session_scope(app.state.session_factory) as db:
        machine = db.get(Machine, machine_id)
        if machine is None:
            return
        machine.connected = False
        # Heartbeats are gone: billing stops at the last one received.
        ledger.stop_meter(machine)
        if machine.status in (MachineStatus.AWAKE, MachineStatus.SLEEPING):
            machine.status = MachineStatus.ASLEEP


async def _hello(app: FastAPI, machine_id: int, message: dict) -> None:
    known = {int(item.get("task_id", 0)) for item in message.get("tasks") or [] if isinstance(item, dict)}
    with session_scope(app.state.session_factory) as db:
        lost = db.scalars(
            select(Task).where(
                Task.machine_id == machine_id,
                Task.status.in_((TaskStatus.RUNNING, TaskStatus.WAITING_FOR_USER)),
            )
        )
        for task in lost:
            if task.id not in known:
                # The runner restarted and its Kvist process with it; the checkpoint is on disk.
                tasks.apply_exit(task, "machine_restarted")
                app.state.hub.publish(task.id, {"type": "task", "task": tasks.task_view(db, task)})
    await dispatch(app, machine_id, known)
    await exports.send_pending(app, machine_id)
    await _send_projects(app, machine_id)


async def _send_projects(app: FastAPI, machine_id: int) -> None:
    """Every project of the machine's owner gets its folder: ones added while it slept, or lost since.

    A folder that is already there is left alone, so this is cheap and says the same thing every time.
    """
    with session_scope(app.state.session_factory) as db:
        machine = db.get(Machine, machine_id)
        if machine is None:
            return
        messages = [
            projects.prepare_message(app, project, db.get(User, machine.user_id))
            for project in db.scalars(select(Project).where(Project.user_id == machine.user_id))
        ]
    for message in messages:
        await app.state.gateway.send(machine_id, message)


def _heartbeat(app: FastAPI, machine_id: int, message: dict) -> ledger.BeatResult:
    running = [int(item) for item in message.get("running") or []]
    background = [int(item) for item in message.get("background") or []]
    settings = app.state.settings
    with session_scope(app.state.session_factory) as db:
        machine = db.get(Machine, machine_id)
        lock_account(db, machine.user_id)
        db.refresh(machine)
        result = ledger.record_heartbeat(db, machine, running, app.state.clock(), settings.heartbeat_max_gap_seconds)
        if running or background:
            machine.last_active_at = utcnow()
        for task_id, seconds in result.per_task.items():
            app.state.hub.publish(task_id, {"type": "usage", "task_id": task_id, "billed_seconds": seconds,
                                            "used_seconds": ledger.task_used_seconds(db, task_id),
                                            "balance_seconds": result.balance_seconds})
    return result


def _owned_task(db: Session, machine_id: int, task_id: int) -> Task | None:
    machine = db.get(Machine, machine_id)
    if machine is None:
        return None
    lock_account(db, machine.user_id)
    task = db.get(Task, task_id, populate_existing=True)
    if task is None or task.machine_id != machine_id:
        return None
    return task


def _event(app: FastAPI, machine_id: int, message: dict) -> bool:
    """Ingest one relayed event; True when it freed a parallel-task slot."""
    event = message.get("event")
    if not isinstance(event, dict):
        return False
    with session_scope(app.state.session_factory) as db:
        task = _owned_task(db, machine_id, int(message.get("task_id") or 0))
        if task is None:
            log.warning("machine %s sent an event for a task it does not own", machine_id)
            return False
        before = tasks.ingest_event(app, db, task, event)
        return before in TaskStatus.ACTIVE and task.status not in TaskStatus.ACTIVE


def _screen_frame(app: FastAPI, machine_id: int, message: dict) -> None:
    """A picture of the agent's screen: kept in memory for its owner, never written anywhere."""
    with session_scope(app.state.session_factory) as db:
        task = _owned_task(db, machine_id, int(message.get("task_id") or 0))
        task_id = task.id if task is not None else None
    if task_id is None:
        log.warning("machine %s sent a screen picture for a task it does not own", machine_id)
        return
    jpeg = screens.decode_frame(message)
    if jpeg is None or not app.state.screens.put_frame(task_id, str(message.get("screen_id") or ""), jpeg):
        log.info("machine %s: a screen picture was refused", machine_id)


async def _undelivered(app: FastAPI, machine_id: int, message: dict) -> None:
    """The run that should have taken a message had just ended: the message starts the next run."""
    with session_scope(app.state.session_factory) as db:
        task = _owned_task(db, machine_id, int(message.get("task_id") or 0))
        text = str(message.get("text") or "")
        if task is None or not text:
            return
        tasks.restart_with(db, task, text)
        app.state.hub.publish(task.id, {"type": "task", "task": tasks.task_view(db, task)})
    await dispatch(app, machine_id)


def _reason(app: FastAPI, machine_id: int, message: dict) -> None:
    with session_scope(app.state.session_factory) as db:
        task = _owned_task(db, machine_id, int(message.get("task_id") or 0))
        if task is not None and task.status not in TaskStatus.FINISHED:
            task.status_reason = str(message.get("reason", ""))[:60]


def _exit(app: FastAPI, machine_id: int, message: dict) -> None:
    with session_scope(app.state.session_factory) as db:
        task = _owned_task(db, machine_id, int(message.get("task_id") or 0))
        if task is None:
            return
        if task.pending_text and task.status in (TaskStatus.QUEUED, TaskStatus.STARTING):
            # The previous run of this session ending: the next one is already waiting for the machine.
            return
        before = task.status
        tasks.apply_exit(task, str(message.get("reason", "crashed"))[:60])
        if task.status in TaskStatus.FINISHED:
            app.state.screens.forget_task(task.id)
        if task.status != before:
            app.state.hub.publish(task.id, {"type": "task", "task": tasks.task_view(db, task)})


async def handle(app: FastAPI, machine_id: int, message: dict) -> None:
    kind = message.get("type")
    if kind == "hello":
        await _hello(app, machine_id, message)
    elif kind == "heartbeat":
        result = _heartbeat(app, machine_id, message)
        if result.exhausted:
            await limits.on_hours_exhausted(app, machine_id)
    elif kind == "event":
        if _event(app, machine_id, message):
            await limits.admit_queued(app, machine_id)
    elif kind == "screen_frame":
        _screen_frame(app, machine_id, message)
    elif kind == "message_undelivered":
        await _undelivered(app, machine_id, message)
    elif kind == "task_reason":
        _reason(app, machine_id, message)
    elif kind == "task_exit":
        _exit(app, machine_id, message)
        await limits.admit_queued(app, machine_id)
    elif kind == "export_done":
        exports.on_export_done(app, machine_id, message)
    else:
        log.warning("machine %s sent an unknown message type %r", machine_id, kind)


@router.websocket("/gateway/ws")
async def gateway_socket(socket: WebSocket) -> None:
    app = socket.app
    header = socket.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    with session_scope(app.state.session_factory) as db:
        machine = _machine_for_token(db, token)
        machine_id = machine.id if machine is not None else None
    if machine_id is None:
        await socket.close(code=POLICY_VIOLATION)
        return
    await socket.accept()
    previous = app.state.gateway.attach(machine_id, socket)
    if previous is not None:
        await previous.close(code=REPLACED)
    _on_connect(app, machine_id)
    log.info("machine %s connected", machine_id)
    try:
        while True:
            message = await socket.receive_json()
            if isinstance(message, dict):
                await handle(app, machine_id, message)
    except (WebSocketDisconnect, RuntimeError):
        pass  # the runner left (RuntimeError: it left while a message was being read)
    finally:
        if app.state.gateway.detach(machine_id, socket):
            _on_disconnect(app, machine_id)
            log.info("machine %s disconnected", machine_id)
