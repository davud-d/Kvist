"""Typed messages shared by the controller, runtimes, bridge and browser API."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


WEB_PROTOCOL_VERSION = 1
BRIDGE_PROTOCOL_VERSION = 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ConnectionState(StrEnum):
    OFFLINE = "offline"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    TRUST_REQUIRED = "trust_required"
    FAILED = "failed"


class ExecutionState(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    WAITING = "waiting_for_user"
    FINISHED = "finished"
    FAILED = "failed"
    STOPPED = "stopped"


class AuthMethod(StrEnum):
    CONFIG = "ssh_config"
    AGENT = "agent"
    KEY = "key"
    PASSWORD = "password"


@dataclass(slots=True)
class ServerSpec:
    id: str
    name: str
    kind: str = "remote"
    host: str = ""
    port: int = 22
    username: str = ""
    ssh_alias: str = ""
    auth_method: str = AuthMethod.CONFIG
    private_key_path: str = ""
    workspaces: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not _IDENTIFIER.fullmatch(self.id):
            raise ValueError("Server id must use letters, numbers, '.', '_' or '-'.")
        if self.kind not in {"local", "remote"}:
            raise ValueError("Server kind must be local or remote.")
        if not self.name.strip():
            raise ValueError("Server name is required.")
        if self.kind == "remote" and not (self.ssh_alias or self.host):
            raise ValueError("Remote server needs an SSH alias or hostname.")
        if not 1 <= int(self.port) <= 65535:
            raise ValueError("SSH port must be between 1 and 65535.")
        if self.auth_method not in {item.value for item in AuthMethod}:
            raise ValueError("Unsupported SSH authentication method.")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ServerSpec":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        spec = cls(**{key: item for key, item in value.items() if key in allowed})
        spec.port = int(spec.port)
        spec.workspaces = [str(path) for path in spec.workspaces]
        spec.validate()
        return spec

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProcessSnapshot:
    server_id: str
    workspace: str
    session: str
    state: str = ExecutionState.IDLE
    pid: int | None = None
    last_event_type: str = ""
    pending_input: dict[str, Any] | None = None
    error: str = ""
    started_at: float | None = None
    updated_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LiveEnvelope:
    id: int
    server_id: str
    workspace: str
    session: str
    event: dict[str, Any]
    ts: float
    durable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BridgeRequest:
    id: str
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    protocol_version: int = BRIDGE_PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "BridgeRequest":
        if value.get("protocol_version") != BRIDGE_PROTOCOL_VERSION:
            raise ValueError("Incompatible bridge protocol.")
        request_id = value.get("id")
        method = value.get("method")
        params = value.get("params", {})
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("Bridge request id is required.")
        if not isinstance(method, str) or not _IDENTIFIER.fullmatch(method):
            raise ValueError("Invalid bridge method.")
        if not isinstance(params, dict):
            raise ValueError("Bridge params must be an object.")
        return cls(id=request_id, method=method, params=params)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BridgeResponse:
    id: str
    ok: bool
    result: dict[str, Any] | list[Any] | None = None
    error: str = ""
    protocol_version: int = BRIDGE_PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

