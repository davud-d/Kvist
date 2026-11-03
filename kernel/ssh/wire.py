# -*- coding: utf-8 -*-
"""Lines exchanged between the local proxy, the attach shim and the daemon (UI_API.md section 8g).

Everything is one JSON object per line. The ``remote_*`` types never reach a frontend: the proxy
consumes them. ``control`` and ``remote_state`` are the two additive events a frontend does see.
"""
from __future__ import annotations

import json
import re

HELLO = "remote_hello"        # proxy -> daemon, first line: {op, client, from?, segment?, lang?}
ATTACHED = "remote_attached"  # daemon -> proxy: {ok, segment, reason?}
OFFSET = "remote_offset"      # daemon -> proxy: every event line before it has been forwarded
PING = "remote_ping"          # proxy -> daemon, every PING_SECONDS
TAKEOVER = "takeover"         # frontend -> daemon
CONTROL = "control"           # daemon -> frontend: {role, notice}
STATE = "remote_state"        # proxy -> frontend, and shim -> proxy when it finds no daemon

PING_SECONDS = 5.0
LEASE_SECONDS = 20.0

# Exit codes of the attach shim and of the proxy.
EXIT_NO_SERVICE = 3
EXIT_GAVE_UP = 75

_TYPE = re.compile(rb'^\{"type": ?"([A-Za-z_]+)"')


def encode(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def event_type(line: bytes) -> str:
    """The ``type`` of an event line; the core writes it first, so a regex is enough almost always."""
    match = _TYPE.match(line)
    if match:
        return match.group(1).decode("ascii")
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return ""
    return data.get("type", "") if isinstance(data, dict) else ""


def decode(line: bytes) -> dict | None:
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None
