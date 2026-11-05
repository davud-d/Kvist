# -*- coding: utf-8 -*-
"""The runner: one outbound WebSocket to the server, a heartbeat, and a Kvist process per task.

Messages to the server: hello, heartbeat, event (a Kvist event, unchanged, with its task id),
task_reason, task_exit, export_done. From the server: start_task, input_response, control, export.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import deque

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from .config import RunnerConfig
from .task import Limits, TaskProcess

log = logging.getLogger("kvist_runner")
VERSION = "0.1.0"
OUTBOX_LIMIT = 20_000
MAX_MESSAGE_BYTES = 32 * 1024 * 1024
RECONNECT_MAX_SECONDS = 30.0


class Runner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        self.tasks: dict[int, TaskProcess] = {}
        self._jobs: set[asyncio.Task] = set()
        self._outbox: deque[dict] = deque(maxlen=OUTBOX_LIMIT)
        self._wakeup = asyncio.Event()
        self._connected = False
        self._stopping = False
        self.exporter = None

    # -- outgoing -------------------------------------------------------------------------

    def emit(self, message: dict) -> None:
        """Queued in order; kept while the connection is down and sent after it is back."""
        self._outbox.append(message)
        self._wakeup.set()

    def heartbeat_now(self) -> None:
        # A heartbeat queued while disconnected would describe the past; the server bills by
        # receive time, so only live ones are sent.
        if self._connected:
            self.emit(self._heartbeat())

    def _heartbeat(self) -> dict:
        return {
            "type": "heartbeat",
            "running": sorted(task_id for task_id, task in self.tasks.items() if task.running),
            "background": sorted(task_id for task_id, task in self.tasks.items() if task.in_background),
        }

    # -- connection -----------------------------------------------------------------------

    async def run_forever(self) -> None:
        delay = 1.0
        while not self._stopping:
            try:
                async with connect(
                    self.config.gateway_url,
                    additional_headers={"Authorization": f"Bearer {self.config.token}"},
                    max_size=MAX_MESSAGE_BYTES,
                    open_timeout=15,
                ) as socket:
                    delay = 1.0
                    await self._session(socket)
            except InvalidStatus as error:
                log.error("the server refused this machine (HTTP %s)", error.response.status_code)
            except (OSError, InvalidHandshake, ConnectionClosed, TimeoutError) as error:
                log.warning("connection to the server lost: %s", type(error).__name__)
            finally:
                self._connected = False
            if self._stopping:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_SECONDS)

    async def _session(self, socket: ClientConnection) -> None:
        self._connected = True
        # Whatever was queued for the old connection that is a heartbeat is stale now.
        self._outbox = deque((m for m in self._outbox if m.get("type") != "heartbeat"), maxlen=OUTBOX_LIMIT)
        await socket.send(
            json.dumps(
                {
                    "type": "hello",
                    "runner_version": VERSION,
                    "tasks": [{"task_id": task_id, "running": task.running} for task_id, task in self.tasks.items()],
                }
            )
        )
        self.heartbeat_now()
        workers = [
            asyncio.create_task(self._send_loop(socket)),
            asyncio.create_task(self._receive_loop(socket)),
            asyncio.create_task(self._beat_loop()),
        ]
        try:
            done, _pending = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
            for worker in done:
                worker.result()
        finally:
            for worker in workers:
                worker.cancel()

    async def _send_loop(self, socket: ClientConnection) -> None:
        while True:
            while self._outbox:
                message = self._outbox[0]
                await socket.send(json.dumps(message))
                self._outbox.popleft()
            self._wakeup.clear()
            await self._wakeup.wait()

    async def _beat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.heartbeat_seconds)
            self.heartbeat_now()

    async def _receive_loop(self, socket: ClientConnection) -> None:
        async for raw in socket:
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("a message from the server that is not JSON")
                continue
            if isinstance(message, dict):
                await self.handle(message)

    # -- incoming -------------------------------------------------------------------------

    async def handle(self, message: dict) -> None:
        kind = message.get("type")
        task = self.tasks.get(int(message.get("task_id") or 0))
        if kind == "start_task":
            self._start(message)
        elif kind == "input_response" and task is not None:
            await task.answer(message.get("response") or {})
        elif kind == "control" and task is not None:
            await task.control(str(message.get("action", "")))
        elif kind == "export" and self.exporter is not None:
            self._spawn(self.exporter(self, message))
        elif kind in ("input_response", "control"):
            log.info("message for a task this runner does not have: %s", kind)
        else:
            log.warning("unknown message from the server: %r", kind)

    def _start(self, message: dict) -> None:
        task_id = int(message["task_id"])
        if task_id in self.tasks:
            return
        task = TaskProcess(
            task_id=task_id,
            goal=str(message.get("goal", "")),
            repo_url=str(message.get("repo_url", "")),
            resume=bool(message.get("resume")),
            env={str(k): str(v) for k, v in (message.get("env") or {}).items()},
            limits=Limits.from_message(message["limits"]),
            config=self.config,
            emit=self.emit,
            on_change=self.heartbeat_now,
        )
        # The message carried the key; nothing here keeps it past the task's own copy.
        message.pop("env", None)
        self.tasks[task_id] = task
        self._spawn(self._run_task(task))

    async def _run_task(self, task: TaskProcess) -> None:
        try:
            await task.run()
        finally:
            self.tasks.pop(task.task_id, None)
            self.heartbeat_now()

    def _spawn(self, coroutine) -> None:
        job = asyncio.create_task(coroutine)
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)

    async def shutdown(self) -> None:
        self._stopping = True
        await asyncio.gather(*(task.stop() for task in list(self.tasks.values())), return_exceptions=True)
        # Let the exits reach the server if the connection is still up.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._drain(), 3.0)

    async def _drain(self) -> None:
        while self._outbox and self._connected:
            await asyncio.sleep(0.05)
