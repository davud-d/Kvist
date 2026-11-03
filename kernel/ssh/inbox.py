# -*- coding: utf-8 -*-
"""The daemon's ``in_reader``: lines from whichever frontend holds the lease.

An ordinary stdin hits EOF when the frontend leaves, and EOF ends the core. This one ends only when
the daemon is told to stop, so the agent keeps working with nobody attached.
"""
from __future__ import annotations

import queue

_END = object()


class ClientInbox:
    def __init__(self) -> None:
        self._lines: queue.Queue = queue.Queue()

    def put(self, line: str) -> None:
        self._lines.put(line)

    def close(self) -> None:
        self._lines.put(_END)

    def __iter__(self) -> "ClientInbox":
        return self

    def __next__(self) -> str:
        item = self._lines.get()
        if item is _END:
            self._lines.put(_END)
            raise StopIteration
        return item
