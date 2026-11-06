# -*- coding: utf-8 -*-
"""Archive export of a machine's files (all task folders) or of one task's folder.

The runner packs the folder and uploads it with an HTTP PUT carrying a one-time token; the
browser then downloads it. Going through the runner, not the provider, keeps it the same on any
hosting.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import current_user
from .db import get_db, session_scope
from .errors import ApiError, not_found
from .machines import live_machine, owned_machine, spawn, wake
from .models import Export, Machine, Project, Task, User

log = logging.getLogger("kvist_cloud.exports")
router = APIRouter()
CHUNK = 1024 * 1024
# Above the largest plan's disk: an upload past it is not an archive of that machine.
MAX_ARCHIVE_BYTES = 160 * 1024 ** 3


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def archive_path(app: FastAPI, export_id: int) -> Path:
    return app.state.settings.data_dir / "exports" / f"{export_id}.tar.gz"


def export_view(export: Export) -> dict[str, object]:
    return {
        "id": export.id,
        "status": export.status,
        "task_id": export.task_id,
        "size_bytes": export.size_bytes,
        "error": export.error,
        "download_url": f"/api/exports/{export.id}/download" if export.status == "ready" else None,
    }


def _task_folder(db: Session, task_id: int | None) -> str:
    """The folder of the task's project; empty for a task with a folder of its own."""
    task = db.get(Task, task_id) if task_id else None
    project = db.get(Project, task.project_id) if task is not None and task.project_id else None
    return project.folder if project is not None else ""


def _request_message(app: FastAPI, db: Session, export: Export) -> dict[str, object]:
    """A fresh one-time token each time the request is (re)sent; only its hash is kept."""
    token = secrets.token_urlsafe(32)
    export.upload_token_hash = _hash(token)
    return {
        "type": "export",
        "export_id": export.id,
        "task_id": export.task_id,
        "folder": _task_folder(db, export.task_id),
        "upload_url": f"{app.state.settings.public_url}/gateway/exports/{export.id}",
        "upload_token": token,
    }


async def send_pending(app: FastAPI, machine_id: int) -> None:
    """Ask the runner for every export still waiting; called when it connects."""
    with session_scope(app.state.session_factory) as db:
        pending = db.scalars(select(Export).where(Export.machine_id == machine_id, Export.status == "pending"))
        messages = [_request_message(app, db, export) for export in pending]
    for message in messages:
        await app.state.gateway.send(machine_id, message)


def on_export_done(app: FastAPI, machine_id: int, message: dict) -> None:
    """The runner could not make the archive: say so instead of waiting forever."""
    if message.get("ok"):
        return
    with session_scope(app.state.session_factory) as db:
        export = db.get(Export, int(message.get("export_id") or 0))
        if export is None or export.machine_id != machine_id or export.status != "pending":
            return
        export.status = "failed"
        export.error = str(message.get("error", "failed"))[:300]


async def _start(app: FastAPI, db: Session, user: User, machine: Machine, task_id: int | None) -> dict[str, object]:
    export = Export(user_id=user.id, machine_id=machine.id, task_id=task_id, status="pending")
    db.add(export)
    db.flush()
    message = _request_message(app, db, export)
    db.commit()
    if app.state.gateway.connected(machine.id):
        await app.state.gateway.send(machine.id, message)
    else:
        # Asleep: wake it; the request goes out when its runner connects.
        spawn(app, wake(app, machine.id))
    return export_view(export)


@router.post("/api/machines/{machine_id}/export", status_code=202)
async def export_machine(
    machine_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    machine = owned_machine(db, user, machine_id)
    return await _start(request.app, db, user, machine, None)


@router.post("/api/tasks/{task_id}/export", status_code=202)
async def export_task(
    task_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    task = db.get(Task, task_id)
    if task is None or task.user_id != user.id:
        raise not_found("task")
    machine = live_machine(db, user.id)
    if machine is None or machine.id != task.machine_id:
        raise ApiError(410, "machine_gone", "The machine of this task was deleted. / Машина этой задачи удалена.")
    return await _start(request.app, db, user, machine, task.id)


def _owned_export(db: Session, user: User, export_id: int) -> Export:
    export = db.get(Export, export_id)
    if export is None or export.user_id != user.id:
        raise not_found("export")
    return export


@router.get("/api/exports/{export_id}")
def get_export(export_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return export_view(_owned_export(db, user, export_id))


@router.get("/api/exports/{export_id}/download")
def download_export(
    export_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    export = _owned_export(db, user, export_id)
    path = archive_path(request.app, export.id)
    if export.status != "ready" or not path.exists():
        raise not_found("archive")
    name = f"kvist-task-{export.task_id}.tar.gz" if export.task_id else f"kvist-machine-{export.machine_id}.tar.gz"
    return FileResponse(path, media_type="application/gzip", filename=name)


@router.put("/gateway/exports/{export_id}")
async def upload_export(export_id: int, request: Request) -> dict[str, bool]:
    """The runner's upload. Authorised by the one-time token of this export only."""
    app = request.app
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip()
    with session_scope(app.state.session_factory) as db:
        export = db.get(Export, export_id)
        valid = (
            export is not None
            and export.status == "pending"
            and bool(token)
            and secrets.compare_digest(export.upload_token_hash, _hash(token))
        )
    if not valid:
        raise ApiError(403, "forbidden", "Not allowed.")
    target = archive_path(app, export_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    size = 0
    with open(partial, "wb") as handle:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES:
                handle.close()
                partial.unlink(missing_ok=True)
                raise ApiError(413, "too_large", "Archive too large.")
            handle.write(chunk)
    partial.replace(target)
    with session_scope(app.state.session_factory) as db:
        export = db.get(Export, export_id)
        export.status = "ready"
        export.size_bytes = size
        # Spent: the token cannot be used for a second upload.
        export.upload_token_hash = ""
    log.info("export %s ready (%s bytes)", export_id, size)
    return {"ok": True}
