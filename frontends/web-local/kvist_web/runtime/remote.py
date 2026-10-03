"""SSH-backed runtime; the remote bridge owns every agent process."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Callable

from ..events import EventBroker
from ..protocol import BRIDGE_PROTOCOL_VERSION, ServerSpec
from ..security import SecretStore
from ..ssh.bootstrap import RemoteBootstrap, quote_remote_arg
from ..ssh.transport import SSHTransport
from .base import Runtime


_LOG = logging.getLogger("kvist.web.runtime.remote")


class RemoteRuntime(Runtime):
    def __init__(
        self,
        spec: ServerSpec,
        secrets: SecretStore,
        broker: EventBroker,
        on_connection: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.spec = spec
        self.transport = SSHTransport(spec, secrets)
        self._event_transport = SSHTransport(spec, secrets)
        self.broker = broker
        self._on_connection = on_connection
        self._ready = False
        self._lock = threading.RLock()
        self._live_cursor = 0
        self._live_epoch = ""
        self._poller: threading.Thread | None = None
        self._remote_python = "python3"
        self._remote_os = "linux"

    def connect(self, *, trust_fingerprint: str | None = None) -> dict[str, Any]:
        with self._lock:
            self.transport.connect(trust_fingerprint=trust_fingerprint)
            bootstrap = RemoteBootstrap(self.transport).run()
            self._remote_python = bootstrap.python
            self._remote_os = bootstrap.os
            self._ready = True
            if self._poller is None or not self._poller.is_alive():
                self._poller = threading.Thread(target=self._poll_live, daemon=True)
                self._poller.start()
            return bootstrap.to_dict()

    def list_workspaces(self) -> list[str]:
        return list(self._rpc("workspaces.list")["workspaces"])

    def add_workspace(self, path: str) -> dict[str, Any]:
        return self._rpc("workspaces.add", path=path)

    def sessions(self, workspace: str) -> dict[str, Any]:
        return self._rpc("sessions.list", workspace=workspace)

    def history(
        self, workspace: str, session: str, *, after: int = 0, limit: int = 1_000
    ) -> dict[str, Any]:
        return self._rpc(
            "sessions.history",
            workspace=workspace,
            session=session,
            after=after,
            limit=limit,
        )

    def create_session(self, workspace: str, name: str | None = None) -> dict[str, Any]:
        return self._rpc("sessions.create", workspace=workspace, name=name or "")

    def send(self, workspace: str, session: str, text: str) -> dict[str, Any]:
        return self._rpc(
            "sessions.send", workspace=workspace, session=session, text=text
        )

    def stop(self, workspace: str, session: str) -> dict[str, Any]:
        return self._rpc("sessions.stop", workspace=workspace, session=session)

    def respond(
        self, workspace: str, session: str, response: dict[str, Any]
    ) -> dict[str, Any]:
        return self._rpc(
            "sessions.respond",
            workspace=workspace,
            session=session,
            response=response,
        )

    def processes(self) -> list[dict[str, Any]]:
        return list(self._rpc("processes.list")["processes"])

    def changes(self, workspace: str, session: str | None = None) -> dict[str, Any]:
        return self._rpc("workspace.changes", workspace=workspace, session=session or "")

    def sync_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._rpc("config.sync", payload=payload)

    def _rpc(self, method: str, *, _transport=None, **params) -> dict[str, Any]:
        if not self._ready:
            self.connect()
        request = {
            "id": uuid.uuid4().hex,
            "method": method,
            "params": params,
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
        }
        delay = 0.25
        last_error: Exception | None = None
        transport = _transport or self.transport
        for attempt in range(5):
            try:
                output = transport.execute(
                    f"{quote_remote_arg(self._remote_python, self._remote_os)} -m kvist_web.bridge_cli rpc",
                    stdin=json.dumps(request, ensure_ascii=False, separators=(",", ":")),
                    timeout=40,
                )
                response = json.loads(output.strip().splitlines()[-1])
                if not isinstance(response, dict) or response.get("id") != request["id"]:
                    raise RuntimeError("Remote bridge returned an unrelated response.")
                if response.get("ok") is not True:
                    raise RuntimeError(str(response.get("error") or "Remote operation failed."))
                result = response.get("result")
                return result if isinstance(result, dict) else {"value": result}
            except Exception as error:
                last_error = error
                transport.close()
                if attempt == 4:
                    break
                time.sleep(delay)
                delay = min(delay * 2, 4.0)
        raise RuntimeError(f"Remote connection to {self.spec.name} was lost: {last_error}")

    def _poll_live(self) -> None:
        connection_lost = False
        while self._ready:
            try:
                result = self._rpc(
                    "events.live",
                    _transport=self._event_transport,
                    after=self._live_cursor,
                    epoch=self._live_epoch,
                    timeout=20,
                )
                if result.get("reset") is True:
                    self._live_cursor = max(0, int(result.get("cursor", 0)))
                    self._live_epoch = str(result.get("epoch") or "")
                    self.broker.publish(
                        self.spec.id,
                        "",
                        "",
                        {
                            "type": "stream.reset",
                            "reason": str(result.get("reason") or "remote_live_reset"),
                            "remote": True,
                        },
                    )
                    # The browser uses the durable history cursor to recover
                    # significant events; the remote broker only carries live
                    # telemetry and may legitimately have restarted.
                    continue
                if result.get("epoch"):
                    self._live_epoch = str(result["epoch"])
                events = result.get("events", [])
                for envelope in events:
                    self._live_cursor = max(self._live_cursor, int(envelope.get("id", 0)))
                    self.broker.publish(
                        self.spec.id,
                        str(envelope.get("workspace", "")),
                        str(envelope.get("session", "")),
                        dict(envelope.get("event") or {}),
                        durable=envelope.get("durable") is True,
                    )
                if connection_lost:
                    connection_lost = False
                    details = {"recovered_events": len(events)}
                    self._notify_connection("connected", details)
                    self.broker.publish(
                        self.spec.id,
                        "",
                        "",
                        {"type": "connection.restored", **details},
                    )
                    _LOG.info(
                        "remote connection restored",
                        extra={
                            "component": "runtime",
                            "server": self.spec.id,
                            "connection": "connected",
                            "event": f"recovered:{len(events)}",
                        },
                    )
            except Exception as error:
                if not connection_lost:
                    connection_lost = True
                    details = {"message": str(error)}
                    self._notify_connection("reconnecting", details)
                    self.broker.publish(
                        self.spec.id,
                        "",
                        "",
                        {"type": "connection.lost", **details},
                    )
                    _LOG.warning(
                        "remote connection lost",
                        extra={
                            "component": "runtime",
                            "server": self.spec.id,
                            "connection": "reconnecting",
                        },
                    )
                time.sleep(2.0)

    def _notify_connection(self, state: str, details: dict[str, Any]) -> None:
        if self._on_connection is not None:
            self._on_connection(state, details)
