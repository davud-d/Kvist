"""Lazy, independent Kvist core processes supervised by the controller/bridge."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .events import EventBroker
from .protocol import ExecutionState, ProcessSnapshot
from .security import redact


_LOG = logging.getLogger("kvist.web.process")

# These are the core events the session journal never holds: the ones
# SessionJournal deliberately omits, and command_result, which stdio-multi
# writes straight to stdout after a command (kernel/protocol/event_stdio.py). Every
# other raw core event is meaningful for a reconnecting frontend and is marked
# durable on the live envelope; the durable sequence itself remains owned by
# the core journal. A client applies a durable event from the journal, so an
# event marked durable that the journal never writes would never be applied.
_TRANSIENT_CORE_EVENTS = frozenset(
    {
        "status",
        "live_start",
        "live_stop",
        "debug",
        "stream_chunk",
        "action_output",
        "job_output",
        "context",
        "command_result",
    }
)


def core_command() -> list[str]:
    override = os.environ.get("KVIST_CORE_COMMAND", "").strip()
    if override:
        # A single executable path only; arguments belong to the well-defined
        # protocol below and never come from browser input.
        return [override]
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "kernel"]


class SessionProcess:
    def __init__(
        self,
        *,
        server_id: str,
        workspace: Path,
        session: str,
        broker: EventBroker,
        on_change: Callable[[ProcessSnapshot], None] | None = None,
        command: list[str] | None = None,
    ) -> None:
        self.server_id = server_id
        self.workspace = workspace
        self.session = session
        self._broker = broker
        self._on_change = on_change
        self._command = command or core_command()
        self._process: subprocess.Popen[str] | None = None
        self._write_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._ready = threading.Event()
        self._stdout_done = threading.Event()
        self._stop_requested = False
        self._command_in_flight = False
        self._snapshot = ProcessSnapshot(server_id, str(workspace), session)

    def start(self) -> None:
        with self._state_lock:
            if self._process is not None and self._process.poll() is None:
                return
            self._set_state(ExecutionState.STARTING)
            args = [
                *self._command,
                "--workspace",
                str(self.workspace),
                "--frontend",
                "stdio-multi",
                "--approval-mode",
                "ask",
            ]
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._process = subprocess.Popen(
                args,
                cwd=self.workspace,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
            self._snapshot.pid = self._process.pid
            self._snapshot.started_at = time.time()
            self._snapshot.updated_at = time.time()
            self._notify()
            threading.Thread(target=self._read_stdout, daemon=True).start()
            threading.Thread(target=self._read_stderr, daemon=True).start()
            threading.Thread(target=self._wait, daemon=True).start()
        if not self._ready.wait(30.0):
            self.terminate()
            raise RuntimeError("Kvist core did not complete its protocol handshake.")
        snapshot = self.snapshot()
        if snapshot.state == ExecutionState.FAILED:
            raise RuntimeError(snapshot.error or "Kvist core failed to start.")

    def send_goal(self, text: str) -> None:
        if not text.strip():
            raise ValueError("Task cannot be empty.")
        self.start()
        with self._state_lock:
            if self._command_in_flight:
                raise ValueError("Wait for the model change to finish before sending a message.")
            self._stop_requested = False
            self._write({"type": "goal", "text": text.strip()})
            self._set_state(ExecutionState.RUNNING)

    def command(self, name: str, args: list[str]) -> None:
        from kernel.llm.llm_clients import MODEL_ROLES
        from kernel.llm.reasoning_effort import EFFORT_LEVELS
        from kvist_web.configuration import ROLE_CHOICES

        if not isinstance(args, list) or not all(isinstance(v, str) and v.strip() for v in args):
            raise ValueError("Command arguments must be nonempty text.")
        if not ((name == "models" and len(args) == len(MODEL_ROLES)) or
                (name == "effort" and len(args) == 1 and args[0] in EFFORT_LEVELS) or
                (name == "role" and len(args) == 1 and args[0] in ROLE_CHOICES)):
            raise ValueError("Only model, reasoning and role changes are supported here.")
        with self._state_lock:
            if self._snapshot.state not in {ExecutionState.IDLE, ExecutionState.FINISHED, ExecutionState.STOPPED}:
                raise ValueError("Wait for the current task to finish before changing this.")
            self._snapshot.state = ExecutionState.RUNNING
            self._command_in_flight = True
            try:
                self._write({"type": "command", "name": name, "args": args})
            except Exception:
                self._command_in_flight = False
                self._snapshot.state = ExecutionState.FAILED
                raise
        self._notify()

    def respond(self, response: dict[str, Any]) -> None:
        request_id = response.get("request_id")
        with self._state_lock:
            pending = self._snapshot.pending_input
            if not isinstance(request_id, str) or not pending:
                raise ValueError("No input request is pending.")
            if pending.get("request_id") != request_id:
                raise ValueError("That input request is no longer pending.")
        payload = {"request_id": request_id}
        if pending.get("kind") == "confirm":
            payload["approved"] = response.get("approved") is True
            payload["remember"] = response.get("remember") is True
        else:
            value = response.get("value")
            if value is not None and not isinstance(value, str):
                raise ValueError("Input response must be text or null.")
            payload["value"] = value
        self._write(payload)
        with self._state_lock:
            self._snapshot.pending_input = None
        self._set_state(ExecutionState.RUNNING)

    def cancel(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            self._set_state(ExecutionState.STOPPED)
            return
        self._stop_requested = True
        self._write({"type": "cancel"})

    def control(self, kind: str, agent_id: str = "") -> None:
        """Pause the running task, or stop one of its helpers; the core decides at its next safe point."""
        process = self._process
        if process is None or process.poll() is not None:
            raise ValueError("This session has no running process.")
        if kind == "pause":
            self._write({"type": "pause"})
        elif kind == "helper_stop" and agent_id:
            self._write({"type": "helper_stop", "agent_id": agent_id})
        else:
            raise ValueError("Unknown control message.")

    def terminate(self, timeout: float = 3.0) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin:
                process.stdin.close()
            process.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                process.kill()

    def snapshot(self) -> ProcessSnapshot:
        with self._state_lock:
            value = self._snapshot
            return ProcessSnapshot(**value.to_dict())

    def _write(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise RuntimeError("Kvist process is not accepting commands.")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            process.stdin.write(encoded + "\n")
            process.stdin.flush()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._stdout_done.set()
            return
        try:
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    self._fail("Kvist emitted invalid protocol data.")
                    continue
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    self._fail("Kvist emitted a malformed event.")
                    continue
                self._handle_event(event)
        finally:
            self._stdout_done.set()

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            message = redact(line.strip())
            if message:
                _LOG.warning(
                    message,
                    extra={
                        "component": "agent",
                        "server": self.server_id,
                        "workspace": str(self.workspace),
                        "session": self.session,
                        "process_id": process.pid,
                    },
                )

    def _handle_event(self, event: dict[str, Any]) -> None:
        kind = event["type"]
        with self._state_lock:
            self._snapshot.last_event_type = kind
            self._snapshot.updated_at = time.time()
            if kind == "hello":
                advertised = event.get("session")
                if advertised and advertised != self.session:
                    self._fail("Kvist opened a different session than requested.")
                    return
                if self._snapshot.state == ExecutionState.STARTING:
                    self._snapshot.state = ExecutionState.IDLE
                self._ready.set()
            elif kind in {"live_start", "status", "step", "stream_start", "stream_chunk"}:
                self._snapshot.state = ExecutionState.RUNNING
            elif kind == "input_request":
                self._snapshot.state = ExecutionState.WAITING
                self._snapshot.pending_input = event
            elif kind == "error":
                self._snapshot.state = ExecutionState.FAILED
                self._snapshot.error = str(event.get("message", "Kvist failed."))
            elif kind == "command_result" and self._command_in_flight:
                self._command_in_flight = False
                self._snapshot.pending_input = None
                self._snapshot.state = ExecutionState.IDLE
            elif kind == "turn_end":
                self._snapshot.pending_input = None
                self._snapshot.state = (
                    ExecutionState.STOPPED if self._stop_requested else ExecutionState.FINISHED
                )
                self._stop_requested = False
        self._broker.publish(
            self.server_id,
            str(self.workspace),
            self.session,
            event,
            durable=kind not in _TRANSIENT_CORE_EVENTS,
        )
        self._notify()

    def _wait(self) -> None:
        process = self._process
        if process is None:
            return
        code = process.wait()
        # Let the stdout reader process a final hello/turn_end before deriving
        # a terminal state. Without this, a clean process exit could race the
        # reader and leave a dead core labelled `running` or reject a valid
        # handshake.
        self._stdout_done.wait(timeout=1.0)
        with self._state_lock:
            state = self._snapshot.state
            if state == ExecutionState.STARTING:
                self._snapshot.state = ExecutionState.FAILED
                self._snapshot.error = "Kvist process exited before its protocol handshake."
            elif code != 0 and state not in {
                ExecutionState.FAILED,
                ExecutionState.STOPPED,
            }:
                self._snapshot.state = ExecutionState.FAILED
                self._snapshot.error = f"Kvist process exited with code {code}."
            elif code == 0 and state in {
                ExecutionState.RUNNING,
                ExecutionState.WAITING,
            }:
                self._snapshot.state = ExecutionState.FAILED
                self._snapshot.error = "Kvist process exited before completing its active turn."
            elif code == 0 and state == ExecutionState.IDLE:
                self._snapshot.state = ExecutionState.STOPPED
            self._snapshot.pid = None
            self._snapshot.updated_at = time.time()
            self._ready.set()
        self._broker.publish(
            self.server_id,
            str(self.workspace),
            self.session,
            {"type": "agent.process_exited", "code": code},
        )
        self._notify()

    def _fail(self, message: str) -> None:
        with self._state_lock:
            self._snapshot.state = ExecutionState.FAILED
            self._snapshot.error = message
            self._snapshot.updated_at = time.time()
            self._ready.set()
        self._broker.publish(
            self.server_id,
            str(self.workspace),
            self.session,
            {"type": "error", "message": message},
        )
        self._notify()

    def _set_state(self, state: ExecutionState) -> None:
        with self._state_lock:
            self._snapshot.state = state
            self._snapshot.updated_at = time.time()
        self._broker.publish(
            self.server_id,
            str(self.workspace),
            self.session,
            {"type": "agent.state_changed", "state": state.value},
        )
        self._notify()

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change(self.snapshot())


class ProcessSupervisor:
    """Own one long-lived agent process per server/workspace/session."""

    def __init__(self, server_id: str, broker: EventBroker) -> None:
        self.server_id = server_id
        self.broker = broker
        self._processes: dict[tuple[str, str], SessionProcess] = {}
        self._lock = threading.RLock()
        self._workspace_locks: dict[str, threading.Lock] = {}

    def get_or_start(self, workspace: Path, session: str) -> SessionProcess:
        key = (str(workspace), session)
        with self._lock:
            existing = self._processes.get(key)
            if existing is not None and existing.snapshot().pid is not None:
                return existing
            lock = self._workspace_locks.setdefault(str(workspace), threading.Lock())
        # The active-session marker is workspace-global. Hold this only across
        # switch + hello so concurrent starts cannot select each other's session.
        with lock:
            # A second request for the same session may have passed the first
            # lookup while the first request was completing its handshake.
            # Re-check under the workspace launch lock so exactly one core is
            # ever attached to a session.
            with self._lock:
                existing = self._processes.get(key)
                if existing is not None and existing.snapshot().pid is not None:
                    return existing
            from kernel.session.session_store import SessionStore

            SessionStore(workspace).switch(session)
            process = SessionProcess(
                server_id=self.server_id,
                workspace=workspace,
                session=session,
                broker=self.broker,
            )
            with self._lock:
                self._processes[key] = process
            process.start()
            return process

    def find(self, workspace: str | Path, session: str) -> SessionProcess | None:
        with self._lock:
            return self._processes.get((str(Path(workspace).resolve()), session))

    def snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            return [process.snapshot().to_dict() for process in self._processes.values()]

    def close(self) -> None:
        with self._lock:
            processes = list(self._processes.values())
        for process in processes:
            process.terminate()
