# -*- coding: utf-8 -*-
"""The admin's hands on accounts: look people up, give a plan or hours, make admins, stop a machine, ban.

Every change writes an ``AdminAction`` row (who did what to whom) and, when it concerns what the person
has, a bell notice to them. Nothing here reaches into a machine: an admin sees no files and no screens of a
customer (screens.py keeps those for the owner), and a customer's own machine (Remote) cannot be stopped.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import billing, ledger, machines, plans, tasks
from .auth import admin_user, ban_view, is_admin, is_owner
from .db import get_db, lock_account, session_scope, utcnow
from .errors import ApiError, not_found
from .models import AdminAction, LedgerEntry, Machine, MachineStatus, Task, TaskStatus, User
from .notifications import notify
from .validation import InputModel

router = APIRouter()
PAGE_SIZE = 50
HISTORY_ROWS = 20


class PlanIn(InputModel):
    plan: Literal["trial", "start", "pro", "max", "remote"]
    # How long it lasts; empty = the plan's own period.
    days: int | None = Field(default=None, ge=1, le=3660)
    note: str = Field(default="", max_length=300)


class HoursIn(InputModel):
    # Positive gives hours, negative takes them back.
    minutes: int = Field(ge=-100_000, le=100_000)
    note: str = Field(min_length=3, max_length=300)


class AdminRightsIn(InputModel):
    value: bool


class BanIn(InputModel):
    permanent: bool = False
    days: int | None = Field(default=None, ge=1, le=3650)
    reason: str = Field(min_length=3, max_length=300)

    @model_validator(mode="after")
    def _one_length(self) -> BanIn:
        if self.permanent == (self.days is not None):
            raise ValueError("a ban is either permanent or has a number of days")
        return self


def _target(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise not_found("user")
    return user


def _record(db: Session, admin: User, user: User, action: str, **detail: object) -> None:
    db.add(AdminAction(admin_id=admin.id, user_id=user.id, action=action, detail=detail))


def _machine_row(db: Session, machine: Machine) -> dict[str, object]:
    return {"id": machine.id, "provider": machine.provider, "status": machine.status,
            "display_status": machines.display_status(db, machine), "connected": machine.connected,
            "suspended": machine.suspended}


def _user_row(db: Session, app: FastAPI, user: User) -> dict[str, object]:
    settings = app.state.settings
    subscription = ledger.current_subscription(db, user.id)
    plan = plans.get_plan(subscription.plan) if subscription is not None else None
    machine = machines.live_machine(db, user.id)
    return {
        "id": user.id,
        "login": user.github_login,
        "name": user.name,
        "email": user.email,
        "avatar_url": user.avatar_url,
        "created_at": user.created_at.isoformat(),
        "plan": plan.id if plan is not None else None,
        "plan_ends_at": subscription.current_period_end.isoformat() if subscription is not None else None,
        "own_machine": bool(plan is not None and plan.own_machine),
        "balance_minutes": ledger.minutes(max(ledger.balance_seconds(db, user.id), 0)) if subscription else 0,
        "machine": _machine_row(db, machine) if machine is not None else None,
        "is_admin": is_admin(settings, user),
        "is_owner": is_owner(settings, user),
        "ban": ban_view(user),
    }


@router.get("/api/admin/users")
def list_users(request: Request, q: str = "", offset: int = 0, _admin: User = Depends(admin_user),
               db: Session = Depends(get_db)) -> dict[str, object]:
    query = select(User).order_by(User.id.desc())
    term = q.strip()
    if term:
        like = f"%{term.lower().replace('%', '').replace('_', '')}%"
        match = [func.lower(User.github_login).like(like), func.lower(User.name).like(like),
                 func.lower(User.email).like(like)]
        if term.isdigit():
            match.append(User.id == int(term))
        query = query.where(or_(*match))
    rows = list(db.scalars(query.offset(max(offset, 0)).limit(PAGE_SIZE + 1)))
    return {"users": [_user_row(db, request.app, user) for user in rows[:PAGE_SIZE]],
            "more": len(rows) > PAGE_SIZE}


@router.get("/api/admin/users/{user_id}")
def get_user(user_id: int, request: Request, _admin: User = Depends(admin_user),
             db: Session = Depends(get_db)) -> dict[str, object]:
    user = _target(db, user_id)
    entries = db.scalars(select(LedgerEntry).where(LedgerEntry.user_id == user.id)
                         .order_by(LedgerEntry.id.desc()).limit(HISTORY_ROWS))
    actions = list(db.scalars(select(AdminAction).where(AdminAction.user_id == user.id)
                              .order_by(AdminAction.id.desc()).limit(HISTORY_ROWS)))
    admin_ids = {a.admin_id for a in actions}
    logins = dict(db.execute(select(User.id, User.github_login).where(User.id.in_(admin_ids))).all())
    return {
        **_user_row(db, request.app, user),
        "subscription": billing.subscription_view(db, user),
        "ledger": [{"kind": e.kind, "minutes": ledger.minutes(e.seconds), "negative": e.seconds < 0,
                    "reason": e.reason, "at": e.created_at.isoformat()} for e in entries],
        "actions": [{"admin": logins.get(a.admin_id, "?"), "action": a.action, "detail": a.detail,
                     "at": a.created_at.isoformat()} for a in actions],
        "plans": list(plans.PLANS),
    }


@router.post("/api/admin/users/{user_id}/plan")
def give_plan(user_id: int, payload: PlanIn, admin: User = Depends(admin_user),
              db: Session = Depends(get_db)) -> dict[str, object]:
    user = _target(db, user_id)
    lock_account(db, user.id)
    now = utcnow()
    subscription = billing.admin_grant_plan(db, user.id, payload.plan, payload.days, now)
    note = payload.note.strip()
    notify(db, user.id, "plan_granted", plan=payload.plan, until=subscription.current_period_end.isoformat(),
           note=note)
    _record(db, admin, user, "plan", plan=payload.plan, days=payload.days, note=note)
    db.commit()
    return {"ok": True, "plan": payload.plan, "until": subscription.current_period_end.isoformat()}


@router.post("/api/admin/users/{user_id}/hours")
def give_hours(user_id: int, payload: HoursIn, admin: User = Depends(admin_user),
               db: Session = Depends(get_db)) -> dict[str, object]:
    if payload.minutes == 0:
        raise ApiError(400, "bad_request", "Minutes cannot be zero. / Минуты не могут быть нулём.")
    user = _target(db, user_id)
    lock_account(db, user.id)
    note = payload.note.strip()
    try:
        ledger.adjust(db, user.id, payload.minutes * 60, note)
    except ledger.LedgerError as error:
        raise ApiError(409, "hours_refused", str(error)) from error
    notify(db, user.id, "hours_granted" if payload.minutes > 0 else "hours_removed",
           minutes=abs(payload.minutes), note=note)
    _record(db, admin, user, "hours", minutes=payload.minutes, note=note)
    db.commit()
    return {"ok": True, "balance_minutes": ledger.minutes(max(ledger.balance_seconds(db, user.id), 0))}


@router.post("/api/admin/users/{user_id}/admin")
def set_admin(user_id: int, payload: AdminRightsIn, request: Request, admin: User = Depends(admin_user),
              db: Session = Depends(get_db)) -> dict[str, object]:
    """Only the owners (ADMIN_GITHUB_LOGINS) hand out admin rights, so an admin cannot appoint a successor."""
    settings = request.app.state.settings
    if not is_owner(settings, admin):
        raise ApiError(403, "owners_only", "Only an owner can do this. / Это может только владелец.")
    user = _target(db, user_id)
    if is_owner(settings, user):
        raise ApiError(409, "owner", "An owner's rights come from the server settings. "
                       "/ Права владельца задаются в настройках сервера.")
    if user.is_admin != payload.value:
        user.is_admin = payload.value
        notify(db, user.id, "admin_granted" if payload.value else "admin_revoked")
        _record(db, admin, user, "admin", value=payload.value)
    db.commit()
    return {"ok": True, "is_admin": user.is_admin}


def _vm_machines(db: Session, user: User) -> list[Machine]:
    """The customer's machines of ours; empty on Remote, which is their own hardware."""
    if machines.machine_plan(db, user.id).own_machine:
        return []
    return list(db.scalars(machines.machines_of(db, user.id)))


