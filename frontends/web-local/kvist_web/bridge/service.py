"""Detached remote supervisor exposed only on remote loopback."""
from __future__ import annotations

import json
import logging
import os
import secrets
import socketserver
import threading
import time
from pathlib import Path
from typing import Any

from kernel.version import kvist_version

from ..events import EventBroker
from ..protocol import BRIDGE_PROTOCOL_VERSION, BridgeRequest, BridgeResponse
from ..runtime.local import LocalRuntime
from ..security import redact


_LOG = logging.getLogger("kvist.web.bridge")


def bridge_home() -> Path:
    return Path.home() / ".kvist" / "web-bridge"


def descriptor_path() -> Path:
    return bridge_home() / "bridge.json"


def _write_private(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    if os.name != "nt":
        temporary.chmod(0o600)
    temporary.replace(path)


def read_descriptor() -> dict[str, Any]:
    try:
        value = json.loads(descriptor_path().read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("Remote bridge is not running.") from error
    if not isinstance(value, dict):
        raise RuntimeError("Remote bridge descriptor is invalid.")
    return value


class BridgeApplication:
    def __init__(self, workspaces_path: Path | None = None) -> None:
        self.broker = EventBroker(capacity=8_000)
        self._workspaces_path = workspaces_path or bridge_home() / "workspaces.json"
        self._workspace_lock = threading.Lock()
        # `bridge.shutdown` takes this gate before it decides that the bridge
        # is idle. A send that won the gate is allowed to finish starting its
        # process; a send that loses it is rejected, so an upgrade can never
        # stop a task that began in the check-to-shutdown window.
        self._lifecycle_lock = threading.RLock()
        self._accepting_work = True
        self.runtime = LocalRuntime(
            "remote",
            self._load_workspaces(),
            self.broker,
            on_workspace_added=self._remember_workspace,
        )

    def dispatch(self, request: BridgeRequest) -> BridgeResponse:
        try:
            result = self._call(request.method, request.params)
        except Exception as error:
            return BridgeResponse(request.id, False, error=redact(error))
        return BridgeResponse(request.id, True, result=result)

    def _call(self, method: str, params: dict[str, Any]):
        if method == "ping":
            return {
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "kvist_version": kvist_version(),
                "time": time.time(),
            }
        if method == "workspaces.list":
            return {"workspaces": self.runtime.list_workspaces()}
        if method == "workspaces.add":
            return self.runtime.add_workspace(_text(params, "path"))
        if method == "processes.list":
            return {"processes": self.runtime.processes()}
        if method == "bridge.shutdown":
            with self._lifecycle_lock:
                active = _active_processes(self.runtime.processes())
                if active:
                    raise RuntimeError(
                        "Remote bridge owns active tasks and will not stop them."
                    )
                self._accepting_work = False
                return {"stopping": True}
        if method == "events.live":
            cursor = max(0, int(params.get("after", 0)))
            timeout = min(25.0, max(0.0, float(params.get("timeout", 0))))
            epoch = params.get("epoch")
            if epoch is not None and not isinstance(epoch, str):
                raise ValueError("events.live epoch must be text")
            replay = self.broker.read_after(cursor, epoch=epoch or None, timeout=timeout)
            return {
                "events": [event.to_dict() for event in replay.events],
                "cursor": replay.cursor,
                "epoch": replay.epoch,
                "reset": replay.reset,
                "reason": replay.reason,
            }
        if method == "config.sync":
            from ..config_sync import apply_sync_payload

            payload = params.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("Configuration sync payload is required.")
            return apply_sync_payload(payload)
        workspace = _text(params, "workspace")
        if method == "sessions.list":
            return self.runtime.sessions(workspace)
        if method == "sessions.create":
            name = params.get("name")
            return self.runtime.create_session(
                workspace, str(name).strip() if isinstance(name, str) and name.strip() else None
            )
        session = _text(params, "session")
        if method == "sessions.history":
            return self.runtime.history(
                workspace,
                session,
                after=max(0, int(params.get("after", 0))),
                limit=min(5_000, max(1, int(params.get("limit", 1_000)))),
            )
        if method == "sessions.send":
            with self._lifecycle_lock:
                if not self._accepting_work:
                    raise RuntimeError("Remote bridge is restarting; retry after it is ready.")
                return self.runtime.send(workspace, session, _text(params, "text"))
        if method == "sessions.stop":
            return self.runtime.stop(workspace, session)
        if method == "sessions.respond":
            response = params.get("response")
            if not isinstance(response, dict):
                raise ValueError("Response payload is required.")
            return self.runtime.respond(workspace, session, response)
        if method == "workspace.changes":
            session_value = params.get("session")
            session = session_value if isinstance(session_value, str) and session_value else None
            return self.runtime.changes(workspace, session)
        raise ValueError(f"Unknown bridge method: {method}")

    def _load_workspaces(self) -> list[str]:
        try:
            value = json.loads(self._workspaces_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return []
        return [str(item) for item in value] if isinstance(value, list) else []

    def _remember_workspace(self, workspace: str) -> None:
        with self._workspace_lock:
            values = self._load_workspaces()
            if workspace not in values:
                values.append(workspace)
            _write_private(self._workspaces_path, values)


def _text(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required.")
    return value.strip()


_TERMINAL_PROCESS_STATES = frozenset({"idle", "finished", "failed", "stopped"})


def _active_processes(processes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Treat unknown states as active: safety beats an opportunistic restart."""
    return [
        process
        for process in processes
        if str(process.get("state", "")) not in _TERMINAL_PROCESS_STATES
    ]


class _BridgeHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server: BridgeServer = self.server  # type: ignore[assignment]
        line = self.rfile.readline(2 * 1024 * 1024)
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict) or not secrets.compare_digest(
                str(payload.pop("auth", "")), server.auth_token
            ):
                raise PermissionError("Bridge authentication failed.")
            response = server.dispatch(BridgeRequest.from_dict(payload))
        except Exception as error:
            response = BridgeResponse("unknown", False, error=redact(error))
        self.wfile.write(
            (json.dumps(response.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n").encode(
                "utf-8"
            )
        )


class BridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, application: BridgeApplication, auth_token: str) -> None:
        self.application = application
        self.auth_token = auth_token
        super().__init__(("127.0.0.1", 0), _BridgeHandler)

    def dispatch(self, request: BridgeRequest) -> BridgeResponse:
        response = self.application.dispatch(request)
        if (
            request.method == "bridge.shutdown"
            and response.ok
            and isinstance(response.result, dict)
            and response.result.get("stopping") is True
        ):
            # This handler must be allowed to write its acknowledgement before
            # `serve_forever` leaves. The application has already closed the
            # lifecycle gate, so no task can start during the short delay.
            timer = threading.Timer(0.05, self.shutdown)
            timer.daemon = True
            timer.start()
        return response


def serve() -> None:
    from ..logging_setup import configure_logging

    configure_logging(bridge_home() / "bridge.jsonl")
    token = secrets.token_urlsafe(40)
    application = BridgeApplication()
    server = BridgeServer(application, token)
    _write_private(
        descriptor_path(),
        {
            "pid": os.getpid(),
            "host": "127.0.0.1",
            "port": server.server_address[1],
            "token": token,
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "kvist_version": kvist_version(),
            "started_at": time.time(),
        },
    )
    _LOG.info(
        "bridge started",
        extra={"component": "bridge", "connection": "127.0.0.1"},
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        application.runtime.supervisor.close()
        _clear_descriptor_if_owner(os.getpid())
        _LOG.info("bridge stopped", extra={"component": "bridge"})


def _clear_descriptor_if_owner(pid: int) -> None:
    try:
        value = read_descriptor()
    except RuntimeError:
        return
    if value.get("pid") != pid:
        return
    try:
        descriptor_path().unlink(missing_ok=True)
    except OSError:
        pass
