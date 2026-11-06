# -*- coding: utf-8 -*-
"""Projects: named folders on the person's machine; names never repeat; tasks work inside them."""
from __future__ import annotations

import pytest
from conftest import eventually
from test_screens import sign_in_as
from test_tasks_api import relay, runner, signed_in

from kvist_cloud import projects


def receive(ws, kind: str) -> dict:
    """The next message of that kind; a machine that connects is also told its project folders."""
    return next(message for message in iter(ws.receive_json, None) if message["type"] == kind)


def test_a_project_is_a_named_folder(client_factory, engine):
    client = signed_in(client_factory, engine)
    made = client.post("/api/projects", json={"name": "  My app  "})
    assert made.status_code == 201
    assert made.json() | {"id": 0, "created_at": ""} == {
        "id": 0, "name": "My app", "folder": "My-app", "repo_url": "", "tasks": 0, "created_at": ""}
    assert [item["name"] for item in client.get("/api/projects").json()] == ["My app"]


@pytest.mark.parametrize("name", ["My app", "my app", "MY APP", "my-app", "My  app", " my-app "])
def test_names_do_not_repeat_whatever_their_case_or_spacing(client_factory, engine, name):
    client = signed_in(client_factory, engine)
    assert client.post("/api/projects", json={"name": "My app"}).status_code == 201
    again = client.post("/api/projects", json={"name": name})
    assert again.status_code == 409 and again.json()["error"]["code"] == "name_taken"
    assert len(client.get("/api/projects").json()) == 1


def test_two_people_may_use_the_same_name(client_factory, engine):
    client = signed_in(client_factory, engine)
    assert client.post("/api/projects", json={"name": "site"}).status_code == 201
    sign_in_as(client, engine, "other", 71)
    assert client.post("/api/projects", json={"name": "site"}).status_code == 201
    assert [item["name"] for item in client.get("/api/projects").json()] == ["site"]


BAD_NAMES = ["", "   ", "../etc", "a/b", "a\\b", ".hidden", "-x", "x.", "x" * 61, "task-7", "Task-12", "a\x00b"]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_a_name_that_could_not_be_a_folder_is_refused(client_factory, engine, name):
    client = signed_in(client_factory, engine)
    answer = client.post("/api/projects", json={"name": name})
    assert answer.status_code in (400, 422)
    assert client.get("/api/projects").json() == []


def test_names_in_any_language_are_fine():
    for name in ("Сайт", "проект-1", "My_app.v2", "日本語", "a"):
        assert projects.name_problem(name) == ""


def test_a_project_may_be_a_clone_of_a_repository(client_factory, engine):
    client = signed_in(client_factory, engine)
    repo = "https://github.com/o/claude-code.git"
    made = client.post("/api/projects", json={"name": "claude-code", "repo_url": repo})
    assert made.status_code == 201 and made.json()["repo_url"].endswith("claude-code.git")
    refused = client.post("/api/projects", json={"name": "bad", "repo_url": "ssh://x"})
    assert refused.json()["error"]["code"] == "bad_repo"


def test_tasks_of_a_project_start_in_its_folder_with_a_session_of_their_own(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    project = client.post("/api/projects", json={"name": "site"}).json()
    with runner(client, fake_provider) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        created = client.post("/api/tasks", json={"goal": "first", "project_id": project["id"]}).json()
        assert created["project_id"] == project["id"]
        start = receive(ws, "start_task")
        assert (start["folder"], start["session"], start["follow_up"]) == ("site", f"task-{created['id']}", False)
        relay(ws, created["id"], {"type": "hello", "protocol_version": 1})
        relay(ws, created["id"], {"type": "final", "message": "ok", "user_messages": [], "outcome": "done"})
        eventually(lambda: client.get(f"/api/tasks/{created['id']}").json()["status"] == "done")

        # A message later goes back into the same session.
        client.post(f"/api/tasks/{created['id']}/messages", json={"text": "more"})
        again = receive(ws, "start_task")
        assert (again["folder"], again["session"], again["follow_up"]) == ("site", f"task-{created['id']}", True)
    assert client.get("/api/projects").json()[0]["tasks"] == 1


def test_a_task_cannot_use_someone_elses_project(client_factory, engine):
    from conftest import MASTER_KEY, make_subscription
    from sqlalchemy import select

    from kvist_cloud.db import make_session_factory, session_scope
    from kvist_cloud.models import User
    from kvist_cloud.secrets import Vault

    client = signed_in(client_factory, engine)
    project = client.post("/api/projects", json={"name": "mine"}).json()
    other = sign_in_as(client, engine, "stranger", 72)
    with session_scope(make_session_factory(engine)) as db:
        stranger = db.scalars(select(User).where(User.id == other)).one()
        stranger.model_provider = "deepseek"
        stranger.model_key_enc = Vault(MASTER_KEY).encrypt("sk-stranger-0123456789")
        make_subscription(db, stranger, "trial")
    assert client.post("/api/tasks", json={"goal": "x", "project_id": project["id"]}).status_code == 404


def test_a_task_without_a_project_keeps_its_own_folder(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    with runner(client, fake_provider) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        client.post("/api/tasks", json={"goal": "old way"})
        start = receive(ws, "start_task")
        assert start["folder"] == "" and start["session"] == ""


def test_the_folder_is_made_at_once_when_the_machine_is_awake(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    with runner(client, fake_provider) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        client.post("/api/projects", json={"name": "docs", "repo_url": ""})
        message = next(m for m in iter(ws.receive_json, None) if m["type"] == "prepare_project")
        assert message["folder"] == "docs" and message["repo_url"] == ""


def test_a_machine_that_was_asleep_gets_every_folder_when_it_connects(client_factory, engine, fake_provider):
    client = signed_in(client_factory, engine)
    client.post("/api/projects", json={"name": "docs"})
    client.post("/api/projects", json={"name": "site", "repo_url": "https://github.com/o/site.git"})
    with runner(client, fake_provider) as ws:
        ws.send_json({"type": "hello", "tasks": []})
        got = {}
        while len(got) < 2:
            message = ws.receive_json()
            if message["type"] == "prepare_project":
                got[message["folder"]] = message["repo_url"]
        assert got == {"docs": "", "site": "https://github.com/o/site.git"}


def test_a_session_has_an_address_of_its_own_that_only_its_owner_can_open(client_factory, engine):
    client = signed_in(client_factory, engine)
    first = client.post("/api/tasks", json={"goal": "one"}).json()
    second = client.post("/api/tasks", json={"goal": "two"}).json()
    assert second["public_id"] != first["public_id"]
    # Not the row number: an opaque id, different for every session.
    assert len(first["public_id"]) >= 12 and first["public_id"] != str(first["id"])
    mine = client.get(f"/api/sessions/{first['public_id']}")
    assert mine.status_code == 200 and mine.json()["id"] == first["id"]
    assert first["public_id"] in [item["public_id"] for item in client.get("/api/tasks").json()]

    sign_in_as(client, engine, "stranger", 81)
    refused = client.get(f"/api/sessions/{first['public_id']}")
    missing = client.get("/api/sessions/nothing-like-that")
    assert refused.status_code == 404 and refused.json() == missing.json()
    # The row number gets a stranger nowhere either.
    assert client.get(f"/api/tasks/{first['id']}").status_code == 404
    client.cookies.clear()
    assert client.get(f"/api/sessions/{first['public_id']}").status_code == 401
