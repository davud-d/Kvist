# -*- coding: utf-8 -*-
"""Every rule of docs/ARCHITECTURE.md, "Hours ledger rules"."""
from __future__ import annotations

import sqlite3

import pytest
from conftest import make_machine, make_subscription, make_task, make_user
from sqlalchemy import delete, select, text, update

from kvist_cloud import ledger
from kvist_cloud.db import LedgerAppendOnlyError
from kvist_cloud.models import LedgerEntry, LedgerKind, TaskStatus

GAP = 45.0
T0 = 1_800_000_000.0


def beat(db, machine, running, at):
    return ledger.record_heartbeat(db, machine, [task.id for task in running], at, GAP)


def usage_rows(db):
    return list(db.scalars(select(LedgerEntry).where(LedgerEntry.kind == LedgerKind.USAGE)))


@pytest.fixture
def account(db):
    user = make_user(db)
    make_subscription(db, user, "trial")
    machine = make_machine(db, user)
    return user, machine


def test_running_time_between_heartbeats_is_billed(db, account):
    user, machine = account
    task = make_task(db, user, machine)
    beat(db, machine, [task], T0)
    result = beat(db, machine, [task], T0 + 15)
    assert result.billed_seconds == 15
    assert ledger.task_used_seconds(db, task.id) == 15
    assert ledger.balance_seconds(db, user.id) == 5 * 3600 - 15


@pytest.mark.parametrize(
    "status",
    [TaskStatus.WAITING_FOR_USER, TaskStatus.PAUSED, TaskStatus.DONE, TaskStatus.CANCELLED,
     TaskStatus.FAILED, TaskStatus.QUEUED, TaskStatus.STARTING],
)
def test_time_outside_running_is_free(db, account, status):
    user, machine = account
    task = make_task(db, user, machine, status=status)
    # Even if a runner claims the task is running, the server's status decides.
    beat(db, machine, [task], T0)
    result = beat(db, machine, [task], T0 + 15)
    assert result.billed_seconds == 0
    assert usage_rows(db) == []


def test_status_change_stops_the_meter_at_the_next_heartbeat(db, account):
    user, machine = account
    task = make_task(db, user, machine)
    beat(db, machine, [task], T0)
    task.status = TaskStatus.WAITING_FOR_USER
    # The runner sends a heartbeat at once when the set changes: the interval before it was running.
    assert beat(db, machine, [], T0 + 7).billed_seconds == 7
    assert beat(db, machine, [], T0 + 22).billed_seconds == 0
    task.status = TaskStatus.RUNNING
    assert beat(db, machine, [task], T0 + 300).billed_seconds == 0
    assert beat(db, machine, [task], T0 + 315).billed_seconds == 15
    assert ledger.task_used_seconds(db, task.id) == 22


def test_without_heartbeats_nothing_is_billed(db, account):
    user, machine = account
    task = make_task(db, user, machine)
    assert beat(db, machine, [task], T0).billed_seconds == 0
    assert usage_rows(db) == []


def test_lost_heartbeats_stop_the_meter_at_the_last_one(db, account):
    user, machine = account
    task = make_task(db, user, machine)
    beat(db, machine, [task], T0)
    beat(db, machine, [task], T0 + 15)
    # Ten minutes of silence: not billed, and the meter restarts from the next heartbeat.
    assert beat(db, machine, [task], T0 + 615).billed_seconds == 0
    assert beat(db, machine, [task], T0 + 630).billed_seconds == 15
    assert ledger.task_used_seconds(db, task.id) == 30


def test_a_disconnect_stops_the_meter(db, account):
    user, machine = account
    task = make_task(db, user, machine)
    beat(db, machine, [task], T0)
    ledger.stop_meter(machine)
    assert beat(db, machine, [task], T0 + 10).billed_seconds == 0
    assert beat(db, machine, [task], T0 + 25).billed_seconds == 15


def test_parallel_tasks_on_one_machine_count_once(db):
    user = make_user(db)
    make_subscription(db, user, "pro")
    machine = make_machine(db, user)
    first, second, third = (make_task(db, user, machine) for _ in range(3))
    beat(db, machine, [first, second, third], T0)
    result = beat(db, machine, [first, second, third], T0 + 16)
    assert result.billed_seconds == 16
    assert result.per_task == {first.id: 6, second.id: 5, third.id: 5}
    assert sum(-row.seconds for row in usage_rows(db)) == 16
    assert ledger.balance_seconds(db, user.id) == 150 * 3600 - 16


