# -*- coding: utf-8 -*-
"""The core a frontend starts when the real one lives on a server.

To the frontend this is an ordinary ``--frontend stdio-multi`` core. Inside, it keeps one ssh
connection to ``kvist-core --remote-attach``, pings so the daemon knows this frontend is alive,
remembers how far into the daemon's log it has read, and after a break continues from there. It
gives up with a reason (never loops forever) when retrying cannot help or the server stays away.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid

from kernel.ssh import messages, wire
from kernel.ssh.command import Failure, Target, classify, forward_args, ssh_argv

GIVE_UP_SECONDS = 600.0
MAX_DELAY_SECONDS = 30.0


class Proxy:
    def __init__(
        self,
        target: Target,
        *,
        stdin=None,
        stdout=None,
        stderr=None,
        language: str | None = None,
        give_up_seconds: float = GIVE_UP_SECONDS,
        max_delay: float = MAX_DELAY_SECONDS,
    ) -> None:
        self.target = target
        self._frontend_in = stdin if stdin is not None else sys.stdin.buffer
        self._frontend_out = stdout if stdout is not None else sys.stdout.buffer
        self._diagnostics = stderr if stderr is not None else sys.stderr
        self._language = language or messages.ui_language()
        self._give_up_seconds = give_up_seconds
        self._max_delay = max_delay
        self._client = uuid.uuid4().hex
        self._frontend_closed = threading.Event()
        self._out_lock = threading.Lock()
        self._in_lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._connected = False
        # A frontend refuses a stream that does not open with ``hello``, so before it nothing but text on stderr.
        self._greeted = False
        self._offset: int | None = None
        self._segment: int | None = None

    # --- the frontend side ---------------------------------------------------

    def _write_frontend(self, raw: bytes) -> None:
        with self._out_lock:
            self._frontend_out.write(raw)
            self._frontend_out.flush()

    def _write_event(self, payload: dict) -> None:
        self._write_frontend(wire.encode(payload))

    def _say(self, text: str) -> None:
        self._diagnostics.write(f"kvist remote: {text}\n")
        self._diagnostics.flush()

    def _state(self, state: str, reason: str = "", **fields: object) -> None:
        if self._greeted:
            payload: dict = {"type": wire.STATE, "state": state, "host": self.target.label, **fields}
            if reason:
                payload["reason"] = reason
            self._write_event(payload)
        elif reason:
            self._say(reason)

    def _read_frontend(self) -> None:
        for raw in self._frontend_in:
            if not raw.endswith(b"\n"):
                raw += b"\n"
            if not self._send_to_server(raw):
                self._not_connected()
        self._frontend_closed.set()
        proc = self._proc
        if proc is not None:
            proc.terminate()

    def _send_to_server(self, raw: bytes) -> bool:
        proc = self._proc
        if proc is None or not self._connected or proc.stdin is None:
            return False
        with self._in_lock:
            try:
                proc.stdin.write(raw)
                proc.stdin.flush()
            except OSError:
                return False
        return True

    def _not_connected(self) -> None:
        text = messages.text("not_connected", self._language)
        if self._greeted:
            self._write_event({"type": "error", "message": text})
        else:
            self._say(text)

    # --- one connection ------------------------------------------------------

    def _ping(self, proc: subprocess.Popen) -> None:
        while proc.poll() is None and not self._frontend_closed.is_set():
            if not self._send_to_server(wire.encode({"type": wire.PING})):
                return
            if self._frontend_closed.wait(wire.PING_SECONDS):
                return

    def _collect_stderr(self, proc: subprocess.Popen, into: list[str]) -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            into.append(raw.decode("utf-8", "replace"))
            del into[:-50]

    def _attach_once(self) -> tuple[Failure, bool]:
        proc = subprocess.Popen(
            ssh_argv(self.target, ["--remote-attach", "--workspace", self.target.workspace]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._proc = proc
        stderr_lines: list[str] = []
        threading.Thread(target=self._collect_stderr, args=(proc, stderr_lines), daemon=True).start()
        hello = {
            "type": wire.HELLO,
            "op": "attach",
            "client": self._client,
            "lang": self._language,
            "from": self._offset,
            "segment": self._segment,
        }
        assert proc.stdin is not None and proc.stdout is not None
        with self._in_lock:
            try:
                proc.stdin.write(wire.encode(hello))
                proc.stdin.flush()
            except OSError:
                pass
        connected, shim_state, refusal = False, None, None
        for raw in proc.stdout:
            message = wire.decode(raw)
            kind = message.get("type", "") if message is not None else ""
            if kind == wire.ATTACHED:
                if not message.get("ok"):
                    refusal = "rotated"
                    proc.terminate()
                    continue
                connected, self._connected = True, True
                self._segment = message.get("segment")
                self._state("connected")
                threading.Thread(target=self._ping, args=(proc,), daemon=True).start()
            elif kind == wire.OFFSET:
                self._offset, self._segment = message.get("offset"), message.get("segment")
            elif kind == wire.STATE:
                shim_state = message.get("state")
            elif not kind.startswith("remote_"):
                self._forward(raw)
        self._connected = False
        proc.wait()
        if refusal:
            return Failure(refusal, True), connected
        if connected and self._greeted and self._offset is None:
            # Part of the replay reached the frontend and nobody knows how much, so continuing would repeat events.
            return Failure("interrupted", True), connected
        return classify(proc.returncode, "".join(stderr_lines), shim_state=shim_state), connected

    def _forward(self, raw: bytes) -> None:
        self._write_frontend(raw)
        if not self._greeted and wire.event_type(raw) == "hello":
            self._greeted = True

    # --- the loop ------------------------------------------------------------

    def _reason(self, failure: Failure) -> str:
        return messages.text(
            failure.kind,
            self._language,
            host=self.target.label,
            path=self.target.workspace,
            minutes=round(self._give_up_seconds / 60),
        )

    def _give_up(self, failure: Failure) -> int:
        reason = self._reason(failure)
        self._state("gave_up", reason)
        if self._greeted:
            self._say(reason)
        return wire.EXIT_GAVE_UP

    def run(self) -> int:
        threading.Thread(target=self._read_frontend, daemon=True, name="remote-frontend").start()
        attempt = 0
        down_since: float | None = None
        while True:
            failure, connected = self._attach_once()
            if self._frontend_closed.is_set():
                return 0
            if failure.definitive:
                return self._give_up(failure)
            if connected:
                attempt, down_since = 0, None
            attempt += 1
            down_since = down_since if down_since is not None else time.monotonic()
            if time.monotonic() - down_since > self._give_up_seconds:
                return self._give_up(Failure("gave_up", True))
            delay = min(self._max_delay, 2.0 ** (attempt - 1))
            self._state("reconnecting", self._reason(failure), attempt=attempt, retry_in=delay)
            if self._frontend_closed.wait(delay):
                return 0


def call_op(target: Target, operation: str) -> tuple[int, dict | None, str]:
    """One ``--remote-op`` on the server: its exit code, its JSON answer if it gave one, and ssh's own complaints."""
    command = ssh_argv(target, ["--remote-op", operation, "--workspace", target.workspace])
    done = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    answer = wire.decode(lines[-1].encode("utf-8")) if lines else None
    return done.returncode, answer, done.stderr


def run_forwarded(target: Target, argv: list[str], *, language: str | None = None) -> int:
    """A one-shot operation (``--session-op``, ``--remote-op``): run it on the server and pass its answer through."""
    language = language or messages.ui_language()
    command = ssh_argv(target, forward_args(argv, target.workspace))
    completed = subprocess.run(command, stderr=subprocess.PIPE, text=True)
    if completed.returncode:
        failure = classify(completed.returncode, completed.stderr, shim_state=None)
        if failure.definitive or completed.returncode == 255:
            print(
                messages.text(failure.kind, language, host=target.label, path=target.workspace, minutes=0),
                file=sys.stderr,
            )
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    return completed.returncode
