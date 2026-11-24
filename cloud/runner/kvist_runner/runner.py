# -*- coding: utf-8 -*-
"""The runner: one outbound WebSocket to the server, a heartbeat, and a Kvist process per task.

Messages to the server: hello, heartbeat, event (a Kvist event, unchanged, with its task id),
task_reason, task_exit, export_done, message_undelivered, screen_frame (a picture of the agent's screen, only
while the owner watches). From the server: start_task, message, input_response, control, export, screen_watch,
chatgpt_access. Updates in place (release.py): from the server update (stage a release) and apply_release; to
it update_ready and update_failed.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import deque

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from . import release
from .chatgpt import AccessError, lend
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
# A running Kvist gets this long to stop on its own before it is killed for a switch to a new release; its
# session is kept on disk either way, and the next run continues it.
SWITCH_GRACE_SECONDS = 5.0


class Runner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        self.tasks: dict[int, TaskProcess] = {}
        self._chatgpt_token = ""
        self._jobs: set[asyncio.Task] = set()
        self._runs: dict[int, asyncio.Task] = {}
        self._outbox: deque[dict] = deque(maxlen=OUTBOX_LIMIT)
        self._wakeup = asyncio.Event()
        self._connected = False
        self._stopping = False
        # Set when the process must start again to run a release it has just switched to (__main__ does it).
        self.restart = asyncio.Event()
        self._staging = False
        self._switching = False
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
                    "release": release.current(self.config),
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
            access = message.pop("chatgpt", None)
            if isinstance(access, dict):
                self._lend_chatgpt(access)
            self._start(message)
        elif kind == "chatgpt_access":
            # A renewed token for the tasks that are running: Kvist reads it from its config the next time it asks.
            self._lend_chatgpt({name: value for name, value in message.items() if name != "type"})
        elif kind == "input_response" and task is not None:
            await task.answer(message.get("response") or {})
        elif kind == "control" and task is not None:
            await task.control(str(message.get("action", "")))
        elif kind == "prepare_project":
            self._spawn(self._prepare_project(message))
        elif kind == "update":
            self._spawn(self._stage(message))
        elif kind == "apply_release":
            self._spawn(self._switch(str(message.get("release", ""))))
        elif kind == "message":
            task_id = int(message.get("task_id") or 0)
            await self._message(task_id, str(message.get("text") or ""), bool(message.get("command")))
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

    async def _stage(self, message: dict) -> None:
        """Install a release beside the running one. Tasks are untouched; the server decides when to switch."""
        if self._staging:
            return
        self._staging = True
        try:
            staged = await release.stage(self.config, message)
        except release.ReleaseError as error:
            log.error("release %s not staged: %s", message.get("release"), error)
            self.emit({"type": "update_failed", "release": str(message.get("release", "")), "reason": str(error)})
        else:
            self.emit({"type": "update_ready", "release": staged})
        finally:
            self._staging = False

    async def _switch(self, wanted: str) -> None:
        """Stop every Kvist within seconds, point at the staged release and restart. The server resumes the tasks.

        Each stopped task reports ``core_updated`` before the connection goes, so the server continues the same
        session on the new release once this runner says hello again.
        """
        if self._switching:
            return
        if not release.is_ready(self.config, wanted):
            self.emit({"type": "update_failed", "release": wanted, "reason": "the release is not staged"})
            return
        self._switching = True
        try:
            live = list(self.tasks.values())
            runs = [run for task_id in self.tasks if (run := self._runs.get(task_id)) is not None]
            await asyncio.gather(*(task.stop("core_updated", SWITCH_GRACE_SECONDS) for task in live),
                                 return_exceptions=True)
            if runs:
                await asyncio.wait(runs, timeout=SWITCH_GRACE_SECONDS)
            release.activate(self.config, wanted)
            release.prune(self.config)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._drain(), 3.0)
            self.restart.set()
        except (release.ReleaseError, OSError) as error:
            self._switching = False
            log.error("switch to release %s failed: %s", wanted, error)
            self.emit({"type": "update_failed", "release": wanted, "reason": str(error)})

    def _lend_chatgpt(self, access: dict) -> None:
        try:
            lend(self.config.home_dir / ".kvist", access)
        except (AccessError, OSError) as error:
            log.warning("the ChatGPT token was not written: %s", error)
            return
        self._chatgpt_token = str(access["access_token"])
        for task in self.tasks.values():
            task.hide(self._chatgpt_token)

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

    async def _message(self, task_id: int, text: str, command: bool = False) -> None:
        task = self.tasks.get(task_id)
        if task is not None and await task.follow_up(text, command):
            return
        # Nobody to take it: the run ended a moment ago. The server starts the next run with this message.
        self.emit({"type": "message_undelivered", "task_id": task_id, "text": text, "command": command})

    def _start(self, message: dict) -> None:
        if self._switching:
            # The server dispatches nothing during a switch; one that crossed it is sent again after hello.
            log.info("task %s: start dropped, the runner is switching releases", message.get("task_id"))
            return
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
            secrets=[self._chatgpt_token] if self._chatgpt_token else [],
            limits=Limits.from_message(message["limits"]),
            session=str(message.get("session") or ""),
            folder=str(message.get("folder") or ""),
            follow_up=bool(message.get("follow_up")),
            command=bool(message.get("command")),
            commands_off=[str(name) for name in message.get("commands_off") or []],
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
                text = str(message.get("goal") or "")
                self._spawn(self._message(existing.task_id, text, bool(message.get("command"))))
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
