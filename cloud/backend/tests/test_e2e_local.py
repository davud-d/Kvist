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
    assert checked == {"ok": True, "code": "", "key": {"provider": "other", "label": "Other", "last4": KEY[-4:],
                                                       "base_url": fake_model, "model": "fake-model", "set": True,
                                                       "chatgpt_state": "", "chatgpt_email": ""}}
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


def test_the_agents_screen_reaches_only_its_owner_through_a_real_runner(live_stack, fake_model, dist):
    """The desktop skill's picture: machine, runner, server, the owner's page; no address, no token, no stranger."""
    import httpx
    from sqlalchemy import select as sql_select

    from kvist_cloud.auth import SESSION_COOKIE, _serializer
    from kvist_cloud.models import TaskEvent

    stack = live_stack(network=True, FRONTEND_DIST=str(dist), ADMIN_GITHUB_LOGINS="dev")
    task_id = walk_the_funnel(stack, fake_model, "screen 30")
    cookies = "; ".join(f"{name}={value}" for name, value in stack.http.cookies.items())
    url = stack.url.replace("http://", "ws://") + f"/api/tasks/{task_id}/stream"
    seen: list[str] = []
    with connect(url, additional_headers={"Cookie": cookies, "Origin": stack.url}, open_timeout=10) as socket:
        view = None
        while view is None:
            message = json.loads(socket.recv(timeout=30))
            seen.append(json.dumps(message))
            if message["type"] == "event" and message["event"]["type"] == "screen":
                view = message["event"]
    frame_url = view["frame_url"]
    assert frame_url == f"/api/tasks/{task_id}/screens/desktop/frame.jpg"
    assert view["url"] == view["stream_url"] == ""

    picture = eventually(lambda: (lambda r: r if r.status_code == 200 else None)(stack.http.get(frame_url)), timeout=20)
    assert picture.content.startswith(b"\xff\xd8\xff") and "no-store" in picture.headers["cache-control"]

    # A signed-out visitor, and another account, learn nothing.
    assert httpx.get(stack.url + frame_url, headers={"X-Kvist-Client": "1"}).status_code == 401
    with stack.db() as db:
        stranger = User(github_id=777, github_login="stranger")
        db.add(stranger)
        db.commit()
        session = {"u": stranger.id, "v": stranger.session_version}
        cookie = _serializer(stack.app.state.settings, "session").dumps(session)
    other = httpx.get(stack.url + frame_url, headers={"X-Kvist-Client": "1"}, cookies={SESSION_COOKIE: cookie})
    missing = httpx.get(stack.url + f"/api/tasks/{task_id + 99}/screens/desktop/frame.jpg",
                        headers={"X-Kvist-Client": "1"}, cookies={SESSION_COOKIE: cookie})
    assert other.status_code == 404 and other.json() == missing.json()

    assert stack.http.post(f"/api/tasks/{task_id}/cancel").status_code == 200
    with stack.db() as db:
        stored = "\n".join(json.dumps(row.payload) for row in db.scalars(sql_select(TaskEvent)))
    assert "fake-screen-token" not in "".join(seen) + stored


def test_the_conversation_goes_on_through_a_real_runner(live_stack, fake_model, dist):
    """A task is a session: the second message reaches a new run of Kvist that remembers the first."""
    stack = live_stack(network=True, FRONTEND_DIST=str(dist), ADMIN_GITHUB_LOGINS="dev")
    task_id = walk_the_funnel(stack, fake_model, "say hello there")
    stream_until_done(stack, task_id)

    sent = stack.http.post(f"/api/tasks/{task_id}/messages", json={"text": "say and once more"})
    assert sent.status_code == 200, sent.text

    def second_answer():
        events = stack.http.get(f"/api/tasks/{task_id}/events").json()["events"]
        done = stack.http.get(f"/api/tasks/{task_id}").json()["status"] == "done"
        replies = [row["event"]["markdown"] for row in events
                   if row["event"]["type"] == "message" and row["event"].get("source") == "reply"]
        return replies if done and len(replies) == 2 else None

    replies = eventually(second_answer, timeout=40)
    assert replies[0].startswith("I remember 0 earlier messages")
    assert replies[1] == "I remember 1 earlier messages: say hello there"
    users = [row["event"]["text"] for row in stack.http.get(f"/api/tasks/{task_id}/events").json()["events"]
             if row["event"]["type"] == "user"]
    assert users == ["say hello there", "say and once more"]


