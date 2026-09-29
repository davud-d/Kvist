# -*- coding: utf-8 -*-
"""ACP stdio under the descriptors a real client captures.

``tests/test_acp_frontend.py`` drives ``run_agent`` over a socket pair, and a
socket is exactly what asyncio's write-pipe transport accepts. Zed instead
captures the agent's stdout as a plain regular file, where the SDK's own
``_posix_stdio_streams`` raises ``ValueError`` from ``connect_write_pipe`` -- the
process dies before ``initialize`` is answered and the client reports a
never-received handshake. These tests hold the descriptors open the way that
client does.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys

import pytest

from acp_frontend import run_acp
from acp_stdio import stdio_streams
from kernel.tests.conftest import requires_posix

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": 1,
        "clientCapabilities": {
            "fs": {"readTextFile": False, "writeTextFile": False},
            "terminal": False,
        },
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}


def _never_built(*_args, **_kwargs):
    """``initialize`` is served without an agent; reaching one is a regression."""
    raise AssertionError("the handshake must not build an agent")


def _stdin_holding(payload: bytes):
    """A text stdin whose binary side yields ``payload`` and then EOF."""
    return io.TextIOWrapper(io.BytesIO(payload))


def _capture_stdout(monkeypatch, tmp_path):
    """Redirect stdout to a regular file and return a reader for it."""
    path = tmp_path / "stdout.log"
    handle = open(path, "wb")
    monkeypatch.setattr(sys, "stdout", handle)
    return path, handle


def test_initialize_is_answered_when_the_client_captures_stdout_as_a_file(monkeypatch, tmp_path):
    """The handshake lands in the client's capture file, not in a pipe."""
    monkeypatch.setattr(sys, "stdin", _stdin_holding((json.dumps(INITIALIZE) + "\n").encode()))
    path, handle = _capture_stdout(monkeypatch, tmp_path)

    assert run_acp(build_agent_fn=_never_built) == 0

    handle.close()
    frames = path.read_bytes().decode("utf-8")
    reply = json.loads(frames)
    assert reply["id"] == INITIALIZE["id"]
    assert "result" in reply, reply
    assert reply["result"]["agentInfo"]["name"] == "kvist"


def test_stdout_carries_protocol_frames_and_nothing_else(monkeypatch, tmp_path):
    """Anything printed to stdout would sit between two JSON-RPC frames."""
    monkeypatch.setattr(sys, "stdin", _stdin_holding((json.dumps(INITIALIZE) + "\n").encode()))
    path, handle = _capture_stdout(monkeypatch, tmp_path)

    run_acp(build_agent_fn=_never_built)

    handle.close()
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 1, lines
    assert json.loads(lines[0])["id"] == INITIALIZE["id"]


def _open_stdout(monkeypatch, kind: str, tmp_path):
    """Return ``(stdout_placeholder, read_back)`` for one kind of descriptor.

    ``read_back`` is ``None`` when nothing can be read back, as for ``/dev/null``.
    """
    if kind == "devnull":
        handle = open(os.devnull, "wb")
        monkeypatch.setattr(sys, "stdout", handle)
        return handle, None

    if kind == "regular_file":
        path = tmp_path / "out.bin"
        handle = open(path, "wb")
        monkeypatch.setattr(sys, "stdout", handle)
        return handle, lambda: path.read_bytes()

    fifo = tmp_path / "out.fifo"
    os.mkfifo(fifo)
    # The read end opens at once without a writer; keeping it open is what
    # unblocks the write end below.
    read_fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    handle = os.fdopen(os.open(fifo, os.O_WRONLY), "wb")
    monkeypatch.setattr(sys, "stdout", handle)
    return handle, lambda: os.read(read_fd, 65536)


@pytest.mark.parametrize("kind", ["regular_file", pytest.param("fifo", marks=requires_posix), "devnull"])
def test_the_writer_reaches_whatever_stdout_the_client_captured(monkeypatch, tmp_path, kind):
    """A regular file, a FIFO and a character device are all written to."""
    monkeypatch.setattr(sys, "stdin", _stdin_holding(b""))
    _handle, read_back = _open_stdout(monkeypatch, kind, tmp_path)

    async def write_one():
        _reader, writer = await stdio_streams()
        writer.write(b'{"jsonrpc":"2.0"}\n')
        await writer.drain()

    asyncio.run(write_one())

    if read_back is not None:
        assert read_back() == b'{"jsonrpc":"2.0"}\n'


def test_the_writer_is_the_stream_type_the_sdk_connection_requires(monkeypatch, tmp_path):
    """``AgentSideConnection`` rejects anything that is not a StreamWriter.

    It raises ``TypeError`` rather than failing later, so this pins the return
    type that the handshake above depends on.
    """
    monkeypatch.setattr(sys, "stdin", _stdin_holding(b""))
    handle, _read_back = _open_stdout(monkeypatch, "regular_file", tmp_path)

    async def build():
        return await stdio_streams()

    reader, writer = asyncio.run(build())
    assert isinstance(writer, asyncio.StreamWriter)
    assert isinstance(reader, asyncio.StreamReader)
    writer.close()
    handle.close()
