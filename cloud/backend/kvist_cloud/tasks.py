# -*- coding: utf-8 -*-
"""Tasks: their status, derived from Kvist's own events, and the events kept for history."""
from __future__ import annotations

import asyncio
import contextlib
import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from pydantic import Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import ledger
from .auth import SESSION_COOKIE, ban_active, current_user, user_from_cookie
from .db import get_db, lock_account, session_scope, utcnow
from .errors import ApiError, not_found
from .funnel import record_step
from .keys import has_model_access
from .models import Machine, MachineStatus, Task, TaskEvent, TaskStatus, User
from .secrets import redact_obj
from .validation import InputModel

router = APIRouter()
GOAL_LIMIT = 20_000
HISTORY_PAGE = 500

# Kept in task_events for the history; everything else (snapshots, deltas) is live only,
# the same split Kvist's own session journal makes.
PERSISTED = frozenset(
    {
        "hello", "route", "plan", "step", "action_result", "job_started", "job_finished", "job_notice",
        "message", "final", "error", "input_request", "input_closed", "stream_start", "stream_commit",
        "reasoning_start", "reasoning_commit", "usage", "session_title", "artifact", "helper", "skill",
        "turn_end", "wake", "command_result", "session", "notice", "sandbox_notice", "task_state", "user",
    }
)
# Reasons the runner gives for stopping a task that can be continued later.
PAUSE_REASONS = frozenset(
    {"hours_exhausted", "provider_unavailable", "invalid_key", "command_time_limit", "machine_restarted",
     "machine_sleeping", "machine_suspended"}
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
    if kind == "message" and event.get("source", "reply") == "reply":
        # A chat reply ends its turn with no final event (UI_API.md, turn_end): it is the answer.
        task.final_message = str(event.get("markdown", ""))
        task.outcome = task.outcome or "replied"
        return
    if kind == "stream_commit" and str(event.get("text", "")).strip():
        # The same answer when the provider streams it. An empty commit is the end-of-session tag that is
        # never shown: its answer comes as a message or a final.
        task.final_message = str(event["text"])
        task.outcome = task.outcome or "replied"
        return
    if kind == "error" and task.outcome == "replied":
        task.outcome = ""
        return
    if kind == "turn_end" and task.status in (TaskStatus.RUNNING, TaskStatus.WAITING_FOR_USER):
        if task.status_reason in PAUSE_REASONS:
            # The runner stopped it on purpose (a wrong key, hours out, ...): it can continue later.
            _finish(task, TaskStatus.PAUSED)
        elif task.outcome:
            _finish(task, TaskStatus.DONE)
        else:
            # A turn that ended with neither a final answer nor a reply ended on an error.
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
        "public_id": task.public_id,
        "machine_id": task.machine_id,
        "project_id": task.project_id,
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


def record_user(app: FastAPI, db: Session, task: Task, text: str) -> None:
    """What the person wrote, kept in the task's history like any event: the core's own journal never reaches us."""
    event = redact_obj({"type": "user", "text": text})
    seq = task.next_seq
    task.next_seq += 1
    db.add(TaskEvent(task_id=task.id, seq=seq, type="user", payload=event))
    db.flush()
    app.state.hub.publish(task.id, {"type": "event", "seq": seq, "event": event})


def core_session(db: Session, task: Task) -> str:
    """The name Kvist gave this conversation, so a later run of Kvist can switch back into it.

    Every run starts a session of its own (hello); the conversation stays in the one the first run opened,
    and a `session` event (the switch back) names it again.
    """
    rows = db.scalars(
        select(TaskEvent)
        .where(TaskEvent.task_id == task.id, TaskEvent.type.in_(("session", "hello")))
        .order_by(TaskEvent.seq)
    )
    first_hello = ""
    switched = ""
    for row in rows:
        name = str(row.payload.get("session") or "")
        if row.type == "session" and name:
            switched = name
        elif row.type == "hello" and name and not first_hello:
            first_hello = name
    return switched or first_hello


def ingest_event(app: FastAPI, db: Session, task: Task, event: dict) -> str:
    """Store (if it is kept), update the status, fan out. Returns the status before the event."""
    before = task.status
    # Second line of defence after the runner's own scrub: every secret this process has seen.
    event = redact_obj(event)
    kind = str(event.get("type", ""))
    if kind == "screen":
        # Live only, and only the whitelisted view with our own address: never the machine's URL or token.
        view = app.state.screens.apply(task.id, event)
        if view is not None:
            app.state.hub.publish(task.id, {"type": "event", "seq": None, "event": view})
        return task.status
    if kind == "hello" or (kind == "task_state" and event.get("state") == "running"):
        # Kvist on the machine is up and working, so it has the message that started this turn.
        task.pending_text = ""
    seq = None
    if kind in PERSISTED:
        seq = task.next_seq
        task.next_seq += 1
        db.add(TaskEvent(task_id=task.id, seq=seq, type=kind[:40], payload=event))
    apply_event(task, event)
    db.flush()
    app.state.hub.publish(task.id, {"type": "event", "seq": seq, "event": event})
    if task.status != before:
        if task.status in TaskStatus.FINISHED:
            app.state.screens.forget_task(task.id)
        app.state.hub.publish(task.id, {"type": "task", "task": task_view(db, task)})
        if task.status == TaskStatus.DONE:
            _first_done(db, task)
    return before


def _first_done(db: Session, task: Task) -> None:
    done_before = db.scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.user_id == task.user_id, Task.status == TaskStatus.DONE, Task.id != task.id)
    )
    if not done_before:
        record_step(db, "first_task_done", user_id=task.user_id)


