# -*- coding: utf-8 -*-
"""One task = one Kvist process in Mode B (``--frontend stdio-multi``).

The model key lives only in that process's environment. Every line Kvist prints is scrubbed of
it before it is relayed; the relayed event is otherwise unchanged.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass

from .config import RunnerConfig
from .guard import provider_trouble, scrub, with_machine_limits
from .screens import ScreenRelay
from .workspace import WorkspaceError, prepare

log = logging.getLogger("kvist_runner.task")

# Kvist's task_state values that mean the agent is working (billed) or not.
WORKING_STATES = {"running", "resuming", "pause_requested", "cancelling"}
STOPPED_STATES = {"awaiting_input", "paused", "cancelled", "completed", "failed", "idle"}
LINE_LIMIT = 16 * 1024 * 1024
EXIT_WAIT_SECONDS = 30.0
WATCH_SECONDS = 0.25
# Variables a Kvist process may inherit from the runner; the rest is built here.
PASSED_ENV = ("PATH", "TZ", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")


@dataclass(frozen=True, slots=True)
class Limits:
    command_seconds: float
    background_seconds: float
    vcpu: int
    memory_gb: int
    hours_grace_seconds: float = 300.0
    provider_outage_seconds: float = 300.0
    allow_local_repos: bool = False

    @classmethod
    def from_message(cls, raw: dict) -> Limits:
        return cls(
            command_seconds=float(raw["command_seconds"]),
            background_seconds=float(raw["background_seconds"]),
            vcpu=int(raw["vcpu"]),
            memory_gb=int(raw["memory_gb"]),
            hours_grace_seconds=float(raw.get("hours_grace_seconds", 300.0)),
            provider_outage_seconds=float(raw.get("provider_outage_seconds", 300.0)),
            allow_local_repos=bool(raw.get("allow_local_repos", False)),
        )


class TaskProcess:
    def __init__(
        self,
        task_id: int,
        goal: str,
        repo_url: str,
        resume: bool,
        env: dict[str, str],
        limits: Limits,
        config: RunnerConfig,
        emit: Callable[[dict], None],
        on_change: Callable[[], None],
        send_frame: Callable[[dict], bool] = lambda _message: False,
        session: str = "",
    ) -> None:
        self.task_id = task_id
        self.goal = goal
        self.repo_url = repo_url
        self.resume = resume
        self.limits = limits
        self.config = config
        self._emit = emit
        self._on_change = on_change
        self._model_env: dict[str, str] | None = dict(env)
        self._secrets = [value for name, value in env.items() if name.endswith("_API_KEY")]
        self.process: asyncio.subprocess.Process | None = None
        self.running = False
        self.turn_open = False
        self.jobs: dict[str, float] = {}
        self.commands: dict[str, float] = {}
        self.trouble_since: float | None = None
        self.stop_reason = ""
        self.pause_deadline: float | None = None
        self.background_deadline: float | None = None
        self._stdin_closed = False
        self.screens = ScreenRelay(task_id, send_frame)
        # A task is a conversation: a run after the first starts by switching back into the session the
        # first run opened (`session`), and only then takes its message (`_after_switch`).
        self.session = session
        self._after_switch: str | None = None
        # Goals written to Kvist whose turn has not ended yet. A message that comes in while a turn is
        # finishing is one of them: the stdin stays open until its turn has been taken too.
        self._goals_waiting = 0
        self._woken = False

    # -- state the heartbeat reports ------------------------------------------------------

    @property
    def in_background(self) -> bool:
        return bool(self.jobs) and not self.turn_open

    def _set_running(self, value: bool) -> None:
        if value != self.running:
            self.running = value
            self._on_change()

    # -- life cycle -----------------------------------------------------------------------

    async def run(self) -> None:
        try:
            await self._run()
        finally:
            await self.screens.stop()
            # The key goes with the process.
            self._secrets.clear()
            self._model_env = None

    async def _run(self) -> None:
        try:
            workdir = await prepare(self.config.work_dir, self.task_id, self.repo_url, self.limits.allow_local_repos)
        except WorkspaceError as error:
            log.warning("task %s: workspace not ready: %s %s", self.task_id, error.reason, error.detail)
            self._exit(None, error.reason)
            return
        argv = [
            *self.config.kvist_command,
            "--workspace", str(workdir),
            "--frontend", "stdio-multi",
            "--approval-mode", "ask",
        ]
        if self.limits.background_seconds > 0:
            argv.append("--wake")
        argv = with_machine_limits(argv, self.limits.vcpu, self.limits.memory_gb)
        env = self._child_env()
        log_path = self.config.root / "logs" / f"task-{self.task_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(log_path, "ab") as stderr:
                self.process = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=workdir,
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=stderr,
                    limit=LINE_LIMIT,
                    start_new_session=True,
                )
        except OSError as error:
            log.error("task %s: Kvist did not start: %s", self.task_id, error.strerror)
            self._exit(None, "start_failed")
            return
        finally:
            env.clear()
            self._model_env = None
        if self.resume:
            first = {"type": "command", "name": "resume"}
        elif self.session:
            first = {"type": "command", "name": "session", "args": ["switch", self.session]}
            self._after_switch = self.goal
        else:
            first = {"type": "goal", "text": self.goal}
        await self._write(first)
        self._goals_waiting = 1
        self.turn_open = True
        watchdog = asyncio.create_task(self._watch())
        try:
            assert self.process.stdout is not None
            async for line in self.process.stdout:
                await self._handle_line(line)
        finally:
            watchdog.cancel()
        code = await self.process.wait()
        self.turn_open = False
        self.jobs.clear()
        self._set_running(False)
        self._exit(code, self.stop_reason or ("finished" if code == 0 else "crashed"))

    def _child_env(self) -> dict[str, str]:
        home = self.config.home_dir
        home.mkdir(parents=True, exist_ok=True)
        env = {name: os.environ[name] for name in PASSED_ENV if name in os.environ}
        env.update(
            {
                "HOME": str(home),
                "KVIST_CONFIG_DIR": str(home / ".kvist"),
                "LANG": "C.UTF-8",
                "PYTHONUNBUFFERED": "1",
                "PYTHONUTF8": "1",
                # The task folder is the agent's own; asking before every file write helps nobody.
                "AGENT_WRITE_APPROVAL": "0",
                "AGENT_SANDBOX_NET": "allow",
            }
        )
        env.update(self.config.kvist_env)
        env.update(self._model_env or {})
        return env

    def _exit(self, code: int | None, reason: str) -> None:
        self._emit({"type": "task_exit", "task_id": self.task_id, "exit_code": code, "reason": reason})

    # -- Kvist's output -------------------------------------------------------------------

    async def _handle_line(self, raw: bytes) -> None:
        text = scrub(raw.decode("utf-8", "replace"), self._secrets).strip()
        if not text:
            return
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            log.warning("task %s: a line that is not JSON on Kvist's stdout", self.task_id)
            return
        if not isinstance(event, dict):
            return
        if event.get("type") == "screen":
            event = self.screens.sanitize(event)
        self._emit({"type": "event", "task_id": self.task_id, "event": event})
        await self._track(event)

    async def _track(self, event: dict) -> None:
        kind = event.get("type")
        now = time.monotonic()
        if kind == "hello":
            self._set_running(True)
        elif kind == "task_state":
            state = str(event.get("state", ""))
            if state in WORKING_STATES:
                self._set_running(True)
            elif state in STOPPED_STATES:
                self._set_running(False)
        elif kind == "wake":
            self._woken = True
            self.turn_open = True
            self.background_deadline = None
            self._set_running(True)
        elif kind == "turn_end":
            await self._turn_ended()
        elif kind == "command_result" and event.get("name") == "session" and self._after_switch is not None:
            await self._take_message(event)
        elif kind == "command_result" and event.get("name") == "resume" and not event.get("ok"):
            self.stop_reason = self.stop_reason or "resume_failed"
            await self._turn_ended()
        elif kind == "step" and event.get("action_type") == "run_shell":
            self.commands[str(event.get("call_id") or event.get("step"))] = now
        elif kind == "action_result":
            self.commands.pop(str(event.get("call_id") or event.get("step")), None)
        elif kind == "job_started":
            self.jobs[str(event.get("job_id"))] = now
        elif kind == "job_finished":
            self.jobs.pop(str(event.get("job_id")), None)
            if not self.turn_open and not self.jobs:
                await self.close_stdin()
            self._on_change()
        elif kind == "usage":
            # A model call went through: whatever trouble the provider had is over.
            self.trouble_since = None
        trouble = provider_trouble(event)
        if trouble == "auth":
            await self.pause_for("invalid_key")
        elif trouble == "transient" and self.trouble_since is None:
            self.trouble_since = now

    async def _take_message(self, switched: dict) -> None:
        """Back in the conversation (or, if that failed, in a fresh one, said so): now the message."""
        text, self._after_switch = self._after_switch or "", None
        if not switched.get("ok"):
            log.warning("task %s: could not switch back into the session", self.task_id)
            self._emit({"type": "event", "task_id": self.task_id, "event": {
                "type": "message", "source": "notice",
                "markdown": "Could not reopen the earlier conversation, so this message starts a new one. / "
                            "Не удалось открыть прежний разговор: это сообщение начнёт новый."}})
        await self._write({"type": "goal", "text": text})

    @property
    def accepts_goals(self) -> bool:
        """Kvist is up and listening: a message can be written to it now."""
        process = self.process
        if process is None or process.stdin is None or self._stdin_closed:
            return False
        return process.returncode is None

    async def follow_up(self, text: str) -> bool:
        """Another message for the agent that is already running; False when it is already on its way out."""
        if not self.accepts_goals:
            return False
        self.turn_open = True
        self.background_deadline = None
        self._goals_waiting += 1
        self._set_running(True)
        await self._write({"type": "goal", "text": text})
        return True

    async def _turn_ended(self) -> None:
        if self._woken:
            # A wake-up turn nobody typed; it answers no goal.
            self._woken = False
        elif self._goals_waiting > 0:
            self._goals_waiting -= 1
        if self._goals_waiting > 0 and not self.stop_reason:
            # Another message is already queued behind this turn: its turn comes next, so Kvist stays up.
            self.turn_open = True
            return
        self.turn_open = False
        self.commands.clear()
        self.pause_deadline = None
        self._set_running(False)
        if self.stop_reason or self.limits.background_seconds <= 0 or not self.jobs:
            await self.close_stdin()
            return
        self.background_deadline = time.monotonic() + self.limits.background_seconds
        self._on_change()

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(WATCH_SECONDS)
            now = time.monotonic()
            if self.pause_deadline is not None and now >= self.pause_deadline and self.turn_open:
                # The grace is over and Kvist has not paused: stop it; the checkpoint survives.
                self.pause_deadline = None
                await self._write({"type": "cancel"})
            if self.trouble_since is not None and now - self.trouble_since >= self.limits.provider_outage_seconds:
                self.trouble_since = None
                await self.pause_for("provider_unavailable")
            started = [*self.commands.values(), *self.jobs.values()]
            if any(now - when > self.limits.command_seconds for when in started) and not self.stop_reason:
                await self.cancel_for("command_time_limit")
            if self.background_deadline is not None and now >= self.background_deadline:
                self.background_deadline = None
                await self.close_stdin()

    # -- control from the server ----------------------------------------------------------

    async def pause_for(self, reason: str, grace_seconds: float | None = None) -> None:
        """Pause at Kvist's next safe point; if it has not paused after the grace, cancel."""
        if self.stop_reason:
            return
        self.stop_reason = reason
        self._emit({"type": "task_reason", "task_id": self.task_id, "reason": reason})
        if not self.turn_open:
            await self.close_stdin()
            return
        await self._write({"type": "pause"})
        grace = self.limits.hours_grace_seconds if grace_seconds is None else grace_seconds
        self.pause_deadline = time.monotonic() + grace

    async def cancel_for(self, reason: str) -> None:
        if self.stop_reason:
            return
        self.stop_reason = reason
        self._emit({"type": "task_reason", "task_id": self.task_id, "reason": reason})
        await self._cancel()

    async def _cancel(self) -> None:
        if self.turn_open:
            await self._write({"type": "cancel"})
        else:
            await self.close_stdin()

    async def control(self, action: str) -> None:
        if action == "pause":
            if self.turn_open:
                await self._write({"type": "pause"})
        elif action == "cancel":
            self.stop_reason = self.stop_reason or "cancelled"
            await self._cancel()
        elif action == "hours_exhausted":
            await self.pause_for("hours_exhausted")
        else:
            log.warning("task %s: unknown control %r", self.task_id, action)

    async def answer(self, response: dict) -> None:
        await self._write(response)

    async def _write(self, message: dict) -> None:
        process = self.process
        if process is None or process.stdin is None or self._stdin_closed:
            return
        try:
            process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            log.warning("task %s: Kvist's stdin is closed", self.task_id)

    async def close_stdin(self) -> None:
        """End of the task: Kvist exits on EOF. A process that ignores it is killed."""
        process = self.process
        if process is None or process.stdin is None or self._stdin_closed:
            return
        self._stdin_closed = True
        process.stdin.close()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await process.stdin.wait_closed()
        asyncio.get_running_loop().call_later(EXIT_WAIT_SECONDS, self.kill)

    def kill(self) -> None:
        process = self.process
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)

    async def stop(self) -> None:
        """Machine going to sleep: stop the agent (checkpoint kept), then make sure it is gone."""
        self.stop_reason = self.stop_reason or "machine_sleeping"
        await self._cancel()
        await self.close_stdin()
        process = self.process
        if process is None:
            return
        try:
            await asyncio.wait_for(process.wait(), 10.0)
        except TimeoutError:
            self.kill()
