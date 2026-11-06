# -*- coding: utf-8 -*-
"""The agent's screens (browser skill, desktop skill): what the owner sees, and nobody else.

What the person's agent does on its screen is as private as their files. The rules:

* A screen's address on the machine (a loopback URL with a secret token) never reaches this server:
  the runner strips it. Whatever arrives is stripped again here (``public_view``).
* Pictures come from the machine over its authenticated socket and live in this process's memory
  only: not in the database, not in task_events, not in logs, not in exports.
* The one way out is ``GET /api/tasks/{id}/screens/{screen}/frame.jpg``. It answers the signed-in
  owner of the task and no one else: another account, an admin and a signed-out visitor all get
  the same answer as for a task that does not exist.
* A picture is served ``private, no-store`` and goes stale in seconds; nothing is kept once the
  screen closes or the task ends.
"""
from __future__ import annotations

import base64
import binascii
import re
import time
from collections import OrderedDict
from collections.abc import Callable

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from .auth import current_user
from .db import get_db
from .errors import ApiError
from .models import User

router = APIRouter()

SCREEN_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
STATES = frozenset({"starting", "running", "paused", "unavailable", "closed"})
MAX_FRAME_BYTES = 1024 * 1024
# One owner watching two screens of two tasks is the normal worst case; a bigger set is evicted oldest first.
MAX_FRAMES = 64
FRESH_SECONDS = 10.0
LEASE_SECONDS = 10.0
RENEW_EVERY_SECONDS = 4.0
JPEG_MAGIC = b"\xff\xd8\xff"


def frame_path(task_id: int, screen_id: str) -> str:
    return f"/api/tasks/{task_id}/screens/{screen_id}/frame.jpg"


def public_view(task_id: int, event: dict) -> dict | None:
    """The ``screen`` event as a browser may get it: a whitelist, with our own address in place of the machine's."""
    screen_id = str(event.get("screen_id") or "")
    state = str(event.get("state") or "")
    if not SCREEN_ID.match(screen_id) or state not in STATES:
        return None
    return {
        "type": "screen",
        "screen_id": screen_id,
        "skill": str(event.get("skill") or "")[:64],
        "title": str(event.get("title") or "")[:120],
        "state": state,
        "url": "",
        "stream_url": "",
        "frame_url": frame_path(task_id, screen_id) if state != "closed" else "",
    }


class ScreenStore:
    """Current screens and their latest picture, per task, in memory."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._views: dict[int, dict[str, dict]] = {}
        self._frames: OrderedDict[tuple[int, str], tuple[float, bytes]] = OrderedDict()
        self._renewed: dict[tuple[int, str], float] = {}

    def apply(self, task_id: int, event: dict) -> dict | None:
        """Take a ``screen`` event; returns what may be shown (None: not a usable event)."""
        view = public_view(task_id, event)
        if view is None:
            return None
        if view["state"] == "closed":
            self._views.get(task_id, {}).pop(view["screen_id"], None)
            self._drop_frame(task_id, view["screen_id"])
        else:
            self._views.setdefault(task_id, {})[view["screen_id"]] = view
        return view

    def views(self, task_id: int) -> list[dict]:
        return list(self._views.get(task_id, {}).values())

    def knows(self, task_id: int, screen_id: str) -> bool:
        return screen_id in self._views.get(task_id, {})

    def put_frame(self, task_id: int, screen_id: str, jpeg: bytes) -> bool:
        if not self.knows(task_id, screen_id) or len(jpeg) > MAX_FRAME_BYTES or not jpeg.startswith(JPEG_MAGIC):
            return False
        key = (task_id, screen_id)
        self._frames[key] = (self._clock(), jpeg)
        self._frames.move_to_end(key)
        while len(self._frames) > MAX_FRAMES:
            self._frames.popitem(last=False)
        return True

    def frame(self, task_id: int, screen_id: str) -> bytes | None:
        entry = self._frames.get((task_id, screen_id))
        if entry is None or self._clock() - entry[0] > FRESH_SECONDS:
            return None
        return entry[1]

    def renew_due(self, task_id: int, screen_id: str) -> bool:
        """True when the machine should be told again that someone is watching."""
        now = self._clock()
        key = (task_id, screen_id)
        if now - self._renewed.get(key, -RENEW_EVERY_SECONDS) < RENEW_EVERY_SECONDS:
            return False
        self._renewed[key] = now
        return True

    def forget_task(self, task_id: int) -> None:
        self._views.pop(task_id, None)
        for key in [key for key in self._frames if key[0] == task_id]:
            del self._frames[key]
        for key in [key for key in self._renewed if key[0] == task_id]:
            del self._renewed[key]

    def _drop_frame(self, task_id: int, screen_id: str) -> None:
        self._frames.pop((task_id, screen_id), None)
        self._renewed.pop((task_id, screen_id), None)


def decode_frame(message: dict) -> bytes | None:
    raw = message.get("jpeg")
    if not isinstance(raw, str) or len(raw) > MAX_FRAME_BYTES * 4 // 3 + 8:
        return None
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None


# -- API: the owner's own view -------------------------------------------------------------


@router.get("/api/tasks/{task_id}/screens")
def list_screens(
    task_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[dict]:
    from .tasks import owned_task

    owned_task(db, user, task_id)
    return request.app.state.screens.views(task_id)


@router.get("/api/tasks/{task_id}/screens/{screen_id}/frame.jpg")
async def get_frame(
    task_id: int,
    screen_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    from .tasks import owned_task

    # Ownership first: whoever else asks learns nothing, not even whether the screen exists.
    task = owned_task(db, user, task_id)
    # A hot-linked <img> on another site cannot set this header (and has no cookie either).
    if request.headers.get("x-kvist-client") != "1":
        raise ApiError(403, "csrf", "Reload the page. / Обнови страницу.")
    store: ScreenStore = request.app.state.screens
    if not SCREEN_ID.match(screen_id) or not store.knows(task_id, screen_id):
        return Response(status_code=204, headers=_private())
    if store.renew_due(task_id, screen_id):
        await request.app.state.gateway.send(
            task.machine_id,
            {"type": "screen_watch", "task_id": task_id, "screen_id": screen_id, "lease_seconds": LEASE_SECONDS},
        )
    frame = store.frame(task_id, screen_id)
    if frame is None:
        return Response(status_code=204, headers=_private())
    return Response(content=frame, media_type="image/jpeg", headers=_private())


def _private() -> dict[str, str]:
    return {
        "Cache-Control": "private, no-store, max-age=0",
        "Pragma": "no-cache",
        "Cross-Origin-Resource-Policy": "same-origin",
        "X-Content-Type-Options": "nosniff",
    }
