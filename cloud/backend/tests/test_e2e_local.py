# -*- coding: utf-8 -*-
"""End to end on the local provider: sign-in, machine, key, task, events, charge, funnel.

Everything a browser does goes over HTTP and the task's WebSocket to a real server on a real
port; the machine is a real runner process; the model is a fake OpenAI-compatible server on this
computer. Two variants: the scripted fake Kvist (fast), and the real Kvist core.
"""
from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path

import pytest
from conftest import eventually
from sqlalchemy import select, text
from websockets.sync.client import connect

from kvist_cloud import ledger
from kvist_cloud.config import CLOUD_ROOT, REPO_ROOT
from kvist_cloud.models import FunnelEvent, Task, TaskStatus, User

KEY = "dev-key-e2e-0123456789"
FUNNEL = ["pricing_view", "github_login", "checkout_open", "card_added", "key_valid", "first_task_started",
          "first_task_done"]


@pytest.fixture
def fake_model():
    spec = importlib.util.spec_from_file_location("fake_model", CLOUD_ROOT / "scripts" / "fake_model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = module.Server(("127.0.0.1", 0), module.make_handler(0.05))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


@pytest.fixture
def dist(tmp_path):
    folder = tmp_path / "dist"
    folder.mkdir()
    for name in ("pricing.en", "pricing.ru", "app.en", "app.ru"):
        (folder / f"{name}.html").write_text("<!doctype html>")
    return folder


def stream_until_done(stack, task_id: int, timeout: float = 90.0) -> list[dict]:
    """What the task screen receives: the task, its history, then live events, until it ends."""
    cookies = "; ".join(f"{name}={value}" for name, value in stack.http.cookies.items())
    url = stack.url.replace("http://", "ws://") + f"/api/tasks/{task_id}/stream"
    received: list[dict] = []
    with connect(url, additional_headers={"Cookie": cookies, "Origin": stack.url}, open_timeout=10) as socket:
        while True:
            message = json.loads(socket.recv(timeout=timeout))
            received.append(message)
            if message["type"] == "task" and message["task"]["status"] in ("done", "failed", "cancelled", "paused"):
                return received


def walk_the_funnel(stack, fake_model: str, goal: str) -> int:
    http = stack.http
    assert http.get("/").status_code == 200
    login = http.get("/auth/github/login?next=/start", follow_redirects=True)
    assert login.url.path == "/start"
    eventually(lambda: [m for m in http.get("/api/machines").json() if m["connected"]], timeout=30)
    assert http.post("/api/billing/checkout-opened").json()["ok"]
    assert http.post("/api/billing/dev/skip-checkout").json()["status"] == "trialing"
    checked = http.post("/api/model-key", json={"provider": "other", "key": KEY, "base_url": fake_model,
                                                 "model": "fake-model"}).json()
    assert checked == {"ok": True, "code": "", "key": {"provider": "other", "last4": KEY[-4:],
                                                       "base_url": fake_model, "model": "fake-model", "set": True}}
    created = http.post("/api/tasks", json={"goal": goal, "repo_url": ""})
    assert created.status_code == 201
    return created.json()["id"]


def check_the_outcome(stack, task_id: int, received: list[dict], written: str) -> None:
    events = [m["event"] for m in received if m["type"] == "event"]
    kinds = [event["type"] for event in events]
    assert received[0]["type"] == "task"
    assert "hello" in kinds and "action_result" in kinds and "final" in kinds, kinds
    assert received[-1]["task"]["status"] == "done"

    changes = stack.http.get(f"/api/tasks/{task_id}/changes").json()
    assert [change["path"] for change in changes] == [written]
    assert changes[0]["patches"][0].startswith("--- /dev/null")

    with stack.db() as db:
        used = ledger.task_used_seconds(db, task_id)
        user = db.scalars(select(User)).one()
        balance = ledger.balance_seconds(db, user.id)
        steps = sorted({row.step for row in db.scalars(select(FunnelEvent))}, key=FUNNEL.index)
        stored = "\n".join(str(row) for row in db.execute(text("SELECT payload FROM task_events")))
        task = db.get(Task, task_id)
        assert task.status == TaskStatus.DONE
    assert used >= 1, "the running time was charged"
    assert balance == 5 * 3600 - used
    feed = stack.http.get("/api/ledger").json()["feed"]
    assert any(row["kind"] == "usage" and row["task_id"] == task_id and row["seconds"] == -used for row in feed)
    assert steps == FUNNEL
    assert KEY[:-4] not in stored, "the model key reached the stored events"
    admin = stack.http.get("/api/admin/funnel").json()["steps"]
    assert [row["people"] for row in admin] == [1] * 7


def test_end_to_end_with_the_scripted_kvist(live_stack, fake_model, dist):
    stack = live_stack(network=True, FRONTEND_DIST=str(dist), ADMIN_GITHUB_LOGINS="dev")
    task_id = walk_the_funnel(stack, fake_model, "slow 3")
    received = stream_until_done(stack, task_id)
    check_the_outcome(stack, task_id, received, "hello.txt")


@pytest.mark.real_core
@pytest.mark.skipif(not (REPO_ROOT / ".venv" / "bin" / "python").exists(), reason="the Kvist core's venv is missing")
def test_end_to_end_with_the_real_kvist_core(live_stack, fake_model, dist):
    stack = live_stack(
        kvist_command=[str(REPO_ROOT / ".venv" / "bin" / "python"), "-m", "kernel"],
        network=True, FRONTEND_DIST=str(dist), ADMIN_GITHUB_LOGINS="dev",
    )
    task_id = walk_the_funnel(stack, fake_model, "Write the cloud notes")
    received = stream_until_done(stack, task_id, timeout=120)
    check_the_outcome(stack, task_id, received, "CLOUD_NOTES.md")
    folder = Path(stack.app.state.settings.data_dir) / "machines" / "m1" / "work" / f"task-{task_id}"
    assert "Written by the fake model." in (folder / "CLOUD_NOTES.md").read_text()
