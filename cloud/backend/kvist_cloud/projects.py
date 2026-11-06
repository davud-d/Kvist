# -*- coding: utf-8 -*-
"""Projects: a named folder on the person's machine, empty or a clone of one of their GitHub repositories.

A project's name is its folder, so names cannot repeat (compared without regard to case, with spaces and
hyphens alike): two projects would be one folder. Tasks (sessions) of a project work in its folder.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import current_user
from .db import get_db
from .errors import ApiError, not_found
from .machines import live_machine
from .models import Project, Task, User

router = APIRouter()

NAME_LIMIT = 60
# A letter or digit first, then letters, digits, spaces, dots, hyphens and underscores: safe as a folder name
# on the machine and readable on screen. No slash, so no way out of the work folder; no leading dot or dash.
NAME = re.compile(r"^\w[\w .\-]*$")
# The folders tasks from before projects existed use: a project must not become one of them.
RESERVED = re.compile(r"^task-\d+$", re.IGNORECASE)


def folder_of(name: str) -> str:
    return re.sub(r"\s+", "-", name.strip())


def name_problem(name: str) -> str:
    """Why this cannot be a project name; empty when it can."""
    if not name or len(name) > NAME_LIMIT or not NAME.match(name) or name.endswith((".", " ")):
        return "bad_project_name"
    if RESERVED.match(folder_of(name)):
        return "bad_project_name"
    return ""


def prepare_message(app: object, project: Project) -> dict[str, object]:
    """Ask the machine to have this project's folder: made empty, or cloned from the repository."""
    return {
        "type": "prepare_project",
        "folder": project.folder,
        "repo_url": project.repo_url,
        "allow_local": app.state.settings.allow_local_endpoints,
    }


def project_view(db: Session, project: Project) -> dict[str, object]:
    count = db.scalar(select(func.count()).select_from(Task).where(Task.project_id == project.id)) or 0
    return {
        "id": project.id,
        "name": project.name,
        "folder": project.folder,
        "repo_url": project.repo_url,
        "tasks": count,
        "created_at": project.created_at.isoformat(),
    }


def owned_project(db: Session, user: User, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise not_found("project")
    return project


def _name_taken() -> ApiError:
    return ApiError(409, "name_taken", "A project with this name already exists. / Проект с таким именем уже есть.")


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # A repository of the person's own, to clone into the folder; empty for an empty folder.
    repo_url: str = Field(default="", max_length=500)


@router.get("/api/projects")
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict[str, object]]:
    rows = db.scalars(select(Project).where(Project.user_id == user.id).order_by(Project.name))
    return [project_view(db, project) for project in rows]


@router.post("/api/projects", status_code=201)
async def create_project(
    payload: ProjectIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    from .tasks import repo_problem

    name = payload.name.strip()
    if name_problem(name):
        raise ApiError(
            400,
            "bad_project_name",
            "A project name is letters, digits, spaces, dots and hyphens, up to 60 characters. "
            "/ Имя проекта: буквы, цифры, пробелы, точки и дефисы, до 60 знаков.",
        )
    repo_url = payload.repo_url.strip()
    if repo_problem(repo_url, request.app.state.settings.allow_local_endpoints):
        raise ApiError(400, "bad_repo", "That is not a repository link. / Это не ссылка на репозиторий.")
    folder = folder_of(name)
    if db.scalar(select(Project.id).where(Project.user_id == user.id, Project.folder_key == folder.casefold())):
        raise _name_taken()
    project = Project(user_id=user.id, name=name, folder=folder, folder_key=folder.casefold(), repo_url=repo_url)
    db.add(project)
    try:
        db.flush()
    except IntegrityError as error:
        # Two requests with the same name at once: the database's own uniqueness decides.
        db.rollback()
        raise _name_taken() from error
    db.commit()
    view = project_view(db, project)
    machine = live_machine(db, user.id)
    if machine is not None:
        # The folder is made on the machine now if it is awake; if not, when its runner next connects.
        await request.app.state.gateway.send(machine.id, prepare_message(request.app, project))
    return view
