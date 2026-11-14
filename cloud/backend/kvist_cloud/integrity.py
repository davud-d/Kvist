# -*- coding: utf-8 -*-
"""Read-only checks of the data that holds money and hours. Run it after a deploy, after a restore and from a timer.

``python -m kvist_cloud.integrity [--env-file PATH]`` exits 1 when a rule is broken. Things that need a person's
attention but are not corruption (a payment held for review, a stuck machine) are listed too, without failing.
It reads and never writes.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .config import ConfigError, load_settings, read_env_file
from .db import alembic_config, make_engine, make_session_factory, session_scope, utcnow
from .models import (
    BillingCheckout,
    CheckoutStatus,
    Dispute,
    LedgerEntry,
    LedgerKind,
    Machine,
    MachineStatus,
)

# Money that has been pending longer than this was abandoned or lost its callback; look at it.
STALE_PENDING = timedelta(days=2)
STALE_DISPUTE = timedelta(days=7)
REMOTE = "remote"


def schema_problems(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    url = engine.url.render_as_string(hide_password=False)
    head = ScriptDirectory.from_config(alembic_config(url)).get_current_head()
    if current != head:
        return [f"the schema is at revision {current}, the code expects {head}"]
    return []


def guard_problems(db: Session) -> list[str]:
    """The database itself must refuse to change or delete a ledger row, whatever the client is."""
    if db.get_bind().dialect.name == "sqlite":
        names = set(db.scalars(text("SELECT name FROM sqlite_master WHERE type = 'trigger'")))
        missing = {"ledger_no_update", "ledger_no_delete"} - names
    else:
        found = db.scalar(
            text("SELECT count(*) FROM pg_trigger WHERE tgname = 'ledger_no_change' AND NOT tgisinternal")
        )
        missing = set() if found else {"ledger_no_change"}
    return [f"the ledger's append-only guard is missing: {name}" for name in sorted(missing)]


def ledger_problems(db: Session) -> list[str]:
    found: list[str] = []
    wrong_sign = db.scalars(
        select(LedgerEntry.id).where(
            ((LedgerEntry.kind == LedgerKind.USAGE) & (LedgerEntry.seconds > 0))
            | (LedgerEntry.kind.in_((LedgerKind.GRANT, LedgerKind.PURCHASE, LedgerKind.REFUND))
               & (LedgerEntry.seconds <= 0))
        ).limit(20)
    ).all()
    if wrong_sign:
        found.append(f"ledger rows with the wrong sign for their kind: ids {wrong_sign}")
    known = select(LedgerEntry.id)
    dangling = db.scalars(
        select(LedgerEntry.id).where(LedgerEntry.refers_to.is_not(None), LedgerEntry.refers_to.not_in(known)).limit(20)
    ).all()
    if dangling:
        found.append(f"ledger rows that refer to a row that does not exist: ids {dangling}")
    return found


def checkout_problems(db: Session) -> list[str]:
    """A paid purchase must have produced its hours or its plan, and what it points at must exist."""
    found: list[str] = []
    for checkout in db.scalars(select(BillingCheckout).where(BillingCheckout.status == CheckoutStatus.PAID)):
        if checkout.ledger_entry_id is None and checkout.subscription_row_id is None:
            found.append(f"checkout {checkout.id} is paid but granted nothing")
        elif checkout.ledger_entry_id is not None and db.get(LedgerEntry, checkout.ledger_entry_id) is None:
            found.append(f"checkout {checkout.id} points at ledger row {checkout.ledger_entry_id}, which is missing")
    return found


def machine_problems(db: Session) -> list[str]:
    found: list[str] = []
    for remote in (False, True):
        kind = Machine.provider == REMOTE if remote else Machine.provider != REMOTE
        crowded = db.execute(
            select(Machine.user_id, func.count())
            .where(Machine.status != MachineStatus.DELETED, kind)
            .group_by(Machine.user_id)
            .having(func.count() > 1)
        ).all()
        label = "remote machines" if remote else "machines"
        found += [f"user {user_id} has {count} live {label}" for user_id, count in crowded]
    return found


def attention(db: Session) -> list[str]:
    """Not corruption; someone should look."""
    now = utcnow()
    notes: list[str] = []
    held = db.execute(
        select(BillingCheckout.status, func.count()).where(
            BillingCheckout.status.in_((CheckoutStatus.MISMATCH, CheckoutStatus.NEEDS_REVIEW))
        ).group_by(BillingCheckout.status)
    ).all()
    notes += [f"{count} payment(s) held as {status}: money may have arrived unapplied" for status, count in held]
    stale = db.scalar(
        select(func.count()).select_from(BillingCheckout).where(
            BillingCheckout.status == CheckoutStatus.PENDING, BillingCheckout.created_at < now - STALE_PENDING
        )
    )
    if stale:
        notes.append(f"{stale} checkout(s) pending over {STALE_PENDING.days} days (abandoned, or a lost callback)")
    errors = db.scalars(select(Machine.id).where(Machine.status == MachineStatus.ERROR)).all()
    if errors:
        notes.append(f"machines in error: ids {errors[:20]}")
    old_disputes = db.scalar(
        select(func.count()).select_from(Dispute).where(
            Dispute.status == "open", Dispute.created_at < now - STALE_DISPUTE
        )
    )
    if old_disputes:
        notes.append(f"{old_disputes} dispute(s) open for more than {STALE_DISPUTE.days} days")
    return notes


def check(engine: Engine) -> tuple[list[str], list[str]]:
    """(problems, things that need attention)."""
    problems = schema_problems(engine)
    with session_scope(make_session_factory(engine)) as db:
        problems += guard_problems(db) + ledger_problems(db) + checkout_problems(db) + machine_problems(db)
        return problems, attention(db)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", help="the server's env file (default: KVIST_CLOUD_ENV_FILE or cloud/.env)")
    args = parser.parse_args(argv)
    try:
        settings = load_settings({**read_env_file(Path(args.env_file)), **os.environ} if args.env_file else None)
    except ConfigError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    problems, notes = check(make_engine(settings.database_url))
    for line in notes:
        print(f"attention: {line}", file=sys.stderr)
    for line in problems:
        print(f"FAIL: {line}", file=sys.stderr)
    if not problems:
        print("ok: the schema, the ledger's guard and the money records are consistent")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
