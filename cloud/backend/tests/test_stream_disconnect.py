# -*- coding: utf-8 -*-
"""A browser that is already gone must not turn into a traceback when the server closes its socket."""
from __future__ import annotations

from conftest import dev_login, make_machine, make_task
from starlette.websockets import WebSocketDisconnect

from kvist_cloud import tasks
from kvist_cloud.db import session_scope
from kvist_cloud.models import User


class GoneBrowser:
    """What Starlette hands the endpoint once the other side has vanished: every call says code 1006."""

    def __init__(self, app, cookie: str) -> None:
        self.app, self.headers, self.cookies = app, {}, {"kc_session": cookie}
        self.closed_calls = 0

    async def accept(self) -> None:
        return None

    async def send_json(self, _message) -> None:
        raise WebSocketDisconnect(code=1006)

    async def receive_text(self) -> str:
        raise WebSocketDisconnect(code=1006)

    async def close(self, code: int = 1000) -> None:
        self.closed_calls += 1
        raise WebSocketDisconnect(code=1006)


def test_stream_to_a_vanished_browser_ends_quietly(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    with session_scope(client.app.state.session_factory) as db:
        user = db.get(User, 1)
        task_id = make_task(db, user, make_machine(db, user)).id
    socket = GoneBrowser(client.app, client.cookies.get("kc_session"))
    client.portal.call(tasks.stream, socket, task_id, 0)  # raised before the fix
    assert socket.closed_calls == 1
    # And nothing is left subscribed to the task.
    assert not client.app.state.hub._subscribers.get(task_id)
