# -*- coding: utf-8 -*-
"""The person deletes their own account (the app stores require it in the app, not by writing to support).

Asking for it signs the person out everywhere, puts the machine to sleep and stops the plan from renewing; nothing
is erased yet, because the law asks for 90 days. Signing in again within them cancels the deletion (auth.py) and
everything is as it was. After them, ``purge_forever`` erases the account:

- gone: the machines (removed at the provider first; a failure there leaves the account for the next pass, so no
  machine outlives it), the projects, the sessions' text and events, the exports and their files, notifications,
  queued mail, model keys, the ChatGPT and GitHub tokens, and every personal field of the row;
- kept, as the privacy policy says (payment data for 3 years, for disputed payments and refunds): the users row as a
  nameless tombstone, because ledger entries, checkouts and subscriptions refer to it, and those records. If the
  trial was used, the GitHub id stays in ``trial_github_id`` so the same GitHub account gets no second trial.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from . import apple
from .auth import SESSION_COOKIE, _ide_cookie, signed_user
from .db import get_db, lock_account, session_scope, utcnow
from .exports import archive_path
from .machines import _gone_at_provider, provider_of, put_to_sleep, spawn
from .models import (
    ChatGPTLogin,
    Dispute,
    Export,
    FunnelEvent,
    Machine,
    MachineStatus,
    Notification,
    Outbox,
    Project,
    SearchUse,
    Subscription,
    SubscriptionStatus,
    Task,
    TaskEvent,
    TaskStatus,
    User,
)
from .providers.base import ProviderError
from .validation import InputModel

log = logging.getLogger("kvist_cloud.account")
router = APIRouter()
GRACE = timedelta(days=90)
PURGE_SECONDS = 3600
DELETED_LOGIN = "deleted"


class DeleteIn(InputModel):
    # The page asks twice; the body says the second answer was yes.
    confirm: Literal[True]


@router.post("/api/me/delete")
async def request_deletion(payload: DeleteIn, request: Request, user: User = Depends(signed_user),
                           db: Session = Depends(get_db)) -> JSONResponse:
    """Any signed-in person may: a banned one and one who never accepted the terms too."""
    lock_account(db, user.id)
    now = utcnow()
    user.delete_requested_at = now
    user.session_version += 1
    db.execute(update(Subscription).where(Subscription.user_id == user.id,
                                          Subscription.status.in_(SubscriptionStatus.USABLE))
               .values(cancel_at_period_end=True))
    awake = list(db.scalars(select(Machine.id).where(
        Machine.user_id == user.id, Machine.status.in_((MachineStatus.AWAKE, MachineStatus.WAKING)))))
    sealed, user.apple_token_enc = user.apple_token_enc, ""
    db.commit()
    # Apple asks for the token to be revoked when the person deletes the account; signing in with Apple again
    # within the grace period makes a new one.
    await apple.revoke(request.app, sealed)
    for machine_id in awake:
        spawn(request.app, put_to_sleep(request.app, machine_id))
    log.info("account %s asked to be deleted", user.id)
    response = JSONResponse({"erase_after": (now + GRACE).isoformat()})
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.headers.append("set-cookie", _ide_cookie('""', 0))
    return response


async def _remove_machines(app: FastAPI, user_id: int) -> bool:
    """True once no machine of the account is left at its provider."""
    with session_scope(app.state.session_factory) as db:
        machines = [(m.id, m.provider, m.provider_ref) for m in db.scalars(select(Machine).where(
            Machine.user_id == user_id, Machine.status != MachineStatus.DELETED, Machine.provider_ref != ""))]
    for machine_id, name, ref in machines:
        provider = provider_of(app, name)
        try:
            await provider.delete(ref)
        except (ProviderError, NotImplementedError, OSError) as error:
            if await _gone_at_provider(provider, ref):
                continue
            log.error("machine %s could not be removed for an account deletion: %s", machine_id, error)
            return False
    return True


def _erase_work(app: FastAPI, db: Session, user_id: int) -> None:
    task_ids = select(Task.id).where(Task.user_id == user_id)
    db.execute(delete(TaskEvent).where(TaskEvent.task_id.in_(task_ids)))
    # The rows stay because ledger entries point at them; what the person wrote and got back does not.
    db.execute(update(Task).where(Task.user_id == user_id, Task.status.not_in(TaskStatus.FINISHED))
               .values(status=TaskStatus.CANCELLED, finished_at=utcnow()))
    db.execute(update(Task).where(Task.user_id == user_id).values(
        goal="", title="", repo_url="", final_message="", pending_text="", start_envelope="",
        pending_command=False, resume_requested=False))
    for export_id in db.scalars(select(Export.id).where(Export.user_id == user_id)):
        archive_path(app, export_id).unlink(missing_ok=True)
    for table in (Export, Project, Notification, Outbox, ChatGPTLogin, SearchUse):
        db.execute(delete(table).where(table.user_id == user_id))
    db.execute(update(Dispute).where(Dispute.user_id == user_id).values(message="", resolution=""))
    db.execute(update(FunnelEvent).where(FunnelEvent.user_id == user_id).values(user_id=None))
    db.execute(update(Machine).where(Machine.user_id == user_id)
               .values(status=MachineStatus.DELETED, connected=False, provider_ref=""))
    db.execute(update(Subscription).where(Subscription.user_id == user_id,
                                          Subscription.status != SubscriptionStatus.CANCELLED)
               .values(status=SubscriptionStatus.CANCELLED, cancel_at_period_end=False))


def _anonymize(user: User) -> None:
    user.trial_github_id = user.github_id if user.trial_used_at is not None else None
    user.github_id = None
    user.apple_sub = None
    user.apple_token_enc = ""
    user.github_login = DELETED_LOGIN
    user.name = user.avatar_url = user.email = user.github_token_enc = ""
    user.github_created_at = None
    user.model_provider = user.model_key_enc = user.model_key_last4 = ""
    user.model_base_url = user.model_name = user.model_headers_enc = user.model_header_names = ""
    user.chatgpt_enc = user.chatgpt_state = user.chatgpt_email = ""
    user.chatgpt_checked_at = None
    user.timezone = ""
    user.search_credits = 0
    user.is_admin = False
    user.terms_accepted_at = None
    user.session_version += 1
    user.deleted_at = utcnow()


async def purge(app: FastAPI, user_id: int) -> bool:
    """Erase one account whose grace period is over; False when a machine would not go (tried again later)."""
    if not await _remove_machines(app, user_id):
        return False
    with session_scope(app.state.session_factory) as db:
        lock_account(db, user_id)
        user = db.get(User, user_id)
        # Signed in again while the machines were being removed: the deletion is off.
        if user.delete_requested_at is None:
            return False
        _erase_work(app, db, user_id)
        _anonymize(user)
    log.info("account %s erased", user_id)
    return True


async def purge_due(app: FastAPI) -> None:
    cutoff = utcnow() - GRACE
    with session_scope(app.state.session_factory) as db:
        due = list(db.scalars(select(User.id).where(User.deleted_at.is_(None),
                                                    User.delete_requested_at < cutoff)))
    for user_id in due:
        await purge(app, user_id)


async def purge_forever(app: FastAPI) -> None:
    while True:
        try:
            await purge_due(app)
        except Exception:  # noqa: BLE001 - the loop must survive one bad pass; the error is logged
            log.exception("account purge failed")
        await asyncio.sleep(PURGE_SECONDS)
