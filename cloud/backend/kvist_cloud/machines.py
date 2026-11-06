# -*- coding: utf-8 -*-
"""Machines: created in the background at sign-in, woken for tasks, put to sleep when idle."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from collections.abc import Coroutine
from datetime import timedelta

from fastapi import APIRouter, Depends, FastAPI, Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from starlette.status import WS_1008_POLICY_VIOLATION

from . import ledger, plans
from .auth import current_user
from .db import get_db, lock_account, session_scope, utcnow
from .errors import ApiError, not_found
from .funnel import record_step
from .models import Export, Machine, MachineStatus, Task, TaskStatus, User
from .providers import MachineProvider, MachineSpec, ProviderError, ProviderState

log = logging.getLogger("kvist_cloud.machines")
router = APIRouter()
SWEEP_SECONDS = 30.0
# A machine that was asked to wake and never dialled in.
WAKE_TIMEOUT = timedelta(minutes=3)
REMOTE_PROVIDER = "remote"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def spawn(app: FastAPI, coroutine: Coroutine) -> None:
    """Run in the background, keeping a reference so the task is not collected half-way."""
    task = asyncio.get_running_loop().create_task(coroutine)
    app.state.background.add(task)
    task.add_done_callback(app.state.background.discard)


def machine_plan(db: Session, user_id: int) -> plans.Plan:
    subscription = ledger.current_subscription(db, user_id)
    return plans.get_plan(subscription.plan) if subscription is not None else plans.TRIAL


def provider_of(app: FastAPI, name: str) -> MachineProvider:
    """A customer's own machine has its own provider; every other machine lives with the hosting one."""
    return app.state.remote_provider if name == REMOTE_PROVIDER else app.state.provider


def machines_of(db: Session, user_id: int):
    """The person's machines for the plan they have now: ours, or their own when the plan is Remote.

    A machine left over from an earlier plan is not shown and not used; it is not touched either.
    """
    own = machine_plan(db, user_id).own_machine
    kind = Machine.provider == REMOTE_PROVIDER if own else Machine.provider != REMOTE_PROVIDER
    return select(Machine).where(Machine.user_id == user_id, Machine.status != MachineStatus.DELETED, kind)


def live_machine(db: Session, user_id: int) -> Machine | None:
    return db.scalars(machines_of(db, user_id).order_by(Machine.id)).first()


def owned_machine(db: Session, user: User, machine_id: int) -> Machine:
    machine = db.get(Machine, machine_id)
    if machine is None or machine.user_id != user.id or machine.status == MachineStatus.DELETED:
        raise not_found("machine")
    return machine


def display_status(db: Session, machine: Machine) -> str:
    """What the dashboard says: asleep, working, waiting for you."""
    statuses = set(
        db.scalars(select(Task.status).where(Task.machine_id == machine.id, Task.status.in_(TaskStatus.ACTIVE)))
    )
    if TaskStatus.WAITING_FOR_USER in statuses:
        return "waiting_for_you"
    if statuses:
        return "working"
    if machine.provider == REMOTE_PROVIDER and not machine.connected:
        return "offline"
    if machine.status == MachineStatus.AWAKE and machine.connected:
        return "ready"
    return machine.status


def machine_view(db: Session, machine: Machine) -> dict[str, object]:
    return {
        "id": machine.id,
        "status": machine.status,
        "display_status": display_status(db, machine),
        "detail": machine.status_detail,
        "connected": machine.connected,
        "vcpu": machine.vcpu,
        "memory_gb": machine.memory_gb,
        "disk_gb": machine.disk_gb,
        "created_at": machine.created_at.isoformat(),
    }


def new_machine(app: FastAPI, db: Session, user: User) -> MachineSpec:
    """Insert the row; the caller commits, then runs ``provision`` in the background."""
    plan = machine_plan(db, user.id)
    token = secrets.token_urlsafe(32)
    machine = Machine(
        user_id=user.id,
        provider=app.state.provider.name,
        token_hash=hash_token(token),
        vcpu=plan.vcpu,
        memory_gb=plan.memory_gb,
        disk_gb=plan.disk_gb,
        status=MachineStatus.CREATING,
    )
    db.add(machine)
    db.flush()
    return MachineSpec(machine.id, plan.vcpu, plan.memory_gb, plan.disk_gb, app.state.settings.gateway_url, token)


def _set_status(app: FastAPI, machine_id: int, status: str, detail: str = "", ref: str | None = None) -> None:
    with session_scope(app.state.session_factory) as db:
        machine = db.get(Machine, machine_id)
        if machine is None:
            return
        machine.status = status
        machine.status_detail = detail
        if ref is not None:
            machine.provider_ref = ref
        if status == MachineStatus.WAKING:
            machine.last_active_at = utcnow()


