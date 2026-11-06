# -*- coding: utf-8 -*-
"""Pack a folder of this machine and upload it to the server with the export's one-time token."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

import httpx

from .workspace import WorkspaceError, folder_for, write_archive

log = logging.getLogger("kvist_runner.export")
UPLOAD_TIMEOUT_SECONDS = 3600.0
CHUNK = 1024 * 1024


async def _chunks(path: Path):
    with open(path, "rb") as handle:
        while True:
            chunk = await asyncio.to_thread(handle.read, CHUNK)
            if not chunk:
                return
            yield chunk


async def run_export(runner, message: dict) -> None:
    export_id = int(message["export_id"])
    task_id = message.get("task_id")
    work = runner.config.work_dir
    try:
        source = folder_for(work, int(task_id), str(message.get("folder") or "")) if task_id else work
    except WorkspaceError:
        _failed(runner, export_id, "bad_folder")
        return
    archive = runner.config.root / "exports" / f"{export_id}.tar.gz"
    if not source.is_dir():
        _failed(runner, export_id, "nothing_to_export")
        return
    try:
        await asyncio.to_thread(write_archive, source, archive)
        async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT_SECONDS) as client:
            response = await client.put(
                str(message["upload_url"]),
                content=_chunks(archive),
                headers={"Authorization": f"Bearer {message['upload_token']}",
                         "Content-Type": "application/gzip"},
            )
        if response.status_code != 200:
            raise RuntimeError(f"upload refused (HTTP {response.status_code})")
        log.info("export %s uploaded", export_id)
    except (OSError, RuntimeError, httpx.HTTPError) as error:
        _failed(runner, export_id, type(error).__name__)
    finally:
        with contextlib.suppress(OSError):
            archive.unlink(missing_ok=True)


def _failed(runner, export_id: int, reason: str) -> None:
    log.warning("export %s failed: %s", export_id, reason)
    runner.emit({"type": "export_done", "export_id": export_id, "ok": False, "error": reason})
