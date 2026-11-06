# -*- coding: utf-8 -*-
"""The runner: one outbound WebSocket to the server, a heartbeat, and a Kvist process per task.

Messages to the server: hello, heartbeat, event (a Kvist event, unchanged, with its task id),
task_reason, task_exit, export_done, message_undelivered, screen_frame (a picture of the agent's screen, only
while the owner watches). From the server: start_task, message, input_response, control, export, screen_watch.
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
from .export import run_export
from .task import Limits, TaskProcess
from .workspace import WorkspaceError, prepare

log = logging.getLogger("kvist_runner")
VERSION = "0.1.0"
OUTBOX_LIMIT = 20_000
FRAME_BACKLOG_LIMIT = 100
MAX_MESSAGE_BYTES = 32 * 1024 * 1024
RECONNECT_MAX_SECONDS = 30.0


class Runner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        self.tasks: dict[int, TaskProcess] = {}
        self._jobs: set[asyncio.Task] = set()
        self._runs: dict[int, asyncio.Task] = {}
        self._outbox: deque[dict] = deque(maxlen=OUTBOX_LIMIT)
        self._wakeup = asyncio.Event()
        self._connected = False
        self._stopping = False
        self.exporter = run_export

    # -- outgoing -------------------------------------------------------------------------

    def emit(self, message: dict) -> None:
        """Queued in order; kept while the connection is down and sent after it is back."""
        self._outbox.append(message)
        self._wakeup.set()

    def emit_frame(self, message: dict) -> bool:
        """A screen picture is worth nothing late: never queued behind a dead connection or a backlog."""
        if not self._connected or len(self._outbox) > FRAME_BACKLOG_LIMIT:
            return False
        self.emit(message)
        return True

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
        elif kind == "prepare_project":
            self._spawn(self._prepare_project(message))
        elif kind == "message":
            await self._message(int(message.get("task_id") or 0), str(message.get("text") or ""))
        elif kind == "screen_watch" and task is not None:
            task.screens.watch(str(message.get("screen_id", "")), float(message.get("lease_seconds") or 0))
        elif kind == "export" and self.exporter is not None:
            self._spawn(self.exporter(self, message))
        elif kind == "control" and message.get("action") == "cancel":
            # Nothing runs here for it (the machine restarted, say): tell the server, or it waits for ever.
            log.info("cancel for a task this runner does not have: %s", message.get("task_id"))
            self.emit({"type": "task_exit", "task_id": int(message.get("task_id") or 0), "exit_code": None,
                       "reason": "cancelled"})
        elif kind in ("input_response", "control", "screen_watch"):
            log.info("message for a task this runner does not have: %s", kind)
        else:
            log.warning("unknown message from the server: %r", kind)

    async def _prepare_project(self, message: dict) -> None:
        """A project was added: make its folder (or clone its repository) now, so it is there before any task."""
        try:
            await prepare(
                self.config.work_dir, 0, str(message.get("repo_url") or ""), bool(message.get("allow_local")),
                str(message.get("folder") or ""), str(message.pop("github_token", "") or ""),
            )
        except WorkspaceError as error:
            # The first task tries again and tells the person; nothing waits on this.
            log.warning("project folder not ready: %s %s", error.reason, error.detail)

    async def _message(self, task_id: int, text: str) -> None:
        task = self.tasks.get(task_id)
        if task is not None and await task.follow_up(text):
            return
        # Nobody to take it: the run ended a moment ago. The server starts the next run with this message.
        self.emit({"type": "message_undelivered", "task_id": task_id, "text": text})

    def _start(self, message: dict) -> None:
        task_id = int(message["task_id"])
        existing = self.tasks.get(task_id)
        if existing is not None:
            self._start_again(existing, message)
            return
        task = TaskProcess(
            task_id=task_id,
            goal=str(message.get("goal", "")),
            repo_url=str(message.get("repo_url", "")),
            github_token=str(message.pop("github_token", "") or ""),
            resume=bool(message.get("resume")),
            env={str(k): str(v) for k, v in (message.get("env") or {}).items()},
            limits=Limits.from_message(message["limits"]),
            session=str(message.get("session") or ""),
            folder=str(message.get("folder") or ""),
            follow_up=bool(message.get("follow_up")),
            config=self.config,
            emit=self.emit,
            on_change=self.heartbeat_now,
            send_frame=self.emit_frame,
        )
        # The message carried the key; nothing here keeps it past the task's own copy.
        message.pop("env", None)
        self.tasks[task_id] = task
        run = self._spawn(self._run_task(task))
        self._runs[task_id] = run

    async def _run_task(self, task: TaskProcess) -> None:
        try:
            await task.run()
        finally:
            self.tasks.pop(task.task_id, None)
            self.heartbeat_now()

    def _start_again(self, existing: TaskProcess, message: dict) -> None:
        """A start for a task this runner already has: the same start twice, or the conversation's next message."""
        if existing.accepts_goals:
            message.pop("env", None)  # not needed: the running process already holds the key
            if message.get("follow_up"):
                # Still up (its turn is finishing, or background work keeps it): it takes the message itself.
                self._spawn(self._message(existing.task_id, str(message.get("goal") or "")))
            return
        run = self._runs.get(existing.task_id)
        if run is not None and not run.done():
            # On its way out: the next run begins when this one has ended, so the two never overlap.
            self._spawn(self._start_after(run, message))

    async def _start_after(self, run: asyncio.Task, message: dict) -> None:
        await asyncio.wait({run})
        self._start(message)

    def _spawn(self, coroutine) -> asyncio.Task:
        job = asyncio.create_task(coroutine)
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)
        return job

    async def shutdown(self) -> None:
        self._stopping = True
        await asyncio.gather(*(task.stop() for task in list(self.tasks.values())), return_exceptions=True)
        # Let the exits reach the server if the connection is still up.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._drain(), 3.0)

    async def _drain(self) -> None:
        while self._outbox and self._connected:
            await asyncio.sleep(0.05)
