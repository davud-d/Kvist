"""Local socket client used by the bridge's SSH stdio proxy."""
from __future__ import annotations

import json
import socket
from typing import Any

from ..protocol import BridgeRequest
from .service import read_descriptor


def call_local_bridge(payload: dict[str, Any], *, timeout: float = 35.0) -> dict[str, Any]:
    descriptor = _safe_descriptor(read_descriptor())
    request = BridgeRequest.from_dict(payload)
    return _call_descriptor(descriptor, request.to_dict(), timeout=timeout)


def probe_local_bridge(
    descriptor: dict[str, Any], *, timeout: float = 1.0
) -> dict[str, Any]:
    """Probe a descriptor using the protocol it advertises.

    This deliberately bypasses :class:`BridgeRequest`: an old bridge may use
    a different protocol number, but it can still own a detached core process.
    A successful wire response is enough to make replacement unsafe; callers
    must negotiate before sending any current-protocol management request.
    """
    safe_descriptor = _safe_descriptor(descriptor)
    protocol = safe_descriptor.get("protocol_version")
    if type(protocol) is not int or protocol < 0:
        raise RuntimeError("Remote bridge descriptor has an invalid protocol version.")
    return _call_descriptor(
        safe_descriptor,
        {
            "id": "bridge-probe",
            "method": "ping",
            "params": {},
            "protocol_version": protocol,
        },
        timeout=timeout,
    )


def _call_descriptor(
    descriptor: dict[str, Any], payload: dict[str, Any], *, timeout: float
) -> dict[str, Any]:
    encoded = dict(payload)
    encoded["auth"] = descriptor["token"]
    with socket.create_connection(
        (str(descriptor.get("host", "127.0.0.1")), int(descriptor["port"])),
        timeout=timeout,
    ) as connection:
        connection.sendall(
            (json.dumps(encoded, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
                "utf-8"
            )
        )
        reader = connection.makefile("rb")
        line = reader.readline(30 * 1024 * 1024)
    value = json.loads(line)
    if not isinstance(value, dict):
        raise RuntimeError("Remote bridge returned an invalid response.")
    return value


def bridge_status(*, timeout: float = 3.0) -> dict[str, Any]:
    """Return an authoritative bridge snapshot, not just a stale descriptor."""
    ping = _result(
        call_local_bridge(
            {
                "id": "bridge-status",
                "method": "ping",
                "params": {},
                "protocol_version": 1,
            },
            timeout=timeout,
        )
    )
    processes = _result(
        call_local_bridge(
            {
                "id": "bridge-processes",
                "method": "processes.list",
                "params": {},
                "protocol_version": 1,
            },
            timeout=timeout,
        )
    ).get("processes")
    if not isinstance(processes, list):
        raise RuntimeError("Remote bridge returned an invalid process snapshot.")
    return {**ping, "processes": processes}


def request_idle_shutdown(*, timeout: float = 5.0) -> None:
    """Ask a compatible idle bridge to exit; it refuses if work starts first."""
    result = _result(
        call_local_bridge(
            {
                "id": "bridge-idle-shutdown",
                "method": "bridge.shutdown",
                "params": {},
                "protocol_version": 1,
            },
            timeout=timeout,
        )
    )
    if result.get("stopping") is not True:
        raise RuntimeError("Remote bridge did not accept its idle shutdown request.")


def is_bridge_alive() -> bool:
    try:
        response = probe_local_bridge(read_descriptor(), timeout=1.0)
    except Exception:
        return False
    # An error response still proves that an authenticated bridge endpoint is
    # listening at the descriptor. Treat it as alive so lifecycle code never
    # overwrites a descriptor whose owner may still have running work.
    return isinstance(response, dict)


def _safe_descriptor(value: dict[str, Any]) -> dict[str, Any]:
    host = value.get("host")
    port = value.get("port")
    token = value.get("token")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Remote bridge descriptor does not use loopback.")
    if type(port) is not int or not 1 <= port <= 65535:
        raise RuntimeError("Remote bridge descriptor has an invalid port.")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Remote bridge descriptor has no authentication token.")
    return value


def _result(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok") is not True or not isinstance(response.get("result"), dict):
        raise RuntimeError(str(response.get("error") or "Remote bridge request failed."))
    return dict(response["result"])
