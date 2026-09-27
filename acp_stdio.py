# -*- coding: utf-8 -*-
"""ACP stdio streams that accept any file descriptor as stdout.

The SDK's ``acp.stdio.stdio_streams`` hands ``sys.stdout`` to
``loop.connect_write_pipe``, and asyncio's pipe transport refuses every
descriptor that is not a pipe, socket or character device. Redirect the agent's
own output to a plain file -- ``kvist --acp > log``, or a client that captures
it that way -- and the process dies before the handshake answers:
``connect_write_pipe`` raises ``ValueError`` on a regular file, and the client
reports a never-received ``initialize``.

Writes go through ``os.write`` on the raw descriptor, so a regular file, a FIFO
and a tty all work. The writer is a real ``asyncio.StreamWriter`` wrapped around
that transport, which is the byte-level pair ``acp``'s connection accepts.
Nothing here reads method arguments; this module only owns the bytes on the wire.

The input side keeps the SDK's line feeder: it runs on any descriptor type and
matches the transport the SDK itself uses when it cannot rely on pipe semantics.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from asyncio import transports as aio_transports
from typing import cast

__all__ = ["stdio_streams"]


class _WritePipeProtocol(asyncio.BaseProtocol):
    """Drain signalling for a transport that never applies backpressure.

    Mirrors the SDK's protocol of the same name; ``drain`` completes at once
    because the writes below finish inside ``write``.
    """

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._paused = False
        self._drain_waiter: asyncio.Future[None] | None = None

    def pause_writing(self) -> None:  # type: ignore[override]
        self._paused = True
        if self._drain_waiter is None:
            self._drain_waiter = self._loop.create_future()

    def resume_writing(self) -> None:  # type: ignore[override]
        self._paused = False
        if self._drain_waiter is not None and not self._drain_waiter.done():
            self._drain_waiter.set_result(None)
        self._drain_waiter = None

    async def _drain_helper(self) -> None:
        if self._paused and self._drain_waiter is not None:
            await self._drain_waiter


class _DescriptorTransport(asyncio.BaseTransport):
    """Write transport to a raw file descriptor, whatever kind of file it is.

    ``os.write`` accepts regular files, FIFOs, sockets and ttys alike, which is
    the whole point: asyncio's own write-pipe transport does not.
    """

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._is_closing = False

    def write(self, data: bytes) -> None:  # type: ignore[override]
        if self._is_closing:
            return
        view = memoryview(data)
        while view:
            written = os.write(self._fd, view)
            view = view[written:]

    def can_write_eof(self) -> bool:  # type: ignore[override]
        return False

    def is_closing(self) -> bool:  # type: ignore[override]
        return self._is_closing

    def close(self) -> None:  # type: ignore[override]
        self._is_closing = True

    def abort(self) -> None:  # type: ignore[override]
        self.close()

    def get_extra_info(self, name: str, default=None):  # type: ignore[override]
        return default


def _start_stdin_feeder(loop: asyncio.AbstractEventLoop, reader: asyncio.StreamReader) -> None:
    """Feed ``sys.stdin`` into ``reader`` line by line from a daemon thread.

    :param loop: the loop that owns ``reader``.
    :param reader: stream the lines are fed into.
    """

    def blocking_read() -> None:
        try:
            while True:
                data = sys.stdin.buffer.readline()
                if not data:
                    break
                loop.call_soon_threadsafe(reader.feed_data, data)
        finally:
            loop.call_soon_threadsafe(reader.feed_eof)

    threading.Thread(target=blocking_read, daemon=True).start()


async def stdio_streams(
    limit: int | None = None,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Return the ACP streams for this process, whatever stdout points at.

    :param limit: stdin reader buffer limit, as ``run_agent`` sizes it for
        large messages.
    :returns: the stdin reader and a writer to this process's stdout.
    """
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=limit) if limit is not None else asyncio.StreamReader()
    _start_stdin_feeder(loop, reader)

    write_protocol = _WritePipeProtocol()
    # The raw descriptor, not ``sys.stdout``'s text buffer: a reply must reach
    # the client in the order ``write`` was called in.
    transport = _DescriptorTransport(sys.stdout.fileno())
    writer = asyncio.StreamWriter(cast(aio_transports.WriteTransport, transport), write_protocol, None, loop)
    return reader, writer