async def stop_work(app: FastAPI, machine_ids: list[int], sleep: bool) -> None:
    """Pause everything unfinished on these machines; ours also go to sleep, a customer's own are told to pause."""
    to_tell: list[tuple[int, int]] = []
    with session_scope(app.state.session_factory) as db:
        for task in db.scalars(select(Task).where(Task.machine_id.in_(machine_ids),
                                                  Task.status.not_in(TaskStatus.FINISHED))):
            if task.status in TaskStatus.ACTIVE:
                to_tell.append((task.machine_id, task.id))
            tasks.apply_exit(task, "machine_suspended")
            app.state.hub.publish(task.id, {"type": "task", "task": tasks.task_view(db, task)})
    if sleep:
        for machine_id in machine_ids:
            await machines.put_to_sleep(app, machine_id)
        return
    for machine_id, task_id in to_tell:
        await app.state.gateway.send(machine_id, {"type": "control", "task_id": task_id, "action": "pause"})


@router.post("/api/admin/users/{user_id}/machine/suspend")
async def suspend_machine(user_id: int, request: Request, admin: User = Depends(admin_user),
                          db: Session = Depends(get_db)) -> dict[str, object]:
    user = _target(db, user_id)
    lock_account(db, user.id)
    if machines.machine_plan(db, user.id).own_machine:
        raise ApiError(409, "own_machine", "This customer's machine is their own and cannot be stopped. "
                       "/ Машина клиента — его собственная, остановить её нельзя.")
    targets = _vm_machines(db, user)
    if not targets:
        raise not_found("machine")
    for machine in targets:
        machine.suspended = True
    notify(db, user.id, "machine_suspended")
    _record(db, admin, user, "suspend")
    db.commit()
    await stop_work(request.app, [m.id for m in targets], sleep=True)
    return {"ok": True}


