# -*- coding: utf-8 -*-
"""Framing of the core <-> skill runner protocol (plugin API v1).

Each message is a 4-byte big-endian length followed by that many bytes of UTF-8
JSON holding one object with a ``kind``. The channel is a dedicated socket
inherited by the runner; stdout and stderr stay free for diagnostics, so
nothing a skill prints can be mistaken for a protocol message.

Binary data never travels here: files move through the exchange directory and
are named by path or attachment id. This module is stdlib-only because the
runner imports it inside the sandbox without access to the rest of Kvist.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
from typing import Any

PLUGIN_API_VERSION = 1
MAX_FRAME_BYTES = 4 * 1024 * 1024
_HEADER = struct.Struct(">I")

# Messages the core sends and the runner sends; anything else is a protocol error.
CORE_KINDS = frozenset({"describe", "execute", "control", "response", "shutdown"})
RUNNER_KINDS = frozenset({"hello", "describe_result", "result", "request", "event", "fatal"})

# Final statuses of one call, shared with the core's result handling.
STATUSES = frozenset({"success", "error", "cancelled", "paused", "denied", "timeout", "partial"})


class ProtocolError(RuntimeError):
    """The peer sent something that is not a valid message; the channel is closed."""


class Channel:
    """One end of the protocol socket; ``send`` is safe from several threads."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._send_lock = threading.Lock()
        self._buffer = bytearray()

    def send(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_FRAME_BYTES:
            raise ProtocolError(f"message of {len(payload)} bytes exceeds the {MAX_FRAME_BYTES}-byte frame limit")
        with self._send_lock:
            self._sock.sendall(_HEADER.pack(len(payload)) + payload)

    def receive(self, timeout: float | None = None) -> dict[str, Any] | None:
        """The next message; None when ``timeout`` passes before a whole frame arrived.

        The timeout bounds the whole frame, header and body: a peer that stops
        mid-frame cannot hold the caller past it. Bytes of an unfinished frame
        stay buffered and the next call continues that frame. EOFError when the
        peer closed the channel.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        if not self._fill(_HEADER.size, deadline):
            return None
        (size,) = _HEADER.unpack_from(self._buffer)
        if size > MAX_FRAME_BYTES:
            raise ProtocolError(f"peer announced a {size}-byte frame (limit {MAX_FRAME_BYTES})")
        end = _HEADER.size + size
        if not self._fill(end, deadline):
            return None
        body = bytes(self._buffer[_HEADER.size:end])
        del self._buffer[:end]
        try:
            message = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProtocolError(f"frame is not UTF-8 JSON: {error}") from None
        if not isinstance(message, dict) or not isinstance(message.get("kind"), str):
            raise ProtocolError("frame is not an object with a kind")
        return message

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            # Already shut down by the peer; closing below is what matters.
            self._sock.close()
            return
        self._sock.close()

    def _fill(self, size: int, deadline: float | None) -> bool:
        """Buffer at least ``size`` bytes; False when ``deadline`` passes first."""
        while len(self._buffer) < size:
            if deadline is None:
                self._sock.settimeout(None)
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(max(65536, size - len(self._buffer)))
            except socket.timeout:
                return False
            if not chunk:
                raise EOFError("protocol channel closed")
            self._buffer.extend(chunk)
        return True


def error_payload(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message[:2000]}
