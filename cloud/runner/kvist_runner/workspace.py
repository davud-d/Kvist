# -*- coding: utf-8 -*-
"""Task folders: one per task, optionally a clone of a repository; and their archives."""
from __future__ import annotations

import asyncio
import os
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


def allowed_repo(url: str, allow_local: bool) -> bool:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname:
        return True
    return allow_local and parsed.scheme == "file"


async def prepare(work_root: Path, task_id: int, repo_url: str, allow_local: bool) -> Path:
    folder = task_dir(work_root, task_id)
    if folder.exists():
        return folder
    work_root.mkdir(parents=True, exist_ok=True)
    if not repo_url:
        folder.mkdir()
        return folder
    if not allowed_repo(repo_url, allow_local):
        raise WorkspaceError("repo_not_allowed")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_TERMINAL_PROMPT": "0", "LANG": "C.UTF-8"}
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
        raise WorkspaceError("clone_failed", err.decode("utf-8", "replace")[-300:])
    return folder


def write_archive(source: Path, target: Path) -> int:
    """A .tar.gz of ``source``; returns its size. Symlinks are stored as links, never followed."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target, "w:gz") as archive:
        archive.add(source, arcname=source.name, recursive=True)
    return target.stat().st_size
