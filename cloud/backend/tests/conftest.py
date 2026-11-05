# -*- coding: utf-8 -*-
"""Shared fixtures. Every test gets its own SQLite file under tmp_path; nothing touches the network."""
from __future__ import annotations

import secrets
import shutil
from datetime import timedelta
from pathlib import Path

import httpx
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


def no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected outbound request to {request.url.host}")


class FakeProvider:
    """Machines that exist only in memory; records what the server asked for."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.states: dict[str, str] = {}
        self.tokens: dict[str, str] = {}

    async def create(self, spec) -> str:
        ref = f"m{spec.machine_id}"
        self.calls.append(("create", ref))
        self.states[ref] = "stopped"
        self.tokens[ref] = spec.token
        return ref

    async def wake(self, ref: str) -> None:
        self.calls.append(("wake", ref))
        self.states[ref] = "running"

    async def sleep(self, ref: str) -> None:
        self.calls.append(("sleep", ref))
        self.states[ref] = "stopped"

    async def delete(self, ref: str) -> None:
        self.calls.append(("delete", ref))
        self.states.pop(ref, None)

    async def status(self, ref: str) -> str:
        return self.states.get(ref, "missing")


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def app_factory(env, database_url, fake_provider):
    """Builds the app on this test's database; ``handler`` answers every outbound HTTP request."""
    from kvist_cloud.main import create_app

    def build(handler=no_network, provider=None, **overrides: str):
        settings = load_settings({**env, "DATABASE_URL": database_url, **overrides})
        return create_app(settings, http_transport=httpx.MockTransport(handler), provider=provider or fake_provider)

    return build


@pytest.fixture
def client_factory(app_factory):
    from fastapi.testclient import TestClient

    clients = []

    def build(handler=no_network, provider=None, **overrides: str) -> TestClient:
        # Entered as a context manager: one event loop for the whole test, so background tasks live on.
        client = TestClient(app_factory(handler, provider, **overrides), base_url="http://127.0.0.1:8740")
        client.__enter__()
        client.headers["X-Kvist-Client"] = "1"
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def settle(client) -> None:
    """Wait until the app's background tasks are done."""
    import asyncio

    async def wait() -> None:
        for _ in range(200):
            pending = list(client.app.state.background)
            if not pending:
                return
            await asyncio.wait(pending, timeout=0.05)

    client.portal.call(wait)


FAKE_KVIST = Path(__file__).resolve().parents[2] / "runner" / "tests" / "fake_kvist.py"


def eventually(check, timeout: float = 15.0, interval: float = 0.05):
    """Poll until ``check()`` returns something truthy; returns it."""
    import time

    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met in {timeout}s")
        time.sleep(interval)


class LiveStack:
    """A real server on a real port, the local provider, real runners and the fake Kvist."""

    def __init__(self, app, url: str, server, thread) -> None:
        self.app = app
        self.url = url
        self._server = server
        self._thread = thread
        self.http = httpx.Client(base_url=url, headers={"X-Kvist-Client": "1"}, timeout=30.0)

    def call(self, coroutine, timeout: float = 30.0):
        import asyncio

        return asyncio.run_coroutine_threadsafe(coroutine, self.app.state.loop).result(timeout)

    def db(self):
        return dbmod.make_session_factory(self.app.state.engine)()

    def stop(self) -> None:
        self.http.close()
        self._server.should_exit = True
        self._thread.join(30)


@pytest.fixture
def live_stack(env, database_url):
    import socket
    import sys
    import threading

    import uvicorn

    from kvist_cloud.main import create_app

    stacks: list[LiveStack] = []

    def start(kvist_command: list[str] | None = None, **overrides: str) -> LiveStack:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        import json as _json

        settings = load_settings({
            **env,
            "DATABASE_URL": database_url,
            "PUBLIC_URL": url,
            "AUTH_DEV_LOGIN": "true",
            "ALLOW_LOCAL_ENDPOINTS": "true",
            "RUNNER_COMMAND": _json.dumps([sys.executable, "-m", "kvist_runner"]),
            "KVIST_COMMAND": _json.dumps(kvist_command or [sys.executable, str(FAKE_KVIST)]),
            "HEARTBEAT_SECONDS": "0.2",
            "HEARTBEAT_MAX_GAP_SECONDS": "1",
            **overrides,
        })
        app = create_app(settings, http_transport=httpx.MockTransport(no_network))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        eventually(lambda: server.started, timeout=20)
        stack = LiveStack(app, url, server, thread)
        stacks.append(stack)
        return stack

    yield start
    for stack in stacks:
        stack.stop()


def dev_login(client) -> None:
    """Signs in through the dev path of the GitHub button."""
    response = client.get("/auth/github/login", follow_redirects=True)
    assert response.history, "the login did not redirect"


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
