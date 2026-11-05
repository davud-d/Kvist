# -*- coding: utf-8 -*-
"""Tasks API and the browser's event stream, with a hand-driven runner on the gateway."""
from __future__ import annotations

import httpx
import pytest
from conftest import dev_login, eventually, make_subscription, settle
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from kvist_cloud.db import make_session_factory, session_scope
from kvist_cloud.models import FunnelEvent, Task, TaskStatus, User

KEY = "sk-tasks-canary-0123456789abcdef"
ORIGIN = {"origin": "http://127.0.0.1:8740"}


def deepseek_ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"is_available": True})


def signed_in(client_factory, engine, subscription: bool = True, key: bool = True):
    client = client_factory(deepseek_ok, AUTH_DEV_LOGIN="true")
    dev_login(client)
    settle(client)
    if subscription:
        with session_scope(make_session_factory(engine)) as db:
            make_subscription(db, db.scalars(select(User)).one(), "trial")
    if key:
        assert client.post("/api/model-key", json={"provider": "deepseek", "key": KEY}).json()["ok"]
    return client


def browser_headers(client) -> dict[str, str]:
    """What a browser sends with a WebSocket; the test client does not add cookies by itself."""
    cookie = "; ".join(f"{name}={value}" for name, value in client.cookies.items())
    return {**ORIGIN, "cookie": cookie}


def runner(client, fake_provider):
    return client.websocket_connect("/gateway/ws", headers={"Authorization": f"Bearer {fake_provider.tokens['m1']}"})


def status(engine, task_id: int) -> str:
    with session_scope(make_session_factory(engine)) as db:
        return db.get(Task, task_id).status


def relay(ws, task_id: int, event: dict) -> None:
    ws.send_json({"type": "event", "task_id": task_id, "event": event})


WRITE = {"type": "action_result", "step": 1, "action_type": "write_file", "ok": True, "summary": "Wrote 1 line",
         "path": "app.py", "lines_added": 1, "lines_removed": 0, "call_id": "c1",
         "patch": "--- /dev/null\n+++ b/app.py\n@@ -0,0 +1 @@\n+print(1)\n"}


def test_a_task_needs_a_subscription_and_a_key(client_factory, engine):
    client = signed_in(client_factory, engine, subscription=False, key=False)
    assert client.post("/api/tasks", json={"goal": "x"}).json()["error"]["code"] == "no_subscription"
    with session_scope(make_session_factory(engine)) as db:
        make_subscription(db, db.scalars(select(User)).one(), "trial")
    assert client.post("/api/tasks", json={"goal": "x"}).json()["error"]["code"] == "no_key"


def test_used_up_hours_refuse_a_new_task(client_factory, engine):
    client = signed_in(client_factory, engine, subscription=False)
    with session_scope(make_session_factory(engine)) as db:
        make_subscription(db, db.scalars(select(User)).one(), "trial", hours=0)
    response = client.post("/api/tasks", json={"goal": "x"})
    assert (response.status_code, response.json()["error"]["code"]) == (402, "no_hours")


@pytest.mark.parametrize("url", ["ftp://example.com/r", "git@github.com:a/b.git", "https://user:pw@github.com/a/b"])
def test_repository_link_must_be_https(client_factory, engine, url):
    client = signed_in(client_factory, engine)
    assert client.post("/api/tasks", json={"goal": "x", "repo_url": url}).json()["error"]["code"] == "bad_repo"