@pytest.mark.real_core
@pytest.mark.skipif(not (REPO_ROOT / ".venv" / "bin" / "python").exists(), reason="the Kvist core's venv is missing")
def test_the_real_core_takes_the_next_message_in_the_same_conversation(live_stack, fake_model, dist):
    """Both ways a message can arrive: the first run still finishing (the same process takes it), and the first run
    gone (a new process switches back into the session, then takes it)."""
    import time

    stack = live_stack(
        kvist_command=[str(REPO_ROOT / ".venv" / "bin" / "python"), "-m", "kernel"],
        network=True, FRONTEND_DIST=str(dist), ADMIN_GITHUB_LOGINS="dev",
    )
    task_id = walk_the_funnel(stack, fake_model, "Write the cloud notes")
    stream_until_done(stack, task_id, timeout=120)

    def rows_with(finals: int):
        def check():
            rows = stack.http.get(f"/api/tasks/{task_id}/events").json()["events"]
            done = stack.http.get(f"/api/tasks/{task_id}").json()["status"] == "done"
            return rows if done and len([r for r in rows if r["event"]["type"] == "final"]) == finals else None
        return eventually(check, timeout=120)

    # Straight after the answer: the first process is still finishing its turn.
    assert stack.http.post(f"/api/tasks/{task_id}/messages", json={"text": "once more"}).status_code == 200
    rows_with(2)
    # Later: that process has gone, so a new one has to find its way back into the conversation.
    time.sleep(8)
    assert stack.http.post(f"/api/tasks/{task_id}/messages", json={"text": "and again"}).status_code == 200
    rows = rows_with(3)
    results = [row["event"] for row in rows if row["event"]["type"] == "command_result"]
    assert results and results[-1]["name"] == "session" and results[-1]["ok"] is True, results
    users = [row["event"]["text"] for row in rows if row["event"]["type"] == "user"]
    assert users == ["Write the cloud notes", "once more", "and again"]


@pytest.mark.real_core
@pytest.mark.skipif(not (REPO_ROOT / ".venv" / "bin" / "python").exists(), reason="the Kvist core's venv is missing")
def test_the_real_core_keeps_a_session_per_task_in_a_shared_project_folder(live_stack, fake_model, dist):
    stack = live_stack(
        kvist_command=[str(REPO_ROOT / ".venv" / "bin" / "python"), "-m", "kernel"],
        network=True, FRONTEND_DIST=str(dist), ADMIN_GITHUB_LOGINS="dev",
    )
    first = walk_the_funnel(stack, fake_model, "Write the cloud notes")  # an older task: a folder of its own
    stream_until_done(stack, first, timeout=120)
    project = stack.http.post("/api/projects", json={"name": "Notes site"}).json()
    folder = Path(stack.app.state.settings.data_dir) / "machines" / "m1" / "work" / "Notes-site"
    eventually(folder.is_dir, timeout=20)  # made at once: the machine is awake

    ids = []
    for goal in ("Write the cloud notes", "Write the cloud notes again"):
        created = stack.http.post("/api/tasks", json={"goal": goal, "project_id": project["id"]})
        assert created.status_code == 201, created.text
        ids.append(created.json()["id"])
        stream_until_done(stack, ids[-1], timeout=120)

    assert "Written by the fake model." in (folder / "CLOUD_NOTES.md").read_text()
    for task_id in ids:
        rows = stack.http.get(f"/api/tasks/{task_id}/events").json()["events"]
        sessions = [row["event"]["session"] for row in rows if row["event"]["type"] == "session"]
        results = [row["event"] for row in rows if row["event"]["type"] == "command_result"]
        assert sessions == [f"task-{task_id}"], sessions
        assert results and results[0]["name"] == "new" and results[0]["ok"] is True, results
