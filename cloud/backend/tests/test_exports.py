# -*- coding: utf-8 -*-
"""Archive export: through a real runner end to end, and the upload's one-time token."""
from __future__ import annotations

import io
import tarfile

from conftest import dev_login, eventually, make_task, settle
from sqlalchemy import select
from test_gateway import _start_hanging_task, _status

from kvist_cloud.db import make_session_factory, session_scope
from kvist_cloud.models import Export, Machine, TaskStatus, User


def test_task_and_machine_archives_with_a_real_runner(live_stack):
    stack = live_stack()
    machine_id, task_id = _start_hanging_task(stack, goal="quick")
    eventually(lambda: _status(stack, task_id) == TaskStatus.DONE, timeout=30)

    started = stack.http.post(f"/api/tasks/{task_id}/export")
    assert started.status_code == 202 and started.json()["status"] == "pending"
    export_id = started.json()["id"]
    ready = eventually(lambda: (lambda body: body if body["status"] == "ready" else None)(
        stack.http.get(f"/api/exports/{export_id}").json()), timeout=30)
    download = stack.http.get(ready["download_url"])
    assert download.status_code == 200
    assert f"kvist-task-{task_id}.tar.gz" in download.headers["content-disposition"]
    with tarfile.open(fileobj=io.BytesIO(download.content)) as archive:
        names = archive.getnames()
        hello = archive.extractfile(f"task-{task_id}/hello.txt").read()
    assert f"task-{task_id}/hello.txt" in names and hello == b"hello from the cloud\n"

    whole = stack.http.post(f"/api/machines/{machine_id}/export").json()
    done = eventually(lambda: (lambda body: body if body["status"] == "ready" else None)(
        stack.http.get(f"/api/exports/{whole['id']}").json()), timeout=30)
    with tarfile.open(fileobj=io.BytesIO(stack.http.get(done["download_url"]).content)) as archive:
        assert f"work/task-{task_id}/hello.txt" in archive.getnames()


def test_export_of_a_task_with_no_folder_fails_plainly(live_stack):
    stack = live_stack()
    dev_login(stack.http)
    machine_id = eventually(lambda: _connected(stack))
    with stack.db() as db:
        user = db.scalars(select(User)).one()
        task_id = make_task(db, user, db.get(Machine, machine_id), status=TaskStatus.DONE).id
        db.commit()
    export_id = stack.http.post(f"/api/tasks/{task_id}/export").json()["id"]
    failed = eventually(lambda: (lambda body: body if body["status"] == "failed" else None)(
        stack.http.get(f"/api/exports/{export_id}").json()), timeout=30)
    assert failed["error"] == "nothing_to_export"


def _connected(stack):
    with stack.db() as db:
        machine = db.scalars(select(Machine)).first()
        return machine.id if machine is not None and machine.connected else None


def test_upload_needs_the_exports_own_token(client_factory, engine):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    settle(client)
    export_id = client.post("/api/machines/1/export").json()["id"]
    assert client.put(f"/gateway/exports/{export_id}", content=b"x").status_code == 403
    assert client.put(f"/gateway/exports/{export_id}", content=b"x",
                      headers={"Authorization": "Bearer guessed"}).status_code == 403
    with session_scope(make_session_factory(engine)) as db:
        assert db.get(Export, export_id).status == "pending"


def test_someone_elses_export_is_not_found(client_factory, engine):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    settle(client)
    with session_scope(make_session_factory(engine)) as db:
        stranger = User(github_id=55, github_login="stranger")
        db.add(stranger)
        db.flush()
        export = Export(user_id=stranger.id, machine_id=1, status="ready")
        db.add(export)
        db.flush()
        foreign = export.id
    assert client.get(f"/api/exports/{foreign}").status_code == 404
    assert client.get(f"/api/exports/{foreign}/download").status_code == 404