def test_two_machines_are_billed_separately(db):
    user = make_user(db)
    make_subscription(db, user, "pro")
    one, two = make_machine(db, user), make_machine(db, user)
    task_one, task_two = make_task(db, user, one), make_task(db, user, two)
    beat(db, one, [task_one], T0)
    beat(db, two, [task_two], T0)
    beat(db, one, [task_one], T0 + 15)
    beat(db, two, [task_two], T0 + 15)
    assert ledger.balance_seconds(db, user.id) == 150 * 3600 - 30


def test_accounting_is_per_second_without_drift(db, account):
    user, machine = account
    task = make_task(db, user, machine)
    stamps = [T0 + 0.9, T0 + 16.1, T0 + 31.4, T0 + 46.95, T0 + 61.2]
    for stamp in stamps:
        beat(db, machine, [task], stamp)
    # Floors of absolute times telescope: 61 - 0, nothing lost or invented between beats.
    assert ledger.task_used_seconds(db, task.id) == 61
    assert all(isinstance(row.seconds, int) for row in usage_rows(db))


def test_minutes_are_shown_rounded_down():
    assert ledger.minutes(59) == 0
    assert ledger.minutes(119) == 1
    assert ledger.minutes(-120) == 2


def test_hours_running_out_caps_the_charge(db):
    user = make_user(db)
    make_subscription(db, user, "trial", hours=10 / 3600)
    machine = make_machine(db, user)
    task = make_task(db, user, machine)
    beat(db, machine, [task], T0)
    result = beat(db, machine, [task], T0 + 15)
    assert (result.billed_seconds, result.balance_seconds, result.exhausted) == (10, 0, True)
    # The grace minutes after the balance hits zero are free; nothing is deleted.
    result = beat(db, machine, [task], T0 + 30)
    assert (result.billed_seconds, result.exhausted) == (0, True)
    assert ledger.balance_seconds(db, user.id) == 0
    assert ledger.task_used_seconds(db, task.id) == 10


def test_no_subscription_means_no_billing(db):
    user = make_user(db)
    machine = make_machine(db, user)
    task = make_task(db, user, machine)
    beat(db, machine, [task], T0)
    result = beat(db, machine, [task], T0 + 15)
    assert (result.billed_seconds, result.exhausted) == (0, True)


def test_refund_is_a_new_row_with_a_reason(db, account):
    user, machine = account
    task = make_task(db, user, machine)
    beat(db, machine, [task], T0)
    beat(db, machine, [task], T0 + 15)
    charged = usage_rows(db)[0]
    with pytest.raises(ledger.LedgerError):
        ledger.refund(db, user.id, 15, "  ")
    ledger.refund(db, user.id, 15, "disputed: the model hung", refers_to=charged.id, task_id=task.id)
    assert ledger.balance_seconds(db, user.id) == 5 * 3600
    assert charged.seconds == -15


def test_grant_is_written_once_per_period(db):
    user = make_user(db)
    subscription = make_subscription(db, user, "start")
    assert ledger.grant_period(db, subscription, 50 * 3600, "again") is None
    assert ledger.balance_seconds(db, user.id) == 50 * 3600


def test_orm_refuses_update_and_delete(db, account):
    user, machine = account
    task = make_task(db, user, machine)
    beat(db, machine, [task], T0)
    beat(db, machine, [task], T0 + 15)
    db.commit()
    row = usage_rows(db)[0]
    row.seconds = 0
    with pytest.raises(LedgerAppendOnlyError):
        db.flush()
    db.rollback()
    db.delete(usage_rows(db)[0])
    with pytest.raises(LedgerAppendOnlyError):
        db.flush()
    db.rollback()


def test_bulk_and_raw_statements_are_refused(db, account):
    user, _machine = account
    db.commit()
    with pytest.raises(LedgerAppendOnlyError):
        db.execute(update(LedgerEntry).values(seconds=0))
    db.rollback()
    with pytest.raises(LedgerAppendOnlyError):
        db.execute(delete(LedgerEntry))
    db.rollback()
    with pytest.raises(LedgerAppendOnlyError):
        db.execute(text("UPDATE ledger_entries SET seconds = 0"))
    db.rollback()
    with pytest.raises(LedgerAppendOnlyError):
        db.execute(text("delete from ledger_entries"))
    db.rollback()
    assert ledger.balance_seconds(db, user.id) == 5 * 3600


def test_database_trigger_refuses_changes_from_any_client(db, account, database_url):
    db.commit()
    path = database_url.removeprefix("sqlite:///")
    connection = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("UPDATE ledger_entries SET seconds = 0")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("DELETE FROM ledger_entries")
    finally:
        connection.close()


def test_ledger_module_never_updates_or_deletes():
    source = (ledger.__file__ and open(ledger.__file__, encoding="utf-8").read()) or ""
    assert "update(" not in source.replace("onupdate", "")
    assert "delete(" not in source
    assert ".delete" not in source
