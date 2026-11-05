# -*- coding: utf-8 -*-
"""Task events to the browser: history from task_events, then the live stream, over a WebSocket.

Fan-out is an in-process pub/sub; one server process is the MVP's deployment (ARCHITECTURE.md,
decision 13).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict

log = logging.getLogger("kvist_cloud.events")
QUEUE_LIMIT = 2000


class EventHub:
    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, task_id: int) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self._subscribers[task_id].add(queue)
        return queue

    def unsubscribe(self, task_id: int, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(task_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(task_id, None)

    def publish(self, task_id: int, message: dict) -> None:
        for queue in list(self._subscribers.get(task_id, ())):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A browser that stopped reading: drop it; it reloads history on reconnect.
                with contextlib.suppress(asyncio.QueueEmpty):
                    while True:
                        queue.get_nowait()
                queue.put_nowait({"type": "resync"})
