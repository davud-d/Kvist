# -*- coding: utf-8 -*-
"""Link from a terminal-hosted core to the tray icon (``frontends/tray``).

The icon is one background Electron process for the whole machine. Every core
that runs inside a terminal (classic REPL or the Ink app's core) connects to it
over loopback TCP and sends a snapshot of its state: workspace, session and
whether a turn is running or has just finished. The daemon decides what to
draw; the core never reads anything back.

The endpoint file ``~/.kvist/tray.json`` (``{"port", "token"}``, written 0600
by the daemon) is the only discovery mechanism. A core that cannot connect
starts the daemon once per process; the daemon's single-instance lock makes
concurrent starts from several terminals harmless.
"""
from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from kernel.tray.tray_wire import endpoint_file
from kernel.config.user_config import get_tray_enabled

HOST_ENV = "KVIST_TRAY_HOST"
LAUNCHER_ENV = "KVIST_TRAY_CMD"
_TRAY_DIR = Path(__file__).resolve().parent.parent.parent / "frontends" / "tray"
_CONNECT_TIMEOUT_SECONDS = 1.0
_DAEMON_START_WAIT_SECONDS = 20.0
_DAEMON_POLL_SECONDS = 0.2


def mark_terminal_host() -> None:
    """Tell this process and its children (the Ink app's core) they live in a terminal."""
    os.environ[HOST_ENV] = "1"


def is_terminal_host(env: dict[str, str] | None = None) -> bool:
    return (os.environ if env is None else env).get(HOST_ENV) == "1"


def find_launcher(env: dict[str, str] | None = None, tray_dir: Path = _TRAY_DIR) -> list[str] | None:
    """Return the argv that starts the tray daemon, or None when this install has none.

    ``KVIST_TRAY_CMD`` (a JSON argv) wins; otherwise a source checkout's
    ``frontends/tray`` with its installed Electron. A machine without either
    simply has no icon, which is not a misconfiguration.
    """
    env = os.environ if env is None else env
    explicit = env.get(LAUNCHER_ENV)
    if explicit:
        argv = json.loads(explicit)
        if not (isinstance(argv, list) and argv and all(isinstance(item, str) for item in argv)):
            raise ValueError(f"{LAUNCHER_ENV} must be a JSON array of strings.")
        return argv
    electron_dir = tray_dir / "node_modules" / "electron"
    path_file = electron_dir / "path.txt"
    if not path_file.is_file():
        return None
    binary = electron_dir / "dist" / path_file.read_text(encoding="utf-8").strip()
    return [str(binary), str(tray_dir)] if binary.is_file() else None


def core_command() -> list[str]:
    """The argv that starts this core again (the frozen binary, or the interpreter plus the entry point)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve().parent.parent / "__main__.py")]


def _spawn_detached(argv: list[str]) -> None:
    options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    child_env = {key: value for key, value in os.environ.items() if key != "ELECTRON_RUN_AS_NODE"}
    # The daemon runs `--session-op history` with the same core that started it.
    child_env["KVIST_CORE_CMD"] = json.dumps(core_command())
    subprocess.Popen(argv, env=child_env, **options)


class TrayLink:
    """Reports one core's turn state to the tray daemon from a background thread."""

    # Shared by every link in the process: a process that builds many agents
    # must still start the daemon at most once.
    _daemon_start_attempted = False

    def __init__(
        self,
        workspace: Path,
        session_name: Callable[[], str],
        *,
        enabled: Callable[[], bool] = get_tray_enabled,
        launcher: Callable[[], list[str] | None] = find_launcher,
        endpoint: Callable[[], Path] = endpoint_file,
        spawn: Callable[[list[str]], None] = _spawn_detached,
        debug: Callable[[str], None] = lambda message: None,
    ) -> None:
        self._workspace = str(workspace)
        self._session_name = session_name
        self._enabled = enabled
        self._launcher = launcher
        self._endpoint = endpoint
        self._spawn = spawn
        self._debug = debug
        self._pending: queue.SimpleQueue[dict[str, object] | None] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._connection: socket.socket | None = None

    def turn_started(self) -> None:
        self._publish("running")

    def turn_finished(self) -> None:
        self._publish("done")

    def close(self) -> None:
        """Say goodbye so the daemon drops this core without waiting for a timeout."""
        if self._thread is None:
            return
        self._pending.put(None)
        self._thread.join(timeout=2.0)

    def _publish(self, state: str) -> None:
        snapshot = {
            "type": "state",
            "pid": os.getpid(),
            "workspace": self._workspace,
            "session": self._session_name(),
            "state": state,
        }
        if self._thread is None:
            self._thread = threading.Thread(target=self._serve, name="kvist-tray-link", daemon=True)
            self._thread.start()
        self._pending.put(snapshot)

    def _serve(self) -> None:
        while True:
            snapshot = self._pending.get()
            if snapshot is None:
                self._disconnect()
                return
            snapshot = self._latest(snapshot)
            if not self._enabled():
                self._disconnect()
                continue
            self._deliver(snapshot)

    def _latest(self, snapshot: dict[str, object]) -> dict[str, object]:
        # Snapshots are complete, so only the newest one matters.
        while True:
            try:
                newer = self._pending.get_nowait()
            except queue.Empty:
                return snapshot
            if newer is None:
                self._pending.put(None)
                return snapshot
            snapshot = newer

    def _deliver(self, snapshot: dict[str, object]) -> None:
        for _ in range(2):
            connection = self._connection or self._connect()
            if connection is None:
                return
            try:
                connection.sendall((json.dumps(snapshot) + "\n").encode("utf-8"))
                return
            except OSError as error:
                self._debug(f"Tray link lost: {error}")
                self._disconnect()

    def _connect(self) -> socket.socket | None:
        connection = self._try_connect()
        if connection is None and not TrayLink._daemon_start_attempted:
            TrayLink._daemon_start_attempted = True
            argv = self._launcher()
            if argv is None:
                return None
            self._spawn(argv)
            deadline = time.monotonic() + _DAEMON_START_WAIT_SECONDS
            while connection is None and time.monotonic() < deadline:
                time.sleep(_DAEMON_POLL_SECONDS)
                connection = self._try_connect()
        self._connection = connection
        return connection

    def _try_connect(self) -> socket.socket | None:
        try:
            endpoint = json.loads(self._endpoint().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as error:
            self._debug(f"Tray endpoint unreadable: {error}")
            return None
        try:
            connection = socket.create_connection(("127.0.0.1", int(endpoint["port"])), _CONNECT_TIMEOUT_SECONDS)
            connection.sendall((json.dumps({"type": "hello", "token": endpoint["token"]}) + "\n").encode("utf-8"))
        except (OSError, KeyError, ValueError) as error:
            self._debug(f"Tray daemon unreachable: {error}")
            return None
        return connection

    def _disconnect(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            connection.close()
        except OSError as error:
            self._debug(f"Tray link close failed: {error}")
