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
from dataclasses import replace
from collections.abc import Callable

from kernel.config.version import kvist_version
from kernel.protocol.frontend_protocol import PROTOCOL_VERSION
from kernel.session.durable_json import JsonStorageError
from kernel.ssh import credentials, machines, messages, wire
from kernel.ssh.command import Failure, Target, call_op, classify, forward_args, meaningful, ssh_argv
from kernel.ssh.commands import NAMES
from kernel.ssh.local_commands import LocalCommands
from kernel.ssh.prepare import DialogIO, prepare
from kernel.ssh.tracker import SessionTracker

_HELD_LINES = 20
_RENEW_CHECK_SECONDS = 60.0
_RENEW_BEFORE_SECONDS = 15 * 60.0
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
        machine: str | None = None,
        open_session: str | None = None,
        resume: bool = False,
        record=None,
        prepare_machine: Callable[..., bool] = prepare,
    ) -> None:
        self.target = target
        self._machine = machine
        self._prepare = prepare_machine
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
        options = {"record": record} if record is not None else {}
        self._sessions = SessionTracker(machine, target.workspace, report=self._say, **options)
        # What a frontend shows as the place it works: the machine's name, then the folder on it.
        self._place = machine or target.label
        # The session the person asked to continue; asked of the daemon once, when this frontend drives and the log is read.
        self._open_session = open_session
        # Every launch starts a new session; only a person who asked to go on with one (--resume, a chosen session) stays in it.
        # The daemon keeps its own session between frontends, so the proxy has to ask for the new one itself, once.
        self._fresh_session_due = not (open_session or resume)
        self._swallow_failed_new = False
        self._driver = False
        self._local = LocalCommands(self._write_event)
        self._ever_connected = False
        self._first_attempt_failed = False
        # When the ChatGPT access token lent to the daemon runs out (None: none was lent).
        self._token_expires: float | None = None
        self._held: list[bytes] = []

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
            message = wire.decode(raw)
            if message is not None and self._local.take(message):
                continue
            if self._send_to_server(raw):
                continue
            if self._holding(message):
                self._held.append(raw)
            else:
                self._refuse(message)
        self._frontend_closed.set()
        proc = self._proc
        if proc is not None:
            proc.terminate()

    def _holding(self, message: dict | None) -> bool:
        """While the machine is being made ready and the first connection is being made, a command or a request the frontend sends on its
        own (or the person types) waits for it instead of being refused. Once an attempt has failed the frontend can see the link is down
        (it is told), so what is sent then is refused."""
        kind = message.get("type") if message is not None else "goal"
        waiting = not self._ever_connected and not self._first_attempt_failed
        return waiting and kind in ("command", "goal") and len(self._held) < _HELD_LINES

    def _release_held(self) -> None:
        held, self._held = self._held, []
        for raw in held:
            self._send_to_server(raw)

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

    def _notice(self, text: str, level: str = "info") -> None:
        """A word for the person that is not part of the conversation; a frontend shows it as a notification."""
        if self._greeted:
            self._write_event({"type": "notice", "text": text, "level": level})
        else:
            self._say(text)

    def _refuse(self, message: dict | None) -> None:
        """A line that cannot go to the server now. The frontend is told in the way it waits for: a command is answered, a turn ends."""
        kind = message.get("type") if message is not None else "goal"
        if kind == "command":
            name = str(message.get("name", "")).lstrip("/")
            text = messages.text("not_connected", self._language)
            self._write_event({"type": "command_result", "name": name, "ok": False, "message": text, "effects": {}})
        elif kind == "goal":
            self._notice(messages.text("not_connected", self._language), "warn")
            self._write_event({"type": "turn_end"})
        # A cancel, a pause or the answer to a dialog that is gone has nobody to reach, and nothing to say.

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
            ssh_argv(self.target, ["--remote-attach", "--workspace", self.target.workspace, *self.target.lane_args()]),
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
            raw = _what_the_core_said(raw)
            if raw is None:
                continue
            message = wire.decode(raw)
            kind = message.get("type", "") if message is not None else ""
            if kind == wire.ATTACHED:
                if not message.get("ok"):
                    refusal = "rotated"
                    proc.terminate()
                    continue
                connected, self._connected = True, True
                self._segment = message.get("segment")
                if message.get("unseen_work"):
                    # The agent worked while nobody was here; this interface is how the person sees what it did, in the session it did it in.
                    self._fresh_session_due = False
                if not self._ever_connected:
                    self._ever_connected = True
                    self._release_held()
                self._state("connected")
                threading.Thread(target=self._ping, args=(proc,), daemon=True).start()
                threading.Thread(target=self._renew_loop, args=(proc,), daemon=True).start()
                if message.get("revision", 1) < wire.REVISION:
                    # Nobody will ask for a provider: this daemon was started by a Kvist that did not know how.
                    self._notice(messages.text("daemon_old", self._language, host=self.target.label, path=self.target.workspace), "error")
                elif message.get("needs_credentials"):
                    self._send_provider()
            elif kind == wire.OFFSET:
                self._offset, self._segment = message.get("offset"), message.get("segment")
                self._switch_to_asked_session()
            elif kind == wire.STATE:
                shim_state = message.get("state")
            elif kind == wire.NEED:
                threading.Thread(target=self._lend, args=(message.get("provider"),), daemon=True).start()
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

    def _send_provider(self) -> None:
        """The daemon has no provider yet: lend it this computer's, or tell it to use its own when there is none to lend."""
        settings = credentials.from_this_computer()
        message = {"type": wire.CREDENTIALS, "settings": settings} if settings else {"type": wire.CREDENTIALS, "use_server": True}
        self._token_expires = credentials.chatgpt_expiry(settings) if settings else None
        self._send_to_server(wire.encode(message))

    def _lend(self, provider: object) -> None:
        """The person picked a provider the daemon was not given: send this computer's settings for it, or say there are none."""
        if not isinstance(provider, str):
            return
        settings = credentials.from_this_computer(provider)
        self._token_expires = credentials.chatgpt_expiry(settings) if settings else self._token_expires
        self._send_to_server(wire.encode({"type": wire.CREDENTIALS, "for": provider, "settings": settings}))

    def _renew_loop(self, proc: subprocess.Popen) -> None:
        """A lent ChatGPT token is short-lived: send a new one well before it runs out, for as long as this connection lasts."""
        while proc.poll() is None and not self._frontend_closed.wait(_RENEW_CHECK_SECONDS):
            if self._token_expires is None or self._token_expires - time.time() > _RENEW_BEFORE_SECONDS:
                continue
            renewed = credentials.renewal()
            if renewed is None:
                continue
            self._token_expires = credentials.chatgpt_expiry(renewed)
            self._send_to_server(wire.encode({"type": wire.CREDENTIALS, "renew": renewed}))

    def _located(self, raw: bytes) -> bytes:
        """The daemon's ``hello`` with ``location`` added: the frontend's own folder here is only a stand-in."""
        hello = wire.decode(raw)
        if hello is None:
            return raw
        folder = hello.get("workspace") if isinstance(hello.get("workspace"), str) else self.target.workspace
        return wire.encode({**hello, "location": f"{self._place}:{folder}"})

    def _forward(self, raw: bytes) -> None:
        kind = wire.event_type(raw)
        if kind == "command_result" and self._swallow_failed_new and self._failed_new(raw):
            return
        self._write_frontend(self._located(raw) if kind == "hello" else raw)
        if not self._greeted and kind == "hello":
            self._greeted = True
        if kind == wire.CONTROL:
            message = wire.decode(raw) or {}
            self._driver = message.get("role") == "driver"
        self._sessions.see(kind, raw)

    def _failed_new(self, raw: bytes) -> bool:
        """The answer to the ``new`` the proxy sent itself, when it was refused (a turn was running). It is not the person's command, so nothing is shown."""
        result = wire.decode(raw) or {}
        if result.get("name") != "new":
            return False
        self._swallow_failed_new = False
        return not result.get("ok")

    def _open_fresh_session(self) -> None:
        if not self._driver:
            return
        self._swallow_failed_new = True
        self._send_to_server(wire.encode({"type": "command", "name": "new", "args": []}))

    def _switch_to_asked_session(self) -> None:
        """The log has been read: start the new session every launch begins with, or move to the one the person chose."""
        wanted, self._open_session = self._open_session, None
        fresh, self._fresh_session_due = self._fresh_session_due, False
        if fresh:
            self._open_fresh_session()
        if not wanted or wanted == self._sessions.session:
            return
        command = {"type": "command", "name": "session", "args": ["switch", wanted]}
        if self._driver and self._send_to_server(wire.encode(command)):
            return
        self._write_event({"type": "error", "message": messages.text("cannot_switch", self._language, session=wanted)})

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

    # --- opening ---------------------------------------------------------------

    def _hello_of_the_proxy(self) -> dict:
        """What the frontend needs to open at once, before anything is known about the machine; the daemon's own ``hello`` replaces it."""
        return {
            "type": "hello",
            "protocol_version": PROTOCOL_VERSION,
            "provider": "",
            "models": {},
            "role": "auto",
            "session": "",
            "commands": list(NAMES),
            "kvist_version": kvist_version(),
            "location": f"{self._place}:{self.target.workspace}",
        }

    def _ask_where_to_go(self, ui) -> str | None:
        label = f"{self._place} ({self.target.workspace})"
        options = [
            {"value": "retry", "label": messages.text("try_again", self._language)},
            {"value": "local", "label": messages.text("work_here", self._language)},
            {"value": "close", "label": messages.text("close", self._language)},
        ]
        return ui.choose_option(messages.text("not_opened_title", self._language, label=label), options, current_value="retry", help_text="")

    def _open(self) -> int | None:
        """Open the frontend at once, then make the machine ready, asking in the frontend's own dialogs.

        None when it is ready; otherwise the exit code. A machine that cannot be made ready leaves the choice to the person:
        try again, work on this computer (the frontend starts again here), or close.
        """
        self._write_event(self._hello_of_the_proxy())
        self._greeted = True
        self._notice(messages.text("connecting", self._language, label=f"{self._place}:{self.target.workspace}"))
        probed: dict = {}
        while True:
            probed.clear()
            if self._local.ask(lambda ui: self._prepare(self.target, io=DialogIO(ui), language=self._language, probed=probed)):
                self._remember_folder()
                self._local.ask(lambda ui: self._pick_lane(ui, probed.get("lanes") or []))
                return None
            choice = self._local.ask(self._ask_where_to_go)
            if choice == "retry":
                continue
            if choice == "local":
                self._local.relaunch_here()
                return 0
            return wire.EXIT_GAVE_UP

    def _lane_state(self, lane: dict) -> str | None:
        """Why a running session is not simply free to take: it is working, has a result nobody saw, or is open in another window."""
        # A daemon of an older Kvist says none of this, and is not busy as far as anyone can tell.
        for key, word in (("busy", "lane_running"), ("unseen_work", "lane_unseen"), ("attached", "lane_watched")):
            if lane.get(key):
                return messages.text(word, self._language)
        return None

    def _use_lane(self, lane: str, *, starts_fresh: bool) -> None:
        """Work in this lane; ``starts_fresh`` when its session is already new (or the one the person chose), so no ``new`` is sent."""
        self.target = replace(self.target, lane=lane)
        self._fresh_session_due = self._fresh_session_due and not starts_fresh

    def _another_lane(self, free: list[dict]) -> None:
        """A session for a start that must not join one that is busy: a free lane is reused, else one more daemon is started."""
        if free:
            return self._use_lane(free[0].get("lane", ""), starts_fresh=False)
        code, answer, stderr = call_op(self.target, "new-lane")
        if answer is not None and answer.get("ok"):
            return self._use_lane(answer["lane"], starts_fresh=True)
        error = (answer or {}).get("error") or (meaningful(stderr).splitlines() or [f"exit {code}"])[-1]
        self._notice(messages.text("lane_failed", self._language, error=error), "error")
        self._use_lane("", starts_fresh=False)

    def _pick_lane(self, ui, lanes: list[dict]) -> None:
        """Which of the sessions working in this folder this interface belongs to.

        A daemon works on one session at a time, so a second session working beside the first needs its own daemon (a lane).
        Nothing busy, unseen or open elsewhere: the first lane is used and gets a new session. Otherwise the person is asked:
        join one of those, or start a new one beside them.
        """
        taken = [lane for lane in lanes if self._lane_state(lane)]
        free = [lane for lane in lanes if lane not in taken]
        asked = self._open_session
        running_it = next((lane for lane in lanes if asked and lane.get("session") == asked), None)
        if running_it is not None:
            self._open_session = None
            return self._use_lane(running_it.get("lane", ""), starts_fresh=True)
        if not taken:
            return self._use_lane(free[0].get("lane", "") if free else "", starts_fresh=False)
        if asked:
            return self._another_lane(free)
        options = [
            {
                "value": lane.get("lane") or "@first",
                "label": f"{lane.get('title') or messages.text('lane_untitled', self._language)} — {self._lane_state(lane)}",
            }
            for lane in taken
        ]
        options.append({"value": "@new", "label": messages.text("lane_new", self._language)})
        title = messages.text("lanes_title", self._language, label=f"{self._place}:{self.target.workspace}")
        choice = ui.choose_option(title, options, current_value="@new", help_text="")
        if choice in (None, "@new"):
            return self._another_lane(free)
        self._use_lane("" if choice == "@first" else choice, starts_fresh=True)

    def _remember_folder(self) -> None:
        if not self._machine:
            return
        try:
            machines.remember_folder(self._machine, self.target.workspace)
        except (OSError, JsonStorageError) as error:
            self._say(f"could not note the folder in the machines file: {error}")

    def run(self) -> int:
        threading.Thread(target=self._read_frontend, daemon=True, name="remote-frontend").start()
        not_ready = self._open()
        if not_ready is not None:
            return not_ready
        attempt = 0
        down_since: float | None = None
        while True:
            failure, connected = self._attach_once()
            if not connected and not self._ever_connected:
                self._first_attempt_failed = True
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


def _what_the_core_said(raw: bytes) -> bytes | None:
    """The JSON of a line, without what the server's login shell printed before it; None for a line that holds none.

    A shell startup file that prints (a banner, a system summary) does so for every ssh command, ahead of the core's
    own output and sometimes on the same line.
    """
    start = raw.find(b'{"')
    return raw[start:] if start >= 0 else None


def _json_from(output: str) -> str:
    """Output of a forwarded one-shot operation from its first JSON line on: what a login shell printed first is not part of it."""
    lines = output.splitlines(keepends=True)
    first = next((index for index, line in enumerate(lines) if line.lstrip().startswith(("{", "["))), None)
    return "".join(lines[first:]) if first is not None else output


def run_forwarded(target: Target, argv: list[str], *, language: str | None = None) -> int:
    """A one-shot operation (``--session-op``, ``--remote-op``): run it on the server and pass its answer through."""
    language = language or messages.ui_language()
    command = ssh_argv(target, forward_args(argv, target.workspace))
    completed = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace")
    sys.stdout.write(_json_from(completed.stdout))
    sys.stdout.flush()
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