def test_new_task_wakes_a_sleeping_machine(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    fake_provider.calls.clear()
    created = client.post("/api/tasks", json={"goal": "explain the project"})
    assert created.status_code == 201
    assert created.json()["status"] == TaskStatus.STARTING
    settle(client)
    assert ("wake", "m1") in fake_provider.calls
    with session_scope(make_session_factory(engine)) as db:
        steps = [row.step for row in db.scalars(select(FunnelEvent))]
    assert steps == ["github_login", "key_valid", "first_task_started"]


def test_task_life_through_the_runner(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    with runner(client, fake_provider) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        task_id = client.post("/api/tasks", json={"goal": "write app.py", "repo_url": ""}).json()["id"]
        start = ws.receive_json()
        assert (start["type"], start["task_id"], start["goal"]) == ("start_task", task_id, "write app.py")
        relay(ws, task_id, {"type": "hello", "protocol_version": 1})
        relay(ws, task_id, {"type": "stream_chunk", "stream_id": "s", "text": "thinking"})
        relay(ws, task_id, WRITE)
        relay(ws, task_id, {"type": "final", "message": "Wrote app.py", "user_messages": [], "outcome": "done"})
        eventually(lambda: status(engine, task_id) == TaskStatus.DONE)
    task = client.get(f"/api/tasks/{task_id}").json()
    assert (task["final_message"], task["outcome"]) == ("Wrote app.py", "done")
    events = client.get(f"/api/tasks/{task_id}/events").json()["events"]
    # The live snapshot (stream_chunk) is not kept; the significant events are.
    assert [item["event"]["type"] for item in events] == ["hello", "action_result", "final"]
    assert client.get(f"/api/tasks/{task_id}/events?after=2").json()["events"][0]["seq"] == 3
    changes = client.get(f"/api/tasks/{task_id}/changes").json()
    assert changes == [{"path": "app.py", "lines_added": 1, "lines_removed": 0, "patches": [WRITE["patch"]]}]
    assert [row["id"] for row in client.get("/api/tasks").json()] == [task_id]


def test_browser_stream_sends_history_then_live_events(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    with runner(client, fake_provider) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        task_id = client.post("/api/tasks", json={"goal": "go"}).json()["id"]
        ws.receive_json()
        relay(ws, task_id, {"type": "hello", "protocol_version": 1})
        eventually(lambda: status(engine, task_id) == TaskStatus.RUNNING)
        with client.websocket_connect(f"/api/tasks/{task_id}/stream", headers=browser_headers(client)) as browser:
            first = browser.receive_json()
            assert first["type"] == "task" and first["task"]["status"] == "running"
            assert "balance_seconds" in first["task"]
            history = browser.receive_json()
            assert history == {"type": "event", "seq": 1, "event": {"type": "hello", "protocol_version": 1}}
            assert browser.receive_json() == {"type": "history_end"}
            relay(ws, task_id, {"type": "action_output", "step": 1, "output": "building...", "call_id": "c"})
            live = browser.receive_json()
            assert live == {"type": "event", "seq": None,
                            "event": {"type": "action_output", "step": 1, "output": "building...", "call_id": "c"}}
            relay(ws, task_id, {"type": "task_state", "state": "awaiting_input", "reason": ""})
            assert browser.receive_json()["event"]["type"] == "task_state"
            change = browser.receive_json()
            assert change["type"] == "task" and change["task"]["status"] == TaskStatus.WAITING_FOR_USER


def test_stream_refuses_other_origins_and_other_people(client_factory, engine):
    client = signed_in(client_factory, engine)
    task_id = client.post("/api/tasks", json={"goal": "go"}).json()["id"]
    with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect(
        f"/api/tasks/{task_id}/stream", headers={**browser_headers(client), "origin": "https://evil.example"}
    ) as browser:
        browser.receive_json()
    assert refused.value.code == 4403
    client.post("/auth/logout")
    with pytest.raises(WebSocketDisconnect) as anonymous, client.websocket_connect(
        f"/api/tasks/{task_id}/stream", headers=ORIGIN
    ) as browser:
        browser.receive_json()
    assert anonymous.value.code == 4401
    assert client.get(f"/api/tasks/{task_id}").status_code == 401


def test_someone_elses_task_is_not_found(client_factory, engine):
    client = signed_in(client_factory, engine)
    with session_scope(make_session_factory(engine)) as db:
        stranger = User(github_id=99, github_login="stranger")
        db.add(stranger)
        db.flush()
        machine_id = db.scalars(select(Task.machine_id)).first()
        task = Task(user_id=stranger.id, machine_id=machine_id or 1, goal="secret")
        db.add(task)
        db.flush()
        foreign = task.id
    assert client.get(f"/api/tasks/{foreign}").status_code == 404
    assert client.get(f"/api/tasks/{foreign}/events").status_code == 404
    assert client.post(f"/api/tasks/{foreign}/cancel").status_code == 404


def test_cancel_before_the_runner_has_it(client_factory, engine):
    client = signed_in(client_factory, engine)
    task_id = client.post("/api/tasks", json={"goal": "go"}).json()["id"]
    assert client.post(f"/api/tasks/{task_id}/cancel").json()["status"] == TaskStatus.CANCELLED


def test_cancel_and_answer_go_to_the_runner(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    with runner(client, fake_provider) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        task_id = client.post("/api/tasks", json={"goal": "ask me"}).json()["id"]
        ws.receive_json()
        relay(ws, task_id, {"type": "hello", "protocol_version": 1})
        early = client.post(f"/api/tasks/{task_id}/input", json={"request_id": "r1", "approved": True})
        assert early.status_code == 409
        relay(ws, task_id, {"type": "task_state", "state": "awaiting_input", "reason": ""})
        eventually(lambda: status(engine, task_id) == TaskStatus.WAITING_FOR_USER)
        assert client.post(f"/api/tasks/{task_id}/input", json={"request_id": "r1", "approved": True}).json()["ok"]
        assert ws.receive_json() == {"type": "input_response", "task_id": task_id,
                                     "response": {"request_id": "r1", "approved": True}}
        client.post(f"/api/tasks/{task_id}/cancel")
        assert ws.receive_json() == {"type": "control", "task_id": task_id, "action": "cancel"}
        relay(ws, task_id, {"type": "task_state", "state": "cancelled", "reason": ""})
        eventually(lambda: status(engine, task_id) == TaskStatus.CANCELLED)


def test_pause_and_resume(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    with runner(client, fake_provider) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        task_id = client.post("/api/tasks", json={"goal": "long job"}).json()["id"]
        ws.receive_json()
        relay(ws, task_id, {"type": "hello", "protocol_version": 1})
        eventually(lambda: status(engine, task_id) == TaskStatus.RUNNING)
        client.post(f"/api/tasks/{task_id}/pause")
        assert ws.receive_json() == {"type": "control", "task_id": task_id, "action": "pause"}
        relay(ws, task_id, {"type": "final", "message": "Paused.", "user_messages": [], "outcome": "incomplete",
                            "paused": True})
        eventually(lambda: status(engine, task_id) == TaskStatus.PAUSED)
        ws.send_json({"type": "task_exit", "task_id": task_id, "exit_code": 0, "reason": "finished"})
        resumed = client.post(f"/api/tasks/{task_id}/resume").json()
        assert resumed["status"] == TaskStatus.STARTING
        again = ws.receive_json()
        assert (again["type"], again["task_id"], again["resume"]) == ("start_task", task_id, True)
