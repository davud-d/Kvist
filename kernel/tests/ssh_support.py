# -*- coding: utf-8 -*-
"""A real ``Daemon`` over a fake core, and a client that speaks to its socket like the shim does."""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

from kernel.agent.task_control import TaskControl
from kernel.protocol import event_stdio
from kernel.protocol.event_session import EventSession
from kernel.protocol.event_stream_ui import EventStreamUI
from kernel.protocol.frontend_protocol import FrontendChannel
from kernel.ssh import wire
from kernel.ssh.daemon import Daemon, private_directory
from kernel.ssh.event_log import EventLog
from kernel.ssh.inbox import ClientInbox
from kernel.ssh.lease import Lease
from kernel.ssh.paths import daemon_paths


class _Config:
    provider = "openai"
    fast_model = "f"
    smart_model = "s"


class _Agent:
    def __init__(self, ui: EventStreamUI, handler) -> None:
        self._ui = ui
        self._handler = handler
        self.control = TaskControl()
        self.config = _Config()
        self.current_session = "default"
        self.goals: list[str] = []
        self.cancelled = threading.Event()

    def _peek_checkpoint(self):
        return None

    def request_cancel(self) -> None:
        self.cancelled.set()

    def run(self, goal: str, routed_role: str | None = None) -> str:
        self.cancelled.clear()
        self.goals.append(goal)
        return self._handler(self, self._ui, goal)


def echo(agent, ui, goal):
    ui.print_final(f"done {goal}")
    return f"done {goal}"


class FakeCoreDaemon:
    """The daemon's own classes, a scripted agent in place of the model."""

    def __init__(
        self, tmp_path: Path, handler=echo, *, lease_seconds: float = 20.0, log_limit: int | None = None, commands=None
    ) -> None:
        self.workspace = str(tmp_path / "workspace")
        Path(self.workspace).mkdir()
        self.paths = daemon_paths(self.workspace)
        private_directory(self.paths.root)
        kwargs = {"limit": log_limit} if log_limit else {}
        self.log = EventLog(self.paths.log, **kwargs)
        self.daemon = Daemon(self.paths, self.log, ClientInbox(), Lease(lease_seconds))
        channel = FrontendChannel()
        ui = EventStreamUI(channel)
        self.agent = _Agent(ui, handler)
        self.session = EventSession(channel, self.agent)
        self.daemon.adopt_session(self.session)
        self.daemon.listen()
        self.thread = threading.Thread(
            target=event_stdio.serve_session_multi,
            kwargs={"session": self.session, "out_stream": self.log, "in_reader": self.daemon.inbox, "commands": commands},
            daemon=True,
        )
        self.thread.start()

    def client(self, name: str = "a", **hello) -> "Client":
        return Client(self.paths.socket, name, **hello)

    def close(self) -> None:
        self.daemon.request_stop()
        self.thread.join(timeout=3.0)
        self.daemon.close()


class Client:
    """What the shim and proxy are, seen from the daemon: lines in, lines out."""

    def __init__(self, socket_path: Path, name: str = "a", **hello) -> None:
        self.name = name
        self._connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._connection.connect(str(socket_path))
        self.lines: list[dict] = []
        self._changed = threading.Condition()
        self._send(
            {"type": wire.HELLO, "op": hello.pop("op", "attach"), "client": name, "from": hello.pop("start", None), **hello}
        )
        threading.Thread(target=self._collect, daemon=True).start()

    def _send(self, payload: dict) -> None:
        self._connection.sendall(wire.encode(payload))

    def _collect(self) -> None:
        for raw in self._connection.makefile("rb"):
            message = wire.decode(raw)
            with self._changed:
                self.lines.append(message if message is not None else {"type": "?", "raw": raw.decode()})
                self._changed.notify_all()
        with self._changed:
            self.closed = True
            self._changed.notify_all()

    closed = False

    def send(self, line: str | dict) -> None:
        text = line if isinstance(line, str) else json.dumps(line)
        self._connection.sendall(text.encode() + b"\n")

    def ping(self) -> None:
        self._send({"type": wire.PING})

    def wait_for(self, predicate, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                for line in self.lines:
                    if predicate(line):
                        return line
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{self.name}: nothing matched; got {[l.get('type') for l in self.lines]}")
                self._changed.wait(remaining)

    def types(self) -> list[str]:
        with self._changed:
            return [line.get("type", "") for line in self.lines]

    def wait_closed(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        with self._changed:
            while not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{self.name}: connection stayed open")
                self._changed.wait(remaining)

    def close(self) -> None:
        self._connection.close()