# -- API ---------------------------------------------------------------------------------


class TaskIn(InputModel):
    goal: str = Field(min_length=1, max_length=GOAL_LIMIT)
    # A task belongs to a project (a folder on the machine). Without one it gets a folder of its own, as it
    # did before projects: the older screens still start tasks that way.
    project_id: int | None = None
    repo_url: str = Field(default="", max_length=500)


class MessageIn(InputModel):
    text: str = Field(min_length=1, max_length=GOAL_LIMIT)


class InputIn(InputModel):
    request_id: str = Field(min_length=1, max_length=200)
    value: str | None = Field(default=None, max_length=GOAL_LIMIT)
    approved: bool | None = None


def owned_task(db: Session, user: User, task_id: int) -> Task:
    task = db.get(Task, task_id)
    if task is None or task.user_id != user.id:
        raise not_found("task")
    return task


def repo_problem(url: str, allow_local: bool) -> str:
    if not url:
        return ""
    if re.fullmatch(r"https://github\.com/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+(?:\.git)?", url):
        return "" if url.rsplit("/", 1)[-1] not in {".", ".."} else "bad_repo"
    if allow_local and urlparse(url).scheme == "file":
        return ""
    return "bad_repo"


def can_run(db: Session, user: User) -> None:
    """Raise the reason a task cannot start now, in words the person can act on."""
    if ledger.current_subscription(db, user.id) is None:
        raise ApiError(402, "no_subscription", "Start the trial first. / Сначала начни пробный период.")
    if not ledger.has_hours(db, user.id):
        raise ApiError(402, "no_hours", "Your hours are used up. / Часы закончились.")
    if not has_model_access(user):
        if user.model_provider == "chatgpt":
            raise ApiError(400, "chatgpt_login", "Sign in with ChatGPT again. / Войди через ChatGPT снова.")
        raise ApiError(400, "no_key", "Add your model key first. / Сначала добавь ключ модели.")
    from .machines import live_machine, machine_plan

    machine = live_machine(db, user.id)
    if machine_plan(db, user.id).own_machine and machine is None:
        raise ApiError(409, "no_machine", "Connect your machine first. / Сначала подключи свою машину.")
    if machine is not None and machine.suspended:
        raise ApiError(409, "machine_suspended", "The machine is stopped by the administrator. "
                       "/ Машина остановлена администратором.")