async def provision(app: FastAPI, spec: MachineSpec) -> None:
    try:
        ref = await app.state.provider.create(spec)
    except (ProviderError, NotImplementedError, OSError) as error:
        log.error("machine %s could not be created: %s", spec.machine_id, error)
        _set_status(app, spec.machine_id, MachineStatus.ERROR, "create_failed")
        return
    _set_status(app, spec.machine_id, MachineStatus.ASLEEP, ref=ref)
    # Woken right away: by the time the person reaches the task screen it is up.
    await wake(app, spec.machine_id)


async def wake(app: FastAPI, machine_id: int) -> None:
    with session_scope(app.state.session_factory) as db:
        machine = db.get(Machine, machine_id)
        if machine is None or machine.status in (MachineStatus.CREATING, MachineStatus.DELETED):
            return
        if machine.connected:
            machine.status = MachineStatus.AWAKE
            machine.last_active_at = utcnow()
            return
        if not app.state.settings.dev_billing and not ledger.has_hours(db, machine.user_id):
            return
        ref, provider = machine.provider_ref, provider_of(app, machine.provider)
        machine.status = MachineStatus.WAKING
        machine.last_active_at = utcnow()
    try:
        await provider.wake(ref)
    except (ProviderError, NotImplementedError, OSError) as error:
        log.error("machine %s could not wake: %s", machine_id, error)
        _set_status(app, machine_id, MachineStatus.ERROR, "wake_failed")


async def put_to_sleep(app: FastAPI, machine_id: int) -> None:
    with session_scope(app.state.session_factory) as db:
        machine = db.get(Machine, machine_id)
        if machine is None or machine.status in (MachineStatus.DELETED, MachineStatus.CREATING):
            return
        ref, provider = machine.provider_ref, provider_of(app, machine.provider)
        machine.status = MachineStatus.SLEEPING
    try:
        await provider.sleep(ref)
    except (ProviderError, NotImplementedError, OSError) as error:
        log.error("machine %s could not sleep: %s", machine_id, error)
        _set_status(app, machine_id, MachineStatus.ERROR, "sleep_failed")
        return
    _set_status(app, machine_id, MachineStatus.ASLEEP)


async def _gone_at_provider(provider: MachineProvider, ref: str) -> bool:
    try:
        return await provider.status(ref) == ProviderState.MISSING
    except (ProviderError, NotImplementedError, OSError):
        return False


async def reset(app: FastAPI, machine_id: int) -> int:
    """A clean machine in place of this one, which may be dead. Returns the new machine's id.

    The old one is removed first so two never exist at once. If the provider cannot remove it but it is
    already gone there, that is fine: a dead machine must be replaceable, or the person is stuck with it.
    Tasks that were not finished move to the new machine; their projects' folders are sent when it says hello.
    """
    with session_scope(app.state.session_factory) as db:
        machine = db.get(Machine, machine_id)
        ref, provider = machine.provider_ref, provider_of(app, machine.provider)
    try:
        if ref:
            await provider.delete(ref)
    except (ProviderError, NotImplementedError, OSError) as error:
        if not await _gone_at_provider(provider, ref):
            log.error("machine %s could not be removed for a reset: %s", machine_id, error)
            raise ApiError(502, "reset_failed", "The machine could not be reset right now, try again in a minute. "
                           "/ Не получилось сбросить машину, попробуй через минуту.") from error
    with session_scope(app.state.session_factory) as db:
        old = db.get(Machine, machine_id)
        old.status = MachineStatus.DELETED
        lock_account(db, old.user_id)
        spec = new_machine(app, db, db.get(User, old.user_id))
        db.execute(update(Task).where(Task.machine_id == machine_id, Task.status.not_in(TaskStatus.FINISHED))
                   .values(machine_id=spec.machine_id))
    spawn(app, provision(app, spec))
    return spec.machine_id


def _has_work(db: Session, machine: Machine) -> bool:
    active = db.scalar(
        select(func.count()).select_from(Task).where(Task.machine_id == machine.id, Task.status.in_(TaskStatus.ACTIVE))
    )
    pending = db.scalar(
        select(func.count()).select_from(Export).where(Export.machine_id == machine.id, Export.status == "pending")
    )
    return bool(active or pending)


async def sweep_once(app: FastAPI) -> None:
    """Sleep idle machines; give up on ones that never came up."""
    idle = timedelta(seconds=app.state.settings.machine_idle_sleep_seconds)
    now = utcnow()
    to_sleep: list[int] = []
    with session_scope(app.state.session_factory) as db:
        machines = db.scalars(select(Machine).where(Machine.status.in_((MachineStatus.AWAKE, MachineStatus.WAKING))))
        for machine in machines:
            if _has_work(db, machine):
                machine.last_active_at = now
                continue
            # A customer's own machine comes up when its owner starts the runner, which can be much later.
            never_came_up = (machine.status == MachineStatus.WAKING and not machine.connected
                             and machine.provider != REMOTE_PROVIDER)
            if never_came_up and now - machine.last_active_at > WAKE_TIMEOUT:
                machine.status = MachineStatus.ERROR
                machine.status_detail = "wake_timeout"
                continue
            if now - machine.last_active_at > idle:
                to_sleep.append(machine.id)
    for machine_id in to_sleep:
        await put_to_sleep(app, machine_id)


