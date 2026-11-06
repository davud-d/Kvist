# -*- coding: utf-8 -*-
"""Work folders: a project's, or one per task for older tasks; optionally a clone of a repository; and archives."""
from __future__ import annotations

import asyncio
import base64
import os
import re
import tarfile
from pathlib import Path
from urllib.parse import urlparse

CLONE_TIMEOUT_SECONDS = 600


class WorkspaceError(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def task_dir(work_root: Path, task_id: int) -> Path:
    return work_root / f"task-{task_id}"


def project_dir(work_root: Path, folder: str) -> Path:
    """A project's folder under the work root; a name that could leave it is refused, whoever sent it."""
    if not folder or folder in (".", "..") or "/" in folder or "\\" in folder or "\0" in folder:
        raise WorkspaceError("bad_folder")
    return work_root / folder


def folder_for(work_root: Path, task_id: int, folder: str = "") -> Path:
    return project_dir(work_root, folder) if folder else task_dir(work_root, task_id)


def allowed_repo(url: str, allow_local: bool) -> bool:
    if re.fullmatch(r"https://github\.com/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", url):
        return url.rsplit("/", 1)[-1] not in {".", ".."}
    return allow_local and urlparse(url).scheme == "file"


async def prepare(
    work_root: Path, task_id: int, repo_url: str, allow_local: bool, project: str = "", github_token: str = ""
) -> Path:
    """The task's folder: the project's when it has one, else its own. Made (or cloned) the first time only."""
    folder = folder_for(work_root, task_id, project)
    if folder.is_symlink():
        raise WorkspaceError("bad_folder")
    if folder.exists():
        return folder
    work_root.mkdir(parents=True, exist_ok=True)
    if not repo_url:
        folder.mkdir()
        return folder
    if not allowed_repo(repo_url, allow_local):
        raise WorkspaceError("repo_not_allowed")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_TERMINAL_PROMPT": "0", "LANG": "C.UTF-8"}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    auth = ""
    config = [("http.followRedirects", "false"), ("credential.helper", "")]
    if github_token and urlparse(repo_url).hostname == "github.com":
        auth = base64.b64encode(f"x-access-token:{github_token}".encode()).decode()
        config.append(("http.https://github.com/.extraHeader", f"Authorization: Basic {auth}"))
    env["GIT_CONFIG_COUNT"] = str(len(config))
    for index, (key, value) in enumerate(config):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    process = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "50", "--", repo_url, str(folder),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        _out, err = await asyncio.wait_for(process.communicate(), CLONE_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise WorkspaceError("clone_failed", "timed out") from None
    if process.returncode != 0:
        detail = err.decode("utf-8", "replace")
        for secret in (github_token, auth):
            if secret:
                detail = detail.replace(secret, "[redacted]")
        raise WorkspaceError("clone_failed", detail[-300:])
    return folder


def write_archive(source: Path, target: Path) -> int:
    """A .tar.gz of ``source``; returns its size. Symlinks are stored as links, never followed."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target, "w:gz") as archive:
        archive.add(source, arcname=source.name, recursive=True)
    return target.stat().st_size
