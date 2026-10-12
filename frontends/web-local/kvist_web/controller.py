"""Lightweight control runtime: metadata now, agent cores only on send."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .events import EventBroker
from .protocol import ConnectionState, ServerSpec
from .runtime.base import Runtime
from .runtime.local import LocalRuntime
from .runtime.remote import RemoteRuntime
from .security import SecretStore
from .ssh.transport import UnknownHostKey
from .storage import WebStateStore


class WebController:
    def __init__(
        self,
        initial_workspace: str,
        *,
        state: WebStateStore | None = None,
        secrets: SecretStore | None = None,
    ) -> None:
        self.state = state or WebStateStore()
        self.secrets = secrets or SecretStore()
        self.events = EventBroker()
        self._lock = threading.RLock()
        self._runtimes: dict[str, Runtime] = {}
        self._servers: dict[str, ServerSpec] = {}
        self._connections: dict[str, dict[str, Any]] = {}
        self._connect_locks: dict[str, threading.Lock] = {}
        self._retry_threads: dict[str, threading.Thread] = {}
        self._closed = threading.Event()
        stored = self.state.load()
        local_workspaces = list(stored["workspaces"].get("local", []))
        try:
            resolved = str(Path(initial_workspace).expanduser().resolve(strict=True))
            if resolved not in local_workspaces:
                local_workspaces.insert(0, resolved)
        except OSError:
            pass
        local = ServerSpec(id="local", name="local", kind="local", port=22)
        self._servers[local.id] = local
        self._runtimes[local.id] = LocalRuntime(
            local.id,
            local_workspaces,
            self.events,
            on_workspace_added=lambda path: self._remember_workspace("local", path),
        )
        self._connections["local"] = {"state": ConnectionState.CONNECTED, "latency_ms": 0}
        for value in stored["servers"]:
            try:
                spec = ServerSpec.from_dict(value)
            except (TypeError, ValueError):
                continue
            if spec.id == "local":
                continue
            self._servers[spec.id] = spec
            self._runtimes[spec.id] = self._make_remote_runtime(spec)
            self._connections[spec.id] = {"state": ConnectionState.OFFLINE, "latency_ms": None}
        self._persist()

    def restore_remote_connections(self) -> None:
        for server_id in list(self._servers):
            if server_id != "local":
                self._ensure_reconnect_loop(server_id)

    def bootstrap(self) -> dict[str, Any]:
        event_stream = {"cursor": self.events.cursor, "epoch": self.events.epoch}
        return {
            "protocol_version": 1,
            "stages": [
                "Loading configuration",
                "Restoring sessions",
                "Connecting servers",
                "Ready",
            ],
            "navigation": self.navigation(),
            # ``event_cursor`` stays for an already-open v1 page.  New clients
            # must retain the epoch as well: these ids are controller-local,
            # unlike the durable per-session history cursor.
            "event_cursor": event_stream["cursor"],
            "event_stream": event_stream,
        }

    def navigation(self) -> dict[str, Any]:
        result = []
        for server_id, spec in self._servers.items():
            runtime = self._runtimes[server_id]
            connection = dict(self._connections[server_id])
            workspaces = []
            paths = runtime.list_workspaces() if server_id == "local" else list(spec.workspaces)
            for path in paths:
                try:
                    sessions = runtime.sessions(path) if connection["state"] == ConnectionState.CONNECTED else None
                except Exception as error:
                    sessions = None
                    connection = {"state": ConnectionState.FAILED, "error": str(error)}
                    self._set_connection(server_id, ConnectionState.FAILED, {"error": str(error)})
                    if server_id != "local":
                        self._ensure_reconnect_loop(server_id)
                if sessions:
                    cursors = self.state.initialize_sessions(
                        server_id,
                        path,
                        [
                            (item["name"], item.get("last_event_id", 0))
                            for item in sessions["session_items"]
                        ],
                    )
                    for item in sessions["session_items"]:
                        cursor = cursors[item["name"]]
                        item["last_read_event_id"] = cursor
                        item["unread"] = item.get("last_event_id", 0) > cursor
                workspaces.append({"path": path, "sessions": sessions})
            result.append(
                {"server": spec.to_dict(), "connection": connection, "workspaces": workspaces}
            )
        return {"servers": result, "inbox": self.inbox(result)}

    def inbox(self, navigation: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        source = navigation if navigation is not None else self.navigation()["servers"]
        for server in source:
            for workspace in server["workspaces"]:
                sessions = workspace.get("sessions") or {}
                for session in sessions.get("session_items", []):
                    if session.get("unread") or session.get("execution") in {
                        "failed",
                        "waiting_for_user",
                    }:
                        entries.append(
                            {
                                "server_id": server["server"]["id"],
                                "server_name": server["server"]["name"],
                                "workspace": workspace["path"],
                                "session": session["name"],
                                "title": session["title"],
                                "state": session.get("execution", "idle"),
                                "last_event_id": session.get("last_event_id", 0),
                            }
                        )
        return entries

    def add_server(self, payload: dict[str, Any]) -> dict[str, Any]:
        spec = ServerSpec.from_dict(payload)
        if spec.id == "local":
            raise ValueError("The local server id is reserved.")
        password = payload.get("password")
        if isinstance(password, str) and password:
            self.secrets.set(
                f"server:{spec.id}:password",
                password,
                persist=payload.get("persist_secret", True) is True,
            )
        with self._lock:
            self._servers[spec.id] = spec
            self._runtimes[spec.id] = self._make_remote_runtime(spec)
            self._connections[spec.id] = {"state": ConnectionState.OFFLINE, "latency_ms": None}
            self._persist()
        return {"server": spec.to_dict(), "connection": self._connections[spec.id]}

    def connect_server(
        self, server_id: str, fingerprint: str | None = None, *,
        run_setup: bool = False, attempt_id: str = "",
    ) -> dict[str, Any]:
        from .connection_logs import ConnectionLogs

        if not isinstance(attempt_id, str) or len(attempt_id) > 128:
            raise ValueError("Invalid connection attempt id.")
        runtime = self._remote(server_id)
        logs = ConnectionLogs(self.events, server_id, attempt_id, password=self.secrets.get(f"server:{server_id}:password") or "")

        def finish(value):
            return logs.result(value) if run_setup else value

        with self._connect_lock(server_id):
            self._set_connection(server_id, ConnectionState.CONNECTING, {})
            started = __import__("time").monotonic()
            try:
                if run_setup:
                    result = runtime.connect(trust_fingerprint=fingerprint, run_setup=True, on_log=logs.append)
                else:
                    result = runtime.connect(trust_fingerprint=fingerprint)
            except UnknownHostKey as error:
                if run_setup:
                    logs.append("status", "Waiting for server fingerprint confirmation.")
                return finish(self._replace_connection(
                    server_id,
                    {
                        "state": ConnectionState.TRUST_REQUIRED,
                        "host": error.host,
                        "key_type": error.key_type,
                        "fingerprint": error.fingerprint,
                    },
                ))
            except Exception as error:
                detail = logs.clean(error)
                if run_setup:
                    logs.append("stderr", detail)
                return finish(self._replace_connection(
                    server_id,
                    {"state": ConnectionState.FAILED, "error": detail},
                ))
            latency = round((__import__("time").monotonic() - started) * 1000)
            self._replace_connection(
                server_id,
                {
                    "state": ConnectionState.CONNECTED,
                    "latency_ms": latency,
                    "bootstrap": result,
                },
            )
        # General preferences are safe to synchronize automatically. Provider
        # credentials stay absent until the person selects them explicitly.
        try:
            self.sync_config(server_id, [])
        except Exception:
            pass
        try:
            if run_setup:
                logs.append("status", "Restoring workspaces and sessions…")
            paths = runtime.list_workspaces()
        except Exception:
            paths = []
        self._servers[server_id].workspaces = paths
        self._persist()
        if run_setup:
            logs.append("status", "Connected.")
        return finish(dict(self._connections[server_id]))

    def add_workspace(self, server_id: str, path: str) -> dict[str, Any]:
        result = self.runtime(server_id).add_workspace(path)
        workspace = str(result["workspace"])
        if server_id != "local" and workspace not in self._servers[server_id].workspaces:
            self._servers[server_id].workspaces.append(workspace)
            self._persist()
        return result

    def sessions(self, server_id: str, workspace: str) -> dict[str, Any]:
        payload = self.runtime(server_id).sessions(workspace)
        cursors = self.state.initialize_sessions(
            server_id,
            workspace,
            [
                (item["name"], item.get("last_event_id", 0))
                for item in payload["session_items"]
            ],
        )
        for item in payload["session_items"]:
            cursor = cursors[item["name"]]
            item["last_read_event_id"] = cursor
            item["unread"] = item.get("last_event_id", 0) > cursor
        return payload

    def history(
        self, server_id: str, workspace: str, session: str, after: int, limit: int
    ) -> dict[str, Any]:
        return self.runtime(server_id).history(
            workspace, session, after=after, limit=limit
        )

    def create_session(
        self, server_id: str, workspace: str, name: str | None
    ) -> dict[str, Any]:
        result = self.runtime(server_id).create_session(workspace, name)
        active = result.get("active_session")
        if isinstance(active, str):
            self.state.initialize_session(server_id, workspace, active, 0)
        return result

    def send(self, server_id: str, workspace: str, session: str, text: str):
        return self.runtime(server_id).send(workspace, session, text)

    def configuration(self, server_id: str, operation: str = "info", payload: dict | None = None):
        return self.runtime(server_id).configuration(operation, payload)

    def command(self, server_id: str, workspace: str, session: str, name: str, args: list[str]):
        return self.runtime(server_id).command(workspace, session, name, args)

    def stop(self, server_id: str, workspace: str, session: str):
        return self.runtime(server_id).stop(workspace, session)

    def control(self, server_id: str, workspace: str, session: str, kind: str, agent_id: str = ""):
        if kind not in ("pause", "helper_stop"):
            raise ValueError("Unknown control message.")
        return self.runtime(server_id).control(workspace, session, kind, agent_id)

    def respond(
        self,
        server_id: str,
        workspace: str,
        session: str,
        response: dict[str, Any],
    ):
        return self.runtime(server_id).respond(workspace, session, response)

    def mark_read(self, server_id: str, workspace: str, session: str, seq: int) -> None:
        self.state.mark_read(server_id, workspace, session, seq)

    def changes(self, server_id: str, workspace: str, session: str | None = None) -> dict[str, Any]:
        return self.runtime(server_id).changes(workspace, session)

    def config_sync_preview(self) -> dict[str, Any]:
        from .config_sync import sync_preview

        return sync_preview()

    def sync_config(self, server_id: str, selected: list[str]) -> dict[str, Any]:
        from .config_sync import build_sync_payload

        return self._remote(server_id).sync_config(build_sync_payload(selected))

    def runtime(self, server_id: str) -> Runtime:
        try:
            return self._runtimes[server_id]
        except KeyError as error:
            raise ValueError("Unknown server.") from error

    def close(self) -> None:
        self._closed.set()
        for runtime in self._runtimes.values():
            if isinstance(runtime, LocalRuntime):
                runtime.supervisor.close()
            if isinstance(runtime, RemoteRuntime):
                runtime.transport.close()
                runtime._event_transport.close()

    def _remote(self, server_id: str) -> RemoteRuntime:
        runtime = self.runtime(server_id)
        if not isinstance(runtime, RemoteRuntime):
            raise ValueError("That server is not remote.")
        return runtime

    def _ensure_reconnect_loop(self, server_id: str) -> None:
        """Start one bounded-backoff reconnect loop for an offline remote.

        A remote bridge keeps agent processes independently of this controller,
        so losing the first SSH attempt must not make a saved server permanently
        inert until the user recreates it.
        """
        with self._lock:
            existing = self._retry_threads.get(server_id)
            if existing is not None and existing.is_alive():
                return
            thread = threading.Thread(
                target=self._background_reconnect, args=(server_id,), daemon=True
            )
            self._retry_threads[server_id] = thread
            thread.start()

    def _background_reconnect(self, server_id: str) -> None:
        delay = 0.5
        while not self._closed.is_set():
            result = self.connect_server(server_id)
            self.events.publish(
                server_id,
                "",
                "",
                {"type": "connection.state_changed", **result},
            )
            state = result.get("state")
            if state in {ConnectionState.CONNECTED, ConnectionState.TRUST_REQUIRED}:
                return
            message = str(result.get("error", ""))
            # Retrying a rejected credential cannot repair it, while an offline
            # host is expected to return later and deserves a bounded backoff.
            if "authentication failed" in message.casefold() or "password is not available" in message.casefold():
                return
            reconnecting = self._set_connection(
                server_id,
                ConnectionState.RECONNECTING,
                {"error": message},
            )
            self.events.publish(
                server_id,
                "",
                "",
                {"type": "connection.state_changed", **reconnecting},
            )
            self._closed.wait(delay)
            delay = min(delay * 2, 30.0)

    def _make_remote_runtime(self, spec: ServerSpec) -> RemoteRuntime:
        return RemoteRuntime(
            spec,
            self.secrets,
            self.events,
            on_connection=lambda state, details, server_id=spec.id: self._set_connection(
                server_id, state, details
            ),
        )

    def _set_connection(
        self, server_id: str, state: str, details: dict[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            current = dict(self._connections.get(server_id, {}))
            current.update(details)
            current["state"] = state
            self._connections[server_id] = current
            return dict(current)

    def _replace_connection(self, server_id: str, value: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._connections[server_id] = dict(value)
            return dict(value)

    def _connect_lock(self, server_id: str) -> threading.Lock:
        with self._lock:
            return self._connect_locks.setdefault(server_id, threading.Lock())

    def _remember_workspace(self, server_id: str, path: str) -> None:
        def update(state: dict[str, Any]) -> None:
            values = state["workspaces"].setdefault(server_id, [])
            if path not in values:
                values.append(path)

        self.state.update(update)

    def _persist(self) -> None:
        remote = [
            spec.to_dict() for key, spec in self._servers.items() if key != "local"
        ]
        local = self._runtimes.get("local")
        local_workspaces = local.list_workspaces() if local else []

        def update(state: dict[str, Any]) -> None:
            state["servers"] = remote
            state["workspaces"]["local"] = local_workspaces

        self.state.update(update)