def _machine_for_task(app: FastAPI, db: Session, user: User) -> Machine:
    from .machines import live_machine, new_machine, provision, spawn

    machine = live_machine(db, user.id)
    if machine is not None and (machine.status != MachineStatus.ERROR or machine.provider_ref):
        return machine
    spec = new_machine(app, db, user)
    if machine is not None:
        # Never created at the provider, so nothing to clean up there; retiring the row keeps one machine per user.
        machine.status = MachineStatus.DELETED
        db.execute(update(Task).where(Task.machine_id == machine.id, Task.status.not_in(TaskStatus.FINISHED))
                   .values(machine_id=spec.machine_id))
    db.commit()
    spawn(app, provision(app, spec))
    return db.get(Machine, spec.machine_id)


def task_payload(db: Session, task: Task) -> dict[str, object]:
    view = task_view(db, task)
    view["balance_seconds"] = ledger.balance_seconds(db, task.user_id)
    return view


@router.post("/api/tasks", status_code=201)
async def create_task(
    payload: TaskIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    from .limits import admit

    app = request.app
    lock_account(db, user.id)
    can_run(db, user)
    project = None
    if payload.project_id is not None:
        from .projects import owned_project

        project = owned_project(db, user, payload.project_id)
        repo_url = project.repo_url
    else:
        repo_url = payload.repo_url.strip()
    if repo_problem(repo_url, app.state.settings.allow_local_endpoints):
        raise ApiError(400, "bad_repo", "That is not a repository link. / Это не ссылка на репозиторий.")
    machine = _machine_for_task(app, db, user)
    lock_account(db, user.id)
    first = not db.scalar(select(func.count()).select_from(Task).where(Task.user_id == user.id))
    task = Task(
        user_id=user.id,
        machine_id=machine.id,
        goal=payload.goal.strip(),
        repo_url=repo_url,
        project_id=project.id if project is not None else None,
    )
    db.add(task)
    db.flush()
    admit(db, task)
    record_user(app, db, task, task.goal)
    if first:
        record_step(db, "first_task_started", user_id=user.id)
    db.commit()
    await _dispatch(app, task.machine_id)
    return task_payload(db, task)


async def _dispatch(app: FastAPI, machine_id: int) -> None:
    from .gateway import dispatch

    await dispatch(app, machine_id)


@router.get("/api/tasks")
def list_tasks(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict[str, object]]:
    rows = db.scalars(select(Task).where(Task.user_id == user.id).order_by(Task.id.desc()).limit(100))
    return [task_view(db, task) for task in rows]


@router.get("/api/sessions/{public_id}")
def get_session(
    public_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    """A session by the id in its address. Someone else's, or one that does not exist, is the same answer."""
    task = db.scalar(select(Task).where(Task.public_id == public_id, Task.user_id == user.id))
    if task is None:
        raise not_found("session")
    return task_payload(db, task)


@router.get("/api/tasks/{task_id}")
def get_task(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, object]:
    return task_payload(db, owned_task(db, user, task_id))


@router.get("/api/tasks/{task_id}/events")
def get_events(
    task_id: int, after: int = 0, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    owned_task(db, user, task_id)
    rows = list(
        db.scalars(
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id, TaskEvent.seq > after)
            .order_by(TaskEvent.seq)
            .limit(HISTORY_PAGE)
        )
    )
    return {"events": [{"seq": row.seq, "event": row.payload} for row in rows], "more": len(rows) == HISTORY_PAGE}


@router.get("/api/tasks/{task_id}/changes")
def get_changes(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    """Files the task changed, from the writes' own diffs, in the order they were first touched."""
    owned_task(db, user, task_id)
    files: dict[str, dict] = {}
    rows = db.scalars(
        select(TaskEvent)
        .where(TaskEvent.task_id == task_id, TaskEvent.type == "action_result")
        .order_by(TaskEvent.seq)
    )
    for row in rows:
        event = row.payload
        path = event.get("path")
        if not path or not event.get("ok") or not event.get("patch"):
            continue
        entry = files.setdefault(path, {"path": path, "lines_added": 0, "lines_removed": 0, "patches": []})
        entry["lines_added"] += int(event.get("lines_added") or 0)
        entry["lines_removed"] += int(event.get("lines_removed") or 0)
        entry["patches"].append(event["patch"])
    return list(files.values())


async def _control(app: FastAPI, task: Task, action: str) -> bool:
    return await app.state.gateway.send(task.machine_id, {"type": "control", "task_id": task.id, "action": action})


@router.post("/api/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    lock_account(db, user.id)
    task = owned_task(db, user, task_id)
    if task.status in TaskStatus.FINISHED:
        return task_payload(db, task)
    if task.status in (TaskStatus.QUEUED, TaskStatus.PAUSED) or (
        task.status == TaskStatus.STARTING and task.started_at is None
    ):
        held_slot = task.status == TaskStatus.STARTING
        _finish(task, TaskStatus.CANCELLED, "cancelled")
        db.commit()
        request.app.state.hub.publish(task.id, {"type": "task", "task": task_view(db, task)})
        if held_slot:
            from .limits import admit_queued

            await admit_queued(request.app, task.machine_id)
        return task_payload(db, task)
    task.status_reason = "cancelled"
    db.commit()
    if not await _control(request.app, task, "cancel"):
        # No runner to tell: the process is gone with it, so the task is over.
        from .limits import admit_queued

        _finish(task, TaskStatus.CANCELLED)
        db.commit()
        await admit_queued(request.app, task.machine_id)
    return task_payload(db, task)


@router.post("/api/tasks/{task_id}/pause")
async def pause_task(
    task_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    lock_account(db, user.id)
    task = owned_task(db, user, task_id)
    if task.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_FOR_USER):
        raise ApiError(409, "not_running", "The task is not running. / Задача сейчас не выполняется.")
    task.status_reason = "user"
    db.commit()
    await _control(request.app, task, "pause")
    return task_payload(db, task)


@router.post("/api/tasks/{task_id}/resume")
async def resume_task(
    task_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    from .limits import admit

    lock_account(db, user.id)
    task = owned_task(db, user, task_id)
    if task.status != TaskStatus.PAUSED:
        raise ApiError(409, "not_paused", "Only a paused task can continue. / Продолжить можно только задачу на паузе.")
    can_run(db, user)
    task.status_reason = ""
    task.resume_requested = True
    task.started_at = None
    admit(db, task)
    db.commit()
    await _dispatch(request.app, task.machine_id)
    return task_payload(db, task)


def restart_with(db: Session, task: Task, text: str) -> None:
    """The session goes on in a new run of Kvist: it switches back into the conversation and takes `text`."""
    from .limits import admit

    task.pending_text = text
    task.resume_requested = False
    task.status_reason = ""
    task.outcome = ""
    task.final_message = ""
    task.finished_at = None
    task.started_at = None
    admit(db, task)


@router.post("/api/tasks/{task_id}/messages")
async def post_message(
    task_id: int,
    payload: MessageIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Another message in the conversation. A task is a session: it takes as many as the hours allow."""
    app = request.app
    lock_account(db, user.id)
    task = owned_task(db, user, task_id)
    text = payload.text.strip()
    if not text:
        raise ApiError(400, "empty", "Write something first. / Сначала напиши что-нибудь.")
    can_run(db, user)
    if task.status in (TaskStatus.QUEUED, TaskStatus.STARTING):
        raise ApiError(
            409,
            "not_ready",
            "The agent is still starting. Write again in a moment. / Агент ещё запускается: напиши через мгновение.",
        )
    if task.status in (TaskStatus.RUNNING, TaskStatus.WAITING_FOR_USER):
        # Kvist queues a goal that arrives during a turn and takes it when the turn ends.
        db.commit()
        delivered = await app.state.gateway.send(task.machine_id, {"type": "message", "task_id": task.id, "text": text})
        if not delivered:
            raise ApiError(503, "machine_offline", "The machine is not reachable. / Машина недоступна.")
        record_user(app, db, task, text)
        db.commit()
        return task_payload(db, task)
    restart_with(db, task, text)
    record_user(app, db, task, text)
    db.commit()
    await _dispatch(app, task.machine_id)
    return task_payload(db, task)


@router.post("/api/tasks/{task_id}/input")
async def answer_input(
    task_id: int,
    payload: InputIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    task = owned_task(db, user, task_id)
    if task.status != TaskStatus.WAITING_FOR_USER:
        raise ApiError(409, "not_waiting", "The agent is not waiting for an answer. / Агент сейчас ничего не ждёт.")
    can_run(db, user)
    response: dict[str, object] = {"request_id": payload.request_id}
    if payload.approved is not None:
        response["approved"] = payload.approved
    else:
        response["value"] = payload.value
    delivered = await request.app.state.gateway.send(
        task.machine_id, {"type": "input_response", "task_id": task.id, "response": response}
    )
    if not delivered:
        raise ApiError(503, "machine_offline", "The machine is not reachable. / Машина недоступна.")
    return {"ok": True}


def _same_origin(socket: WebSocket, public_url: str) -> bool:
    origin = socket.headers.get("origin")
    # Browsers always send Origin on a WebSocket; a missing one is a non-browser client with the cookie.
    return origin is None or origin.rstrip("/") == public_url


@router.websocket("/api/tasks/{task_id}/stream")
async def stream(socket: WebSocket, task_id: int, after: int = 0) -> None:
    """History after ``after``, then live events, until the browser goes away."""
    app = socket.app
    settings = app.state.settings
    if not _same_origin(socket, settings.public_url):
        await socket.close(code=4403)
        return
    with session_scope(app.state.session_factory) as db:
        user = user_from_cookie(db, settings, socket.cookies.get(SESSION_COOKIE))
        task = db.get(Task, task_id)
        allowed = (user is not None and not ban_active(user) and task is not None
                   and task.user_id == user.id)
    if not allowed:
        await socket.close(code=4401)
        return
    await socket.accept()
    # Subscribe before reading the history, so nothing falls between the two; the browser drops
    # a live event whose seq it already has.
    queue = app.state.hub.subscribe(task_id)
    try:
        with session_scope(app.state.session_factory) as db:
            task = db.get(Task, task_id)
            await socket.send_json({"type": "task", "task": task_payload(db, task)})
            rows = db.scalars(
                select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.seq > after).order_by(TaskEvent.seq)
            )
            for row in rows:
                await socket.send_json({"type": "event", "seq": row.seq, "event": row.payload})
        await socket.send_json({"type": "history_end"})
        # Screens are live only: a page that joins late is told which are open (owner only, checked above).
        for view in app.state.screens.views(task_id):
            await socket.send_json({"type": "event", "seq": None, "event": view})
        receiver = asyncio.create_task(_drain_browser(socket))
        try:
            while not receiver.done():
                getter = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
                if getter not in done:
                    getter.cancel()
                    break
                await socket.send_json(getter.result())
        finally:
            receiver.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        app.state.hub.unsubscribe(task_id, queue)
        # The browser is often gone by now (tab closed, laptop asleep, Ctrl+C on the server): closing then
        # raises WebSocketDisconnect, which is not news and must not reach the log as a traceback.
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await socket.close()


async def _drain_browser(socket: WebSocket) -> None:
    """The browser sends nothing we need; reading tells us when it leaves."""
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        while True:
            await socket.receive_text()