async def sweep_forever(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            await sweep_once(app)
        except Exception:  # noqa: BLE001 - the loop must survive one bad pass; the error is logged
            log.exception("machine sweep failed")


async def on_signed_in(request: Request, db: Session, user: User, _github_token: str) -> None:
    """Sign-in hook: the machine starts being created while the person enters their card."""
    app = request.app
    record_step(db, "github_login", user_id=user.id, visitor_id=request.cookies.get("kc_visitor", ""))
    if not app.state.settings.dev_billing and not ledger.has_hours(db, user.id):
        return
    if machine_plan(db, user.id).own_machine:
        return  # their machine arrives by itself, through "connect"
    lock_account(db, user.id)
    machine = live_machine(db, user.id)
    if machine is None:
        spec = new_machine(app, db, user)
        db.commit()
        spawn(app, provision(app, spec))
        return
    db.commit()
    if machine.status == MachineStatus.ASLEEP:
        spawn(app, wake(app, machine.id))


@router.get("/api/machines")
def list_machines(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict[str, object]]:
    machines = db.scalars(machines_of(db, user.id).order_by(Machine.id))
    return [machine_view(db, machine) for machine in machines]


@router.post("/api/machines", status_code=201)
async def create_machine(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    lock_account(db, user.id)
    _require_hours(request.app, db, user.id)
    if machine_plan(db, user.id).own_machine:
        raise ApiError(409, "own_machine", "This plan runs on your own machine: connect it instead. "
                       "/ Тариф работает на твоей машине: подключи её.")
    existing = live_machine(db, user.id)
    if existing is not None:
        return machine_view(db, existing)
    spec = new_machine(request.app, db, user)
    db.commit()
    spawn(request.app, provision(request.app, spec))
    return machine_view(db, db.get(Machine, spec.machine_id))


@router.post("/api/machines/remote/connect")
async def connect_remote_machine(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Make (or re-key) the row for the person's own machine and hand over what its runner needs, once.

    The token is shown only here and kept only as a hash. Asking again issues a new one and drops the old
    connection, so a leaked config is cured by pressing the button again.
    """
    lock_account(db, user.id)
    if not machine_plan(db, user.id).own_machine:
        raise ApiError(409, "not_remote", "This plan runs on our machines. / Этот тариф работает на наших машинах.")
    token = secrets.token_urlsafe(32)
    machine = live_machine(db, user.id)
    if machine is None:
        machine = Machine(user_id=user.id, provider=REMOTE_PROVIDER, provider_ref=f"remote-{user.id}",
                          token_hash=hash_token(token), vcpu=0, memory_gb=0, disk_gb=0, status=MachineStatus.ASLEEP)
        db.add(machine)
    else:
        machine.token_hash = hash_token(token)
    db.commit()
    old = request.app.state.gateway.sockets.get(machine.id)
    if old is not None:
        await old.close(code=WS_1008_POLICY_VIOLATION)
    return {"machine": machine_view(db, machine),
            "runner_config": {"machine_id": machine.id, "gateway_url": request.app.state.settings.gateway_url,
                              "token": token}}


@router.post("/api/machines/{machine_id}/wake", status_code=202)
async def wake_machine(
    machine_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    owned_machine(db, user, machine_id)
    _require_hours(request.app, db, user.id)
    spawn(request.app, wake(request.app, machine_id))
    return {"ok": True}


@router.post("/api/machines/{machine_id}/sleep", status_code=202)
async def sleep_machine(
    machine_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    machine = owned_machine(db, user, machine_id)
    if _has_work(db, machine):
        raise ApiError(409, "machine_busy", "A task is running on it. / На ней идёт задача.")
    spawn(request.app, put_to_sleep(request.app, machine_id))
    return {"ok": True}


@router.post("/api/machines/{machine_id}/reset")
async def reset_machine(
    machine_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Start over with a clean machine: for one that broke, hung or is simply gone. Files on its disk are lost."""
    lock_account(db, user.id)
    machine = owned_machine(db, user, machine_id)
    if machine.provider == REMOTE_PROVIDER:
        raise ApiError(409, "own_machine", "This is your own machine: connect it again instead. "
                       "/ Это твоя машина: подключи её заново.")
    if _has_work(db, machine):
        raise ApiError(409, "machine_busy", "A task is running on it. / На ней идёт задача.")
    _require_hours(request.app, db, user.id)
    db.commit()
    new_id = await reset(request.app, machine_id)
    db.expire_all()
    return machine_view(db, db.get(Machine, new_id))


def _require_hours(app: FastAPI, db: Session, user_id: int) -> None:
    if not app.state.settings.dev_billing and not ledger.has_hours(db, user_id):
        raise ApiError(402, "no_hours", "A paid plan with hours is required. / Нужен тариф с доступными часами.")
