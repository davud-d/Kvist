# -*- coding: utf-8 -*-
"""Tables. ledger_entries is append-only: the ORM refuses UPDATE and DELETE on it."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, ORMExecuteState, Session, mapped_column

from .db import Base, LedgerAppendOnlyError, UTCDateTime, utcnow


class TaskStatus:
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    PAUSED = "paused"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"

    # Hold a parallel-task slot on the machine.
    ACTIVE = frozenset({STARTING, RUNNING, WAITING_FOR_USER})
    FINISHED = frozenset({DONE, CANCELLED, FAILED})


class MachineStatus:
    CREATING = "creating"
    ASLEEP = "asleep"
    WAKING = "waking"
    AWAKE = "awake"
    SLEEPING = "sleeping"
    ERROR = "error"
    DELETED = "deleted"


class SubscriptionStatus:
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"

    USABLE = frozenset({TRIALING, ACTIVE})


class LedgerKind:
    GRANT = "grant"
    PURCHASE = "purchase"
    REFUND = "refund"
    USAGE = "usage"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    github_login: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200), default="")
    avatar_url: Mapped[str] = mapped_column(String(500), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    github_token_enc: Mapped[str] = mapped_column(Text, default="")
    # Raised to sign out every cookie at once.
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    model_provider: Mapped[str] = mapped_column(String(40), default="")
    model_key_enc: Mapped[str] = mapped_column(Text, default="")
    model_key_last4: Mapped[str] = mapped_column(String(4), default="")
    # "other" provider only: the endpoint and the model, neither of them secret.
    model_base_url: Mapped[str] = mapped_column(String(500), default="")
    model_name: Mapped[str] = mapped_column(String(200), default="")
    locale: Mapped[str] = mapped_column(String(5), default="en")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    current_period_start: Mapped[datetime] = mapped_column(UTCDateTime)
    current_period_end: Mapped[datetime] = mapped_column(UTCDateTime)
    trial_ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    paddle_subscription_id: Mapped[str] = mapped_column(String(100), default="", index=True)
    paddle_customer_id: Mapped[str] = mapped_column(String(100), default="")
    trial_reminder_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    provider_ref: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default=MachineStatus.CREATING)
    status_detail: Mapped[str] = mapped_column(String(300), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    vcpu: Mapped[int] = mapped_column(Integer)
    memory_gb: Mapped[int] = mapped_column(Integer)
    disk_gb: Mapped[int] = mapped_column(Integer)
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    # The previous heartbeat: its whole second (server clock) and the tasks it named.
    last_beat_second: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_beat_running: Mapped[list] = mapped_column(JSON, default=list)
    last_active_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Project(Base):
    """A named folder on the person's machine. Tasks (sessions) work in it, so what one leaves the next finds."""

    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("user_id", "folder_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # What the person typed, and the folder it becomes on the machine (spaces become hyphens).
    name: Mapped[str] = mapped_column(String(60))
    folder: Mapped[str] = mapped_column(String(60))
    # The folder compared without regard to case: "Notes" and "notes" are the same folder, so the same project.
    folder_key: Mapped[str] = mapped_column(String(60))
    # Set when the project is a clone of one of the person's repositories.
    repo_url: Mapped[str] = mapped_column(String(500), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    # None for the tasks from before projects existed: each had a folder of its own.
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    goal: Mapped[str] = mapped_column(Text)
    repo_url: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.QUEUED, index=True)
    # Why it is paused or failed: hours_exhausted, provider_unavailable, invalid_key, ...
    status_reason: Mapped[str] = mapped_column(String(60), default="")
    outcome: Mapped[str] = mapped_column(String(20), default="")
    final_message: Mapped[str] = mapped_column(Text, default="")
    resume_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    # A task is a session: what the person wrote after the first goal waits here until the machine has it
    # (it may be asleep, or the plan may have no free slot yet). Cleared when the machine's Kvist says hello.
    pending_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    next_seq: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (UniqueConstraint("task_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (Index("ix_ledger_user_period", "user_id", "period_start"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    period_start: Mapped[datetime] = mapped_column(UTCDateTime)
    kind: Mapped[str] = mapped_column(String(20))
    # Signed: grants, purchases and refunds are positive, usage negative.
    seconds: Mapped[int] = mapped_column(BigInteger)
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id"), nullable=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    interval_start: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    interval_end: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reason: Mapped[str] = mapped_column(String(300), default="")
    refers_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Dispute(Base):
    __tablename__ = "disputes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")
    resolution: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    upload_token_hash: Mapped[str] = mapped_column(String(64), default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    error: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class FunnelEvent(Base):
    __tablename__ = "funnel_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    step: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    # A random id from a first-party cookie, so steps before sign-in join the ones after it.
    visitor_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PaddleEvent(Base):
    """Webhook ids already processed; Paddle retries, a retry must change nothing."""

    __tablename__ = "paddle_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(100), unique=True)
    event_type: Mapped[str] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Outbox(Base):
    """Emails waiting for a mail provider; the MVP only queues them."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


@event.listens_for(LedgerEntry, "before_update")
def _refuse_ledger_update(_mapper, _connection, _target) -> None:
    raise LedgerAppendOnlyError("ledger_entries is append-only: write a refund row instead")


@event.listens_for(LedgerEntry, "before_delete")
def _refuse_ledger_delete(_mapper, _connection, _target) -> None:
    raise LedgerAppendOnlyError("ledger_entries is append-only: write a refund row instead")


@event.listens_for(Session, "do_orm_execute")
def _refuse_ledger_bulk_writes(state: ORMExecuteState) -> None:
    if not (state.is_update or state.is_delete):
        return
    mapper = state.bind_mapper
    if mapper is not None and mapper.class_ is LedgerEntry:
        raise LedgerAppendOnlyError("ledger_entries is append-only: write a refund row instead")
