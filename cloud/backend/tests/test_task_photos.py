# -*- coding: utf-8 -*-
"""Photos through the tasks API: rewritten before anything stores or forwards them, refused when not photos."""
from __future__ import annotations

import base64
import json

from conftest import eventually
from sqlalchemy import select
from test_images import JPEG, envelope, url, with_segment
from test_tasks_api import relay, runner, signed_in, status

from kvist_cloud.db import make_session_factory, session_scope
from kvist_cloud.models import Task, TaskEvent, TaskStatus

DIRTY = with_segment(JPEG, 0xE1, b"Exif\x00\x00GPS 41.0082 N 28.9784 E") + b"PK\x03\x04 a second file behind it"


def pixels(text: str) -> bytes:
    sent = json.loads(text)["images"][0]["data_url"]
    return base64.b64decode(sent.partition(",")[2])


def test_the_first_message_with_a_photo_reaches_the_machine_clean_and_the_history_keeps_no_pixels(
        client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    with runner(client, fake_provider) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        goal = envelope([{"name": "../../holiday.jpg", "data_url": url(DIRTY)}])
        created = client.post("/api/tasks", json={"goal": goal})
        assert created.status_code == 201, created.text
        task_id = created.json()["id"]
        start = ws.receive_json()
        clean = pixels(start["goal"])
        assert b"GPS" not in clean and b"PK\x03\x04" not in clean and clean.startswith(b"\xff\xd8")
        assert json.loads(start["goal"])["images"][0]["name"] == "holiday.jpg"
        # The list and the task name are the words, never megabytes.
        assert created.json()["goal"] == "what is this" and len(client.get("/api/tasks").text) < 2000
        relay(ws, task_id, {"type": "hello", "protocol_version": 1})
        eventually(lambda: status(engine, task_id) == TaskStatus.RUNNING)
    with session_scope(make_session_factory(engine)) as db:
        task = db.get(Task, task_id)
        assert task.start_envelope == "" and task.pending_text == ""
        stored = db.scalars(select(TaskEvent).where(TaskEvent.type == "user")).one().payload["text"]
    assert json.loads(stored)["images"] == [{"name": "holiday.jpg", "data_url": ""}] and len(stored) < 400


def test_a_photo_in_a_later_message_goes_to_the_running_agent_clean(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    with runner(client, fake_provider) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        task_id = client.post("/api/tasks", json={"goal": "look at my screen"}).json()["id"]
        ws.receive_json()
        relay(ws, task_id, {"type": "hello", "protocol_version": 1})
        eventually(lambda: status(engine, task_id) == TaskStatus.RUNNING)
        sent = client.post(f"/api/tasks/{task_id}/messages",
                           json={"text": envelope([{"name": "a.jpg", "data_url": url(DIRTY)}], text="and this")})
        assert sent.status_code == 200, sent.text
        message = ws.receive_json()
        assert message["type"] == "message" and b"GPS" not in pixels(message["text"])


def test_a_file_that_is_not_a_photo_is_refused_before_a_machine_is_woken_for_it(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    calls = list(fake_provider.calls)  # signing in already prepared the person's machine
    for bad in (b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>", b"\x7fELF" + bytes(100),
                b"PK\x03\x04" + bytes(100), JPEG[:100]):
        goal = envelope([{"name": "a.png", "data_url": url(bad, "image/png")}])
        response = client.post("/api/tasks", json={"goal": goal})
        assert response.status_code == 400 and response.json()["error"]["code"] == "bad_message"
    with session_scope(make_session_factory(engine)) as db:
        assert db.scalars(select(Task)).all() == []
    assert fake_provider.calls == calls


def test_the_envelope_in_another_spelling_is_not_a_way_around_the_check(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    spelled = json.dumps({"type": "kvist-image-goal", "text": "x", "images": [{"name": "a", "data_url": url(DIRTY)}]})
    # The spaces the core accepts and a test of the exact prefix would miss:
    assert spelled.startswith('{"type": "kvist-image-goal"')
    with runner(client, fake_provider) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        assert client.post("/api/tasks", json={"goal": " \n" + spelled}).status_code == 201
        assert b"GPS" not in pixels(ws.receive_json()["goal"])
    bad = json.dumps({"type": "kvist-image-goal", "text": "x", "images": [{"name": "a", "data_url": url(b"<svg>")}]})
    assert client.post("/api/tasks", json={"goal": bad}).status_code == 400


def test_plain_words_keep_their_old_limit(client_factory, engine):
    client = signed_in(client_factory, engine)
    assert client.post("/api/tasks", json={"goal": "x" * 20_001}).status_code == 400
    assert client.post("/api/tasks", json={"goal": "x" * 20_000}).status_code == 201
