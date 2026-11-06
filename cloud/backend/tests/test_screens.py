# -*- coding: utf-8 -*-
"""The agent's screens are private: the owner sees them, nobody else does, nothing is stored."""
from __future__ import annotations

import base64
import dataclasses
import json

import pytest
from conftest import eventually
from sqlalchemy import select
from test_tasks_api import browser_headers, relay, runner, signed_in

from kvist_cloud import screens
from kvist_cloud.auth import SESSION_COOKIE, _serializer
from kvist_cloud.db import make_session_factory, session_scope
from kvist_cloud.models import Task, TaskEvent, User

TOKEN = "SCREEN-TOKEN-0123456789abcdef"
JPEG = b"\xff\xd8\xff\xe0" + b"picture of the owner's desktop" * 20
RAW_SCREEN = {
    "type": "screen",
    "screen_id": "desktop",
    "skill": "desktop",
    "title": "Desktop",
    "state": "running",
    "url": f"http://127.0.0.1:41999/?t={TOKEN}",
    "stream_url": f"http://127.0.0.1:41999/stream.mjpeg?t={TOKEN}",
    "frame_url": f"http://127.0.0.1:41999/frame.jpg?t={TOKEN}",
}


def frame_message(task_id: int, jpeg: bytes = JPEG, screen_id: str = "desktop") -> dict:
    return {"type": "screen_frame", "task_id": task_id, "screen_id": screen_id, "jpeg": base64.b64encode(jpeg).decode()}


def sign_in_as(client, engine, login: str, github_id: int, admin: bool = False) -> int:
    """Swaps the client's session cookie for another account's; returns that account's id."""
    with session_scope(make_session_factory(engine)) as db:
        user = User(github_id=github_id, github_login=login)
        db.add(user)
        db.flush()
        user_id, version = user.id, user.session_version
    value = _serializer(client.app.state.settings, "session").dumps({"u": user_id, "v": version})
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, value)
    return user_id


