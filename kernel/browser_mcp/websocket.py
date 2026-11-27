# -*- coding: utf-8 -*-
"""A minimal RFC 6455 client: text messages over plain ws:// to a local debugging port."""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import threading
from urllib.parse import urlsplit

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 64 * 1024 * 1024


class WebSocketError(Exception):
    """The connection failed or the peer broke the protocol."""


def encode_frame(opcode: int, payload: bytes, mask: bytes) -> bytes:
    """One final client frame; clients must mask everything they send."""
    header = bytes([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header += bytes([0x80 | length])
    elif length < 1 << 16:
        header += bytes([0x80 | 126]) + struct.pack("!H", length)
    else:
        header += bytes([0x80 | 127]) + struct.pack("!Q", length)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return header + mask + masked


class WebSocket:
    def __init__(self, url: str, timeout: float = 5.0) -> None:
        parts = urlsplit(url)
        if parts.scheme != "ws" or not parts.hostname:
            raise WebSocketError(f"only ws:// addresses are supported, got {url!r}")
        port = parts.port or 80
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        try:
            self._sock = socket.create_connection((parts.hostname, port), timeout=timeout)
        except OSError as error:
            raise WebSocketError(f"cannot connect to {parts.hostname}:{port}: {error}") from None
        self._send_lock = threading.Lock()
        self._buffer = b""
        self._handshake(parts.hostname, port, path)
        self._sock.settimeout(None)

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        # No Origin header: browsers refuse debugging connections that carry a web origin.
        request = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self._sock.sendall(request.encode("ascii"))
        while b"\r\n\r\n" not in self._buffer:
            chunk = self._recv_some()
            if len(self._buffer) > 65536:
                raise WebSocketError("handshake reply too long")
            self._buffer += chunk
        head, self._buffer = self._buffer.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            raise WebSocketError(f"the server refused the WebSocket upgrade: {lines[0]}")
        headers = {name.strip().lower(): value.strip() for name, _, value in (line.partition(":") for line in lines[1:])}
        expected = base64.b64encode(hashlib.sha1((key + _GUID).encode("ascii")).digest()).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise WebSocketError("the server answered the upgrade with a wrong accept key")

    def _recv_some(self) -> bytes:
        try:
            chunk = self._sock.recv(65536)
        except OSError as error:
            raise WebSocketError(f"connection lost: {error}") from None
        if not chunk:
            raise WebSocketError("connection closed by the browser")
        return chunk

    def _read_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            self._buffer += self._recv_some()
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._read_exact(2)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", self._read_exact(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._read_exact(8))
        if length > MAX_MESSAGE:
            raise WebSocketError("message too large")
        mask = self._read_exact(4) if second & 0x80 else b""
        payload = self._read_exact(length)
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return bool(first & 0x80), first & 0x0F, payload

    def send(self, text: str) -> None:
        frame = encode_frame(0x1, text.encode("utf-8"), os.urandom(4))
        with self._send_lock:
            try:
                self._sock.sendall(frame)
            except OSError as error:
                raise WebSocketError(f"connection lost: {error}") from None

    def recv(self) -> str:
        """The next text message; raises WebSocketError when the connection ends."""
        parts: list[bytes] = []
        while True:
            final, opcode, payload = self._read_frame()
            if opcode == 0x8:
                raise WebSocketError("connection closed by the browser")
            if opcode == 0x9:
                with self._send_lock:
                    self._sock.sendall(encode_frame(0xA, payload, os.urandom(4)))
                continue
            if opcode == 0xA:
                continue
            parts.append(payload)
            if sum(len(part) for part in parts) > MAX_MESSAGE:
                raise WebSocketError("message too large")
            if final:
                return b"".join(parts).decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            with self._send_lock:
                self._sock.sendall(encode_frame(0x8, struct.pack("!H", 1000), os.urandom(4)))
        except OSError:
            pass  # already gone; closing the socket below is all that is left
        self._sock.close()
