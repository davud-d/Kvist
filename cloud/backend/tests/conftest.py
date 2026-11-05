# -*- coding: utf-8 -*-
"""Shared fixtures. Every test gets its own SQLite file under tmp_path; nothing touches the network."""
from __future__ import annotations

import secrets
import shutil
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from kvist_cloud import db as dbmod
from kvist_cloud import ledger, plans
from kvist_cloud.config import load_settings
from kvist_cloud.models import Machine, Subscription, SubscriptionStatus, Task, TaskStatus, User

MASTER_KEY = Fernet.generate_key().decode()


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("template") / "template.db"
    dbmod.upgrade(f"sqlite:///{path}")
    return path


@pytest.fixture
def env(tmp_path) -> dict[str, str]:
    return {
        "KVIST_CLOUD_DATA_DIR": str(tmp_path / "data"),
        "KVIST_CLOUD_SESSION_SECRET": secrets.token_urlsafe(40),
        "KVIST_CLOUD_MASTER_KEY": MASTER_KEY,
        "PUBLIC_URL": "http://127.0.0.1:8740",
        "FRONTEND_DIST": str(tmp_path / "dist"),
    }


@pytest.fixture
def settings(env):
    return load_settings(env)


@pytest.fixture
def database_url(tmp_path, migrated_template) -> str:
    path = tmp_path / "cloud.db"
    shutil.copy(migrated_template, path)
    return f"sqlite:///{path}"


@pytest.fixture
def engine(database_url):
    engine = dbmod.make_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine):
    factory = dbmod.make_session_factory(engine)
    session = factory()
    yield session
    session.rollback()
    session.close()


def make_user(db, login: str = "octo", github_id: int = 1) -> User:
    user = User(github_id=github_id, github_login=login)
    db.add(user)
    db.flush()
    return user


def make_subscription(db, user: User, plan_id: str = "trial", hours: float | None = None) -> Subscription:
    plan = plans.get_plan(plan_id)
    now = dbmod.utcnow()
    days = plan.trial_days or 30
    subscription = Subscription(
        user_id=user.id,
        plan=plan.id,
        status=SubscriptionStatus.TRIALING if plan.trial_days else SubscriptionStatus.ACTIVE,
        current_period_start=now,
        current_period_end=now + timedelta(days=days),
        trial_ends_at=now + timedelta(days=days) if plan.trial_days else None,
    )
    db.add(subscription)
    db.flush()
    seconds = plan.seconds if hours is None else int(hours * 3600)
    ledger.grant_period(db, subscription, seconds, f"{plan.id} plan hours")
    return subscription


def make_machine(db, user: User) -> Machine:
    machine = Machine(
        user_id=user.id, provider="local", token_hash=secrets.token_hex(32), vcpu=2, memory_gb=4, disk_gb=10
    )
    db.add(machine)
    db.flush()
    return machine


def make_task(db, user: User, machine: Machine, status: str = TaskStatus.RUNNING, goal: str = "do it") -> Task:
    task = Task(user_id=user.id, machine_id=machine.id, goal=goal, status=status)
    db.add(task)
    db.flush()
    return task