def test_the_owner_sees_the_screen_and_the_machines_address_never_arrives(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    task_id = client.post("/api/tasks", json={"goal": "go"}).json()["id"]
    with runner(client, fake_provider) as machine, client.websocket_connect(
        f"/api/tasks/{task_id}/stream", headers=browser_headers(client)
    ) as browser:
        while browser.receive_json()["type"] != "history_end":
            pass
        relay(machine, task_id, RAW_SCREEN)
        seen = browser.receive_json()
        view = seen["event"]
        assert view["frame_url"] == f"/api/tasks/{task_id}/screens/desktop/frame.jpg"
        assert view["url"] == view["stream_url"] == ""
        assert TOKEN not in json.dumps(seen)

        machine.send_json(frame_message(task_id))
        frame_url = view["frame_url"]
        picture = eventually(lambda: (lambda r: r if r.status_code == 200 else None)(client.get(frame_url)))
        assert picture.content == JPEG
        assert picture.headers["content-type"] == "image/jpeg"
        assert "no-store" in picture.headers["cache-control"] and "private" in picture.headers["cache-control"]
        # Watching renews a lease on the machine: it pulls pictures only while someone looks.
        watch = next(m for m in iter(machine.receive_json, None) if m["type"] == "screen_watch")
        assert (watch["task_id"], watch["screen_id"]) == (task_id, "desktop")

    with session_scope(make_session_factory(engine)) as db:
        stored = json.dumps([row.payload for row in db.scalars(select(TaskEvent))])
    assert TOKEN not in stored and '"screen"' not in stored


@pytest.mark.parametrize("who", ["stranger", "admin", "signed-out"])
def test_nobody_but_the_owner_gets_a_picture(client_factory, engine, fake_provider, who):
    client = signed_in(client_factory, engine)
    task_id = client.post("/api/tasks", json={"goal": "go"}).json()["id"]
    url = f"/api/tasks/{task_id}/screens/desktop/frame.jpg"
    with runner(client, fake_provider) as machine:
        relay(machine, task_id, RAW_SCREEN)
        machine.send_json(frame_message(task_id))
        eventually(lambda: client.get(url).status_code == 200)
        eventually(lambda: client.get(f"/api/tasks/{task_id}/screens").json())

        missing = client.get(f"/api/tasks/{task_id + 1000}/screens/desktop/frame.jpg")
        if who == "signed-out":
            client.post("/auth/logout")
            expected = 401
        else:
            sign_in_as(client, engine, who, 50 if who == "stranger" else 51)
            if who == "admin":
                settings = client.app.state.settings
                client.app.state.settings = dataclasses.replace(settings, admin_logins=frozenset({"admin"}))
            expected = 404
        answer = client.get(url)
        listing = client.get(f"/api/tasks/{task_id}/screens")
        assert answer.status_code == listing.status_code == expected
        assert answer.content != JPEG
        # The refusal for the real task is the refusal for a task that does not exist.
        if who != "signed-out":
            assert answer.json() == missing.json()
        assert TOKEN not in answer.text


def test_a_stranger_cannot_watch_the_stream_of_someone_elses_screens(client_factory, engine, fake_provider):
    from starlette.websockets import WebSocketDisconnect

    client = signed_in(client_factory, engine)
    task_id = client.post("/api/tasks", json={"goal": "go"}).json()["id"]
    with runner(client, fake_provider) as machine:
        relay(machine, task_id, RAW_SCREEN)
        eventually(lambda: client.get(f"/api/tasks/{task_id}/screens").json())
        sign_in_as(client, engine, "stranger", 60)
        with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect(
            f"/api/tasks/{task_id}/stream", headers=browser_headers(client)
        ) as browser:
            browser.receive_json()
        assert refused.value.code == 4401


def test_a_picture_needs_the_clients_own_header(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    task_id = client.post("/api/tasks", json={"goal": "go"}).json()["id"]
    url = f"/api/tasks/{task_id}/screens/desktop/frame.jpg"
    # A page on another site can embed an <img>; it cannot add this header.
    assert client.get(url, headers={"X-Kvist-Client": ""}).status_code == 403


def test_pictures_from_the_wrong_machine_or_of_the_wrong_kind_are_dropped(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    task_id = client.post("/api/tasks", json={"goal": "go"}).json()["id"]
    store = client.app.state.screens
    with runner(client, fake_provider) as machine:
        # No screen announced yet: nothing to attach a picture to.
        machine.send_json(frame_message(task_id))
        relay(machine, task_id, RAW_SCREEN)
        eventually(lambda: store.knows(task_id, "desktop"))
        machine.send_json(frame_message(task_id, b"<html>not a picture</html>"))
        machine.send_json(frame_message(task_id, JPEG * 100_000))
        machine.send_json({**frame_message(task_id), "jpeg": "!!! not base64 !!!"})
        machine.send_json(frame_message(task_id + 500))
        machine.send_json(frame_message(task_id, screen_id="../../etc/passwd"))
        machine.send_json(frame_message(task_id, JPEG))
        eventually(lambda: store.frame(task_id, "desktop") == JPEG)
    assert store.frame(task_id + 500, "desktop") is None


def test_a_closed_screen_and_a_finished_task_forget_their_pictures(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    task_id = client.post("/api/tasks", json={"goal": "go"}).json()["id"]
    store = client.app.state.screens
    with runner(client, fake_provider) as machine:
        relay(machine, task_id, RAW_SCREEN)
        machine.send_json(frame_message(task_id))
        eventually(lambda: store.frame(task_id, "desktop"))
        relay(machine, task_id, {**RAW_SCREEN, "state": "closed", "url": "", "stream_url": "", "frame_url": ""})
        eventually(lambda: store.frame(task_id, "desktop") is None and not store.views(task_id))

        relay(machine, task_id, RAW_SCREEN)
        machine.send_json(frame_message(task_id))
        eventually(lambda: store.frame(task_id, "desktop"))
        relay(machine, task_id, {"type": "final", "message": "done", "user_messages": [], "outcome": "done"})
        eventually(lambda: client.app.state.screens.frame(task_id, "desktop") is None)
    with session_scope(make_session_factory(engine)) as db:
        assert db.get(Task, task_id).status == "done"


def test_stale_pictures_are_not_served():
    now = [100.0]
    store = screens.ScreenStore(clock=lambda: now[0])
    store.apply(1, RAW_SCREEN)
    assert store.put_frame(1, "desktop", JPEG)
    assert store.frame(1, "desktop") == JPEG
    now[0] += screens.FRESH_SECONDS + 1
    assert store.frame(1, "desktop") is None


def test_the_store_is_bounded():
    store = screens.ScreenStore()
    for task_id in range(screens.MAX_FRAMES + 20):
        store.apply(task_id, RAW_SCREEN)
        store.put_frame(task_id, "desktop", JPEG)
    assert len(store._frames) == screens.MAX_FRAMES
    assert store.frame(0, "desktop") is None and store.frame(screens.MAX_FRAMES + 19, "desktop") == JPEG
