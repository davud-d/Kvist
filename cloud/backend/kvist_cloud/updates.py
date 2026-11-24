# -*- coding: utf-8 -*-
"""Bringing a customer's machine up to the latest Kvist and runner without a new disk image.

The disk image is built once per set of wheels, and a customer's machine is a thin layer over the image it was
created from, so a machine would otherwise keep the Kvist it was born with. Instead the runner installs a release
beside the running one and switches to it with one pointer (runner/kvist_runner/release.py):

* A machine left alone for ``update_idle_seconds`` (asleep ones are woken for it) is told to stage the release.
* Staging never touches a task that is running, so a person who comes back meanwhile just works on the old Kvist.
* When staging is done the runner is told to apply: it stops every running Kvist within seconds, switches and
  restarts itself. The stopped tasks come back as ``core_updated`` and continue in the same session
  (``resume``); nothing is dispatched to the machine until it has said hello again.

Messages: to the runner ``update`` (stage) and ``apply_release``; from it ``update_ready`` and ``update_failed``.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db, session_scope, utcnow
from .errors import not_found
from .machines import REMOTE_PROVIDER, SWEEP_SECONDS, _has_work, spawn, wake
from .models import Machine, MachineStatus

log = logging.getLogger("kvist_cloud.updates")

# The two wheels a release is made of; every other wheel in the directory is a dependency, which only a new disk
# image changes.
WHEEL_PREFIXES = ("kvist-", "kvist_runner-")
# A failed release is not tried again on the same machine for this long: a broken one must not wake machines all day.
RETRY_AFTER = timedelta(hours=1)
# A machine that was told to stage has this long before it is asked again.
STAGING_ALLOWANCE = timedelta(minutes=10)
# The runner needs a moment to stop Kvist, switch and come back; dispatching earlier would start a task that dies.
APPLY_ALLOWANCE = timedelta(minutes=2)
# After a deploy every machine is behind at once; waking them all together would swamp the VM host.
MAX_WAKES_PER_SWEEP = 2

router = APIRouter()


@dataclass(frozen=True, slots=True)
class Release:
    id: str
    files: tuple[dict[str, object], ...]
    directory: Path

    def message(self) -> dict[str, object]:
        return {"type": "update", "release": self.id, "files": list(self.files)}


@dataclass(slots=True)
class UpdateState:
    """What the server remembers between messages; losing it on a restart only repeats a harmless step."""

    # Machines being switched: nothing is dispatched to them until they say hello again.
    applying: dict[int, object] = field(default_factory=dict)
    staging: dict[int, object] = field(default_factory=dict)
    failed: dict[int, object] = field(default_factory=dict)
    # Asleep machines woken for an update: they are told as soon as they connect.
    wanted: set[int] = field(default_factory=set)
    # (name, size, mtime) of the wheels -> the release they make, so the sweep does not hash 12 MB every 30 seconds.
    cache: tuple[tuple, Release] | None = None


def _wheel_files(directory: Path) -> list[Path]:
    found = []
    for prefix in WHEEL_PREFIXES:
        matches = sorted(directory.glob(f"{prefix}*.whl"))
        if not matches:
            return []
        found.append(matches[-1])
    return found


def current_release(app: FastAPI) -> Release | None:
    """The release the wheels on this server make; None when updates are off or the wheels are not all there."""
    directory = app.state.settings.wheels_dir
    if directory is None or not directory.is_dir():
        return None
    wheels = _wheel_files(directory)
    if not wheels:
        return None
    state: UpdateState = app.state.updates
    stamp = tuple((wheel.name, wheel.stat().st_size, wheel.stat().st_mtime_ns) for wheel in wheels)
    if state.cache is not None and state.cache[0] == stamp:
        return state.cache[1]
    files = []
    overall = hashlib.sha256()
    for wheel in wheels:
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        overall.update(digest.encode("ascii"))
        files.append({"name": wheel.name, "sha256": digest, "size": wheel.stat().st_size})
    release = Release(overall.hexdigest()[:16], tuple(files), directory)
    state.cache = (stamp, release)
    return release


def is_applying(app: FastAPI, machine_id: int) -> bool:
    started = app.state.updates.applying.get(machine_id)
    return started is not None and utcnow() - started < APPLY_ALLOWANCE


def is_updating(app: FastAPI, machine_id: int) -> bool:
    """Staging or switching, for the page to say so. The page only reads this: no route sets it."""
    staging = app.state.updates.staging.get(machine_id)
    return is_applying(app, machine_id) or (staging is not None and utcnow() - staging < STAGING_ALLOWANCE)


async def _send_update(app: FastAPI, machine_id: int, release: Release) -> None:
    state: UpdateState = app.state.updates
    state.staging[machine_id] = utcnow()
    if not await app.state.gateway.send(machine_id, release.message()):
        state.staging.pop(machine_id, None)


async def on_hello(app: FastAPI, machine_id: int, message: dict) -> None:
    """The runner says which release it runs; a machine woken for an update is told to stage it now."""
    state: UpdateState = app.state.updates
    state.applying.pop(machine_id, None)
    reported = str(message.get("release") or "")[:32]
    with session_scope(app.state.session_factory) as db:
        machine = db.get(Machine, machine_id)
        if machine is not None:
            machine.runner_release = reported
    release = current_release(app)
    if release is not None and reported != release.id and machine_id in state.wanted:
        await _send_update(app, machine_id, release)
    state.wanted.discard(machine_id)


async def on_ready(app: FastAPI, machine_id: int, message: dict) -> None:
    """Staging is done: switch now, whoever is working (a running Kvist is stopped and continued).

    Only an answer to an ``update`` this server sent counts: "this machine is switching" is a state the server
    alone starts, because while it holds nothing is dispatched to the machine and a stopped task is not billed.
    """
    state: UpdateState = app.state.updates
    if state.staging.pop(machine_id, None) is None:
        log.warning("machine %s reported a staged release nobody asked for", machine_id)
        return
    release = current_release(app)
    if release is None or str(message.get("release")) != release.id:
        # A newer release was built meanwhile; the next sweep stages that one.
        return
    state.applying[machine_id] = utcnow()
    if not await app.state.gateway.send(machine_id, {"type": "apply_release", "release": release.id}):
        state.applying.pop(machine_id, None)


def on_failed(app: FastAPI, machine_id: int, message: dict) -> None:
    state: UpdateState = app.state.updates
    if state.staging.pop(machine_id, None) is None and machine_id not in state.applying:
        return
    state.applying.pop(machine_id, None)
    state.failed[machine_id] = utcnow()
    log.error("machine %s could not stage release %s: %s", machine_id, message.get("release"),
              str(message.get("reason", ""))[:200])


def _due(app: FastAPI, db: Session, machine: Machine, release: Release, now) -> bool:
    state: UpdateState = app.state.updates
    if machine.runner_release == release.id or machine.provider == REMOTE_PROVIDER or machine.suspended:
        return False
    if machine.status not in (MachineStatus.AWAKE, MachineStatus.ASLEEP):
        return False
    if now - machine.last_active_at < timedelta(seconds=app.state.settings.update_idle_seconds):
        return False
    if _has_work(db, machine):
        return False
    failed, staging = state.failed.get(machine.id), state.staging.get(machine.id)
    if failed is not None and now - failed < RETRY_AFTER:
        return False
    return not (staging is not None and now - staging < STAGING_ALLOWANCE)


async def _end_stale_switches(app: FastAPI) -> None:
    """A switch that did not finish in its allowance ends: what waits for the machine is dispatched again.

    Without this a runner that never says hello again would leave its resumed tasks waiting, not running and so
    not billed, for as long as it stays connected.
    """
    from .gateway import dispatch

    state: UpdateState = app.state.updates
    now = utcnow()
    for machine_id, started in list(state.applying.items()):
        if now - started >= APPLY_ALLOWANCE:
            del state.applying[machine_id]
            await dispatch(app, machine_id)


async def sweep_once(app: FastAPI) -> None:
    await _end_stale_switches(app)
    release = current_release(app)
    if release is None:
        return
    now = utcnow()
    tell: list[int] = []
    wake_up: list[int] = []
    with session_scope(app.state.session_factory) as db:
        candidates = select(Machine).where(Machine.status.in_((MachineStatus.AWAKE, MachineStatus.ASLEEP)))
        for machine in db.scalars(candidates):
            if not _due(app, db, machine, release, now):
                continue
            (tell if app.state.gateway.connected(machine.id) else wake_up).append(machine.id)
    for machine_id in tell:
        await _send_update(app, machine_id, release)
    for machine_id in wake_up[:MAX_WAKES_PER_SWEEP]:
        app.state.updates.wanted.add(machine_id)
        spawn(app, wake(app, machine_id))


async def sweep_forever(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            await sweep_once(app)
        except Exception:  # noqa: BLE001 - the loop must survive one bad pass; the error is logged
            log.exception("update sweep failed")


@router.get("/gateway/release/{release}/{name}")
def download(release: str, name: str, request: Request, db: Session = Depends(get_db)) -> FileResponse:
    """A wheel of the current release, for the machine that asks with its own token."""
    from .gateway import _machine_for_token  # gateway imports this module

    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    current = current_release(request.app)
    if _machine_for_token(db, token) is None or current is None or release != current.id:
        raise not_found("release")
    # Only a name from the manifest is ever opened, so no path the caller makes up reaches the disk.
    if name not in {str(item["name"]) for item in current.files} or not re.fullmatch(r"[\w.+-]+\.whl", name):
        raise not_found("release")
    return FileResponse(current.directory / name, media_type="application/octet-stream")
