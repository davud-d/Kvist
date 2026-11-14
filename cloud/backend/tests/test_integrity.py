# -*- coding: utf-8 -*-
"""The read-only data checks: a healthy database is quiet, each broken rule is named, held money is flagged."""
from __future__ import annotations

from datetime import timedelta

from conftest import make_machine, make_subscription, make_user
from sqlalchemy import text

from kvist_cloud import integrity
from kvist_cloud.db import utcnow
from kvist_cloud.models import BillingCheckout, CheckoutStatus, LedgerEntry, LedgerKind


def run(engine, db):
    db.commit()
    return integrity.check(engine)


def checkout(db, user, status, **fields) -> BillingCheckout:
    row = BillingCheckout(
        user_id=user.id, kind="hours", amount="1.00", currency="RUB", token=f"t{len(fields)}{status}{user.id}",
        status=status, **fields,
    )
    db.add(row)
    db.flush()
    return row


def test_a_healthy_database_has_nothing_to_report(engine, db):
    user = make_user(db)
    make_subscription(db, user)
    make_machine(db, user)
    assert run(engine, db) == ([], [])


def test_a_missing_ledger_guard_is_a_problem(engine, db):
    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER ledger_no_update"))
    problems, _ = run(engine, db)
    assert any("ledger_no_update" in line for line in problems)


def test_wrong_signs_and_dangling_references(engine, db):
    user = make_user(db)
    subscription = make_subscription(db, user)
    start = subscription.current_period_start
    db.add_all([
        LedgerEntry(user_id=user.id, period_start=start, kind=LedgerKind.USAGE, seconds=60),
        LedgerEntry(user_id=user.id, period_start=start, kind=LedgerKind.REFUND, seconds=60, refers_to=99999),
    ])
    problems, _ = run(engine, db)
    assert any("wrong sign" in line for line in problems)
    assert any("does not exist" in line for line in problems)


def test_a_paid_checkout_that_granted_nothing(engine, db):
    user = make_user(db)
    checkout(db, user, CheckoutStatus.PAID)
    problems, _ = run(engine, db)
    assert any("granted nothing" in line for line in problems)


def test_two_live_machines_for_one_account(engine, db):
    user = make_user(db)
    make_machine(db, user)
    make_machine(db, user)
    problems, _ = run(engine, db)
    assert any("2 live machines" in line for line in problems)


def test_held_and_stale_payments_need_attention_but_do_not_fail(engine, db):
    user = make_user(db)
    checkout(db, user, CheckoutStatus.NEEDS_REVIEW)
    old = checkout(db, user, CheckoutStatus.PENDING)
    old.created_at = utcnow() - timedelta(days=3)
    problems, notes = run(engine, db)
    assert problems == []
    assert any("needs_review" in line for line in notes)
    assert any("pending" in line for line in notes)