@router.post("/api/admin/users/{user_id}/machine/resume")
def resume_machine(user_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    user = _target(db, user_id)
    lock_account(db, user.id)
    targets = [m for m in db.scalars(select(Machine).where(Machine.user_id == user.id, Machine.suspended.is_(True),
                                                           Machine.status != MachineStatus.DELETED))]
    if not targets:
        raise not_found("machine")
    for machine in targets:
        machine.suspended = False
    notify(db, user.id, "machine_resumed")
    _record(db, admin, user, "resume")
    db.commit()
    return {"ok": True}


def _check_can_ban(settings, admin: User, user: User) -> None:
    if user.id == admin.id:
        raise ApiError(409, "self_ban", "You cannot ban yourself. / Нельзя заблокировать себя.")
    if is_owner(settings, user):
        raise ApiError(409, "owner", "An owner cannot be banned. / Владельца заблокировать нельзя.")
    if is_admin(settings, user) and not is_owner(settings, admin):
        raise ApiError(403, "owners_only", "Only an owner can do this. / Это может только владелец.")


@router.post("/api/admin/users/{user_id}/ban")
async def ban(user_id: int, payload: BanIn, request: Request, admin: User = Depends(admin_user),
              db: Session = Depends(get_db)) -> dict[str, object]:
    """The account stops working at once: the API answers "banned", its machines stop, its tasks pause."""
    user = _target(db, user_id)
    _check_can_ban(request.app.state.settings, admin, user)
    lock_account(db, user.id)
    user.ban_permanent = payload.permanent
    user.ban_until = None if payload.permanent else utcnow() + timedelta(days=payload.days)
    user.ban_reason = payload.reason.strip()
    _record(db, admin, user, "ban", permanent=payload.permanent, days=payload.days, reason=user.ban_reason)
    own = machines.machine_plan(db, user.id).own_machine
    ids = [m.id for m in db.scalars(select(Machine).where(Machine.user_id == user.id,
                                                          Machine.status != MachineStatus.DELETED))]
    db.commit()
    await stop_work(request.app, ids, sleep=not own)
    return {"ok": True, "ban": ban_view(user)}


@router.post("/api/admin/users/{user_id}/unban")
def unban(user_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)) -> dict[str, object]:
    user = _target(db, user_id)
    if not (user.ban_permanent or user.ban_until):
        raise ApiError(409, "not_banned", "This account is not banned. / Аккаунт не заблокирован.")
    lock_account(db, user.id)
    user.ban_permanent = False
    user.ban_until = None
    user.ban_reason = ""
    notify(db, user.id, "unbanned")
    _record(db, admin, user, "unban")
    db.commit()
    return {"ok": True}
