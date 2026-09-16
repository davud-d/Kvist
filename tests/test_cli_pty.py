from __future__ import annotations

import errno
import json
import os
import re
import select
import signal
import struct
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

if os.name != "nt":
    import fcntl
    import pty
    import termios


pytestmark = [
    pytest.mark.ui_e2e,
    pytest.mark.skipif(os.name == "nt", reason="stdlib PTY harness is POSIX-only"),
]

_ROOT = Path(__file__).resolve().parents[1]
_CHILD_ENTRY = Path(__file__).with_name("_pty_fixture_app.py")
_ANSI_RE = re.compile(
    rb"\x1b(?:\][^\x07]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[()][0-2A-Z]|[@-_])"
)


class _PtyChild:
    def __init__(
        self,
        workspace: Path,
        home: Path,
        *,
        columns: int = 120,
        rows: int = 40,
        entry: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ):
        child_env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TERM": "xterm-256color",
            "PYTHONPATH": str(_ROOT),
            "PYTHONUNBUFFERED": "1",
            "AGENT_PERSIST_LOGS": "0",
            "AGENT_SANDBOX_MODE": "workspace",
        }
        child_env.update(extra_env or {})
        pid, master_fd = pty.fork()
        if pid == 0:
            os.chdir(_ROOT)
            argv = [
                sys.executable,
                str(entry or _CHILD_ENTRY),
                "--workspace",
                str(workspace),
            ]
            os.execve(sys.executable, argv, child_env)
        self.pid = pid
        self.master_fd = master_fd
        self.output = bytearray()
        self._status: int | None = None
        self.resize(columns, rows)

    def resize(self, columns: int, rows: int) -> None:
        size = struct.pack("HHHH", rows, columns, 0, 0)
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, size)
        try:
            os.kill(self.pid, signal.SIGWINCH)
        except ProcessLookupError:
            pass

    def send(self, value: str) -> None:
        os.write(self.master_fd, value.encode("utf-8"))

    def mark(self) -> int:
        return len(self.output)

    def settle(self, *, quiet: float = 0.2, timeout: float = 2.0) -> None:
        """Wait until a modal screen has finished its ownership handoff."""
        deadline = time.monotonic() + timeout
        quiet_since = time.monotonic()
        observed = len(self.output)
        while time.monotonic() < deadline:
            self._read_once(min(0.05, deadline - time.monotonic()))
            current = len(self.output)
            if current != observed:
                observed = current
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= quiet:
                return
        pytest.fail("PTY output did not settle before modal input")

    def visible(self, start: int = 0) -> str:
        cleaned = _ANSI_RE.sub(b"", bytes(self.output[start:]))
        return cleaned.replace(b"\r", b"\n").decode("utf-8", errors="replace")

    def wait_for(self, expected: str, *, start: int = 0, timeout: float = 10.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            visible = self.visible(start)
            if expected in visible:
                return visible
            if self._poll_exit() is not None:
                break
            self._read_once(min(0.1, max(0.0, deadline - time.monotonic())))
        visible = self.visible(start)
        pytest.fail(
            f"PTY child did not render {expected!r}; exit={self.exit_code}; "
            f"tail={visible[-4000:]!r}"
        )

    def wait(self, timeout: float = 10.0) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._poll_exit()
            if status is not None:
                self._drain()
                return os.waitstatus_to_exitcode(status)
            self._read_once(0.1)
        pytest.fail(f"PTY child did not exit; tail={self.visible()[-4000:]!r}")

    @property
    def exit_code(self) -> int | None:
        status = self._poll_exit()
        return None if status is None else os.waitstatus_to_exitcode(status)

    def close(self) -> None:
        if self._poll_exit() is None:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 2
            while self._poll_exit() is None and time.monotonic() < deadline:
                self._read_once(0.05)
            if self._poll_exit() is None:
                try:
                    os.kill(self.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self._poll_exit(block=True)
        self._drain()
        try:
            os.close(self.master_fd)
        except OSError:
            pass

    def _poll_exit(self, *, block: bool = False) -> int | None:
        if self._status is not None:
            return self._status
        options = 0 if block else os.WNOHANG
        try:
            waited_pid, status = os.waitpid(self.pid, options)
        except ChildProcessError:
            return self._status
        if waited_pid:
            self._status = status
        return self._status

    def _read_once(self, timeout: float) -> None:
        ready, _, _ = select.select([self.master_fd], [], [], timeout)
        if not ready:
            return
        try:
            chunk = os.read(self.master_fd, 65536)
        except OSError as error:
            if error.errno == errno.EIO:
                return
            raise
        if chunk:
            self.output.extend(chunk)

    def _drain(self) -> None:
        while True:
            ready, _, _ = select.select([self.master_fd], [], [], 0)
            if not ready:
                return
            before = len(self.output)
            self._read_once(0)
            if len(self.output) == before:
                return


def _spawn(
    workspace: Path,
    home: Path,
    *,
    entry: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> _PtyChild:
    workspace.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    return _PtyChild(workspace, home, entry=entry, extra_env=extra_env)


def test_cli_pty_launch_resize_response_error_and_shutdown(tmp_path):
    child = _spawn(tmp_path / "workspace", tmp_path / "home")
    try:
        child.wait_for("❯", timeout=20)

        start = child.mark()
        child.send("/commands\r")
        child.wait_for("Available commands:", start=start)

        child.resize(82, 28)
        start = child.mark()
        child.send("show a long unicode response please\r")
        response = child.wait_for("LONG-UNICODE-END", start=start, timeout=15)
        assert "строка 079" in response
        assert "🙂" in response

        ready = child.mark()
        child.wait_for("❯ ", start=ready)
        child.settle()
        scroll = child.mark()
        child.send("\x1b[5~")
        child.settle()
        child.send("\x1b[6~")
        child.settle()
        child.send("/commands\r")
        scrolled = child.wait_for("Available commands:", start=scroll)
        assert "show a long unicode response please/commands" not in scrolled

        child.resize(120, 40)
        start = child.mark()
        child.send("trigger safe failure now\r")
        failure = child.wait_for("Task failed safely:", start=start)
        assert "synthetic PTY task failure" in failure
        assert "Traceback (most recent call last)" not in failure

        child.send("/bye\r")
        assert child.wait() == 0
        assert "Traceback (most recent call last)" not in child.visible()
    finally:
        child.close()


def test_cli_pty_session_create_switch_delete_and_restart(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    child = _spawn(workspace, home)
    try:
        child.wait_for("❯", timeout=20)

        start = child.mark()
        child.send("/session\r")
        child.wait_for("+ Create new session...", start=start)
        child.settle()
        # A launch opens its own auto-named session, so the list starts on it.
        redraw = child.mark()
        child.send("\x1b[B")
        child.wait_for("❯ + Create new session...", start=redraw)
        child.settle()
        child.send("\r")
        child.wait_for("New session name:", start=start)
        child.settle()
        child.send("feature_one\r")
        child.wait_for("Active session: feature_one", start=start)

        start = child.mark()
        child.send("/session\r")
        child.wait_for("+ Create new session...", start=start)
        child.settle()
        redraw = child.mark()
        child.send("\x1b[B")
        child.wait_for("❯ + Create new session...", start=redraw)
        child.settle()
        child.send("\r")
        child.wait_for("New session name:", start=start)
        child.settle()
        child.send("feature_two\r")
        child.wait_for("Active session: feature_two", start=start)

        start = child.mark()
        child.send("/session\r")
        child.wait_for("current: feature_two", start=start)
        child.settle()
        redraw = child.mark()
        child.send("\x1b[A")
        child.wait_for("❯   feature_one", start=redraw)
        child.settle()
        child.send("\r")
        child.wait_for("Active session: feature_one", start=start)

        start = child.mark()
        child.send("/session\r")
        child.wait_for("Delete a session...", start=start)
        child.settle()
        for expected in (
            "❯   feature_two",
            "❯ + Create new session...",
            "❯ ✕ Delete a session...",
        ):
            redraw = child.mark()
            child.send("\x1b[B")
            child.wait_for(expected, start=redraw)
            child.settle()
        child.send("\r")
        child.wait_for("Delete session:", start=start)
        child.settle()
        redraw = child.mark()
        child.send("\x1b[B")
        child.wait_for("❯ feature_two", start=redraw)
        child.settle()
        child.send("\r")
        child.wait_for("Session feature_two deleted.", start=start)
        deleted = child.wait_for("Active session: feature_one", start=start)
        assert "Session switch cancelled." not in deleted

        start = child.mark()
        child.send("/new\r")
        started = child.wait_for("── New session:", start=start)
        assert "feature_one" not in started.split("── New session:")[-1]

        child.send("/bye\r")
        assert child.wait() == 0
    finally:
        child.close()

    restarted = _spawn(workspace, home)
    try:
        restarted.wait_for("❯", timeout=20)
        start = restarted.mark()
        restarted.send("/session\r")
        # The restart opens a fresh session; feature_one is kept and listed.
        listing = restarted.wait_for("+ Create new session...", start=start)
        assert "current: feature_one" not in listing
        assert "feature_one" in listing
        restarted.settle()
        restarted.send("\x1b")
        cancelled = restarted.wait_for("Session switch cancelled.", start=start)
        assert "Traceback (most recent call last)" not in cancelled
        restarted.send("/bye\r")
        assert restarted.wait() == 0
    finally:
        restarted.close()


def test_cli_pty_ctrl_c_saves_and_exits_130(tmp_path):
    child = _spawn(tmp_path / "workspace", tmp_path / "home")
    try:
        child.wait_for("❯", timeout=20)
        child.send("\x03")
        assert child.wait() == 130
        assert "Traceback (most recent call last)" not in child.visible()
    finally:
        child.close()


def test_cli_pty_ctrl_c_stops_the_task_and_keeps_the_repl_alive(tmp_path):
    """Ctrl+C during a run ends that turn, not the session."""
    child = _spawn(tmp_path / "workspace", tmp_path / "home")
    try:
        child.wait_for("❯", timeout=20)
        start = child.mark()
        child.send("wait for cancellation now\r")
        child.wait_for("⚡ Role:", start=start)
        child.send("\x03")
        stopped = child.wait_for("Agent run stopped by you.", start=start, timeout=15)
        assert "unexpectedly completed" not in stopped
        assert "Traceback (most recent call last)" not in stopped
        assert child.exit_code is None

        # The session survived the stop, so the normal shutdown path still runs.
        child.send("/bye\r")
        assert child.wait(timeout=15) == 0
    finally:
        child.close()


_CHAT_SECRET = "sk-proj-" + "q" * 24

_CHAT_CHILD_SOURCE = '''\
"""Credential-free child that exercises the real streaming chat path.

Only the provider client is synthetic: the CLI loop, role dispatch,
``LocalAgent._run_chat``, the interactive screen, session persistence, and the
shutdown path all run unchanged in the spawned process.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import llm_clients
import main
from rich.console import Console

from agent import LocalAgent
from agent_state import AgentConfig
from terminal_banner import TerminalBannerUI

SECRET = "{secret}"


def _chunk(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))]
    )


class _ScriptedChatClient:
    def __init__(self):
        self.calls = 0
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    def create(self, **kwargs):
        _ = kwargs
        self.calls += 1
        if self.calls == 1:
            # Blocked inside the provider request; Ctrl+C lands here.
            print("CHAT-REQUEST-BLOCKED", flush=True)
            time.sleep(120)
            raise AssertionError("blocked chat request unexpectedly completed")
        if self.calls == 2:
            return iter([_chunk("OFFLINE-CHAT-OK")])
        raise RuntimeError("Authorization: Bearer " + SECRET)


def _build_chat_agent(
    executor,
    max_steps,
    persist_logs,
    verbose,
    provider_override=None,
    models_override=None,
    language_hint=None,
    ui=None,
    skip_planning=False,
):
    _ = provider_override, models_override, language_hint, ui
    config = AgentConfig(
        provider="openai",
        fast_model="offline-fast",
        smart_model="offline-smart",
        planner_model="offline-planner",
        max_steps=max_steps,
        persist_logs=persist_logs,
        stream=True,
        verbose=verbose,
        skip_planning=skip_planning,
    )
    console = Console()
    agent = LocalAgent(
        _ScriptedChatClient(), executor, config,
        ui=TerminalBannerUI(console=console, verbose=verbose), console=console,
    )
    agent._init_tiktoken_encoding = lambda: None
    return agent, config, []


llm_clients.fallback_candidates_for_model = lambda *args, **kwargs: []
main.build_agent = _build_chat_agent
main.get_saved_role = lambda: "chat"
main.main()
'''


def _write_chat_child(tmp_path: Path) -> Path:
    entry = tmp_path / "_pty_chat_app.py"
    entry.write_text(
        _CHAT_CHILD_SOURCE.format(secret=_CHAT_SECRET),
        encoding="utf-8",
    )
    return entry


def test_cli_pty_ctrl_c_cancels_chat_request_and_keeps_repl_alive(tmp_path):
    entry = _write_chat_child(tmp_path)
    child = _spawn(tmp_path / "workspace", tmp_path / "home", entry=entry)
    try:
        child.wait_for("❯", timeout=20)

        # 1. A chat request that blocks inside the provider call.
        start = child.mark()
        child.send("first chat message\r")
        child.wait_for("CHAT-REQUEST-BLOCKED", start=start, timeout=15)

        # 2. Ctrl+C cancels only that request.
        child.send("\x03")
        cancelled = child.wait_for("Reply stopped by you.", start=start, timeout=15)
        assert "Traceback (most recent call last)" not in cancelled
        assert child.exit_code is None

        # 3. The prompt comes back and the next request works.
        second = child.mark()
        child.send("second chat message\r")
        answered = child.wait_for("OFFLINE-CHAT-OK", start=second, timeout=15)
        assert "Traceback (most recent call last)" not in answered

        # 4. A provider failure also returns control without leaking.
        third = child.mark()
        child.send("third chat message\r")
        failed = child.wait_for(
            "The model did not return a response",
            start=third,
            timeout=15,
        )
        assert "[REDACTED]" in failed
        assert _CHAT_SECRET not in failed

        # 5. Shutdown still runs the normal path.
        child.send("/bye\r")
        assert child.wait(timeout=15) == 0

        transcript = child.visible()
        assert "Traceback (most recent call last)" not in transcript
        assert _CHAT_SECRET not in transcript
    finally:
        child.close()


# ── Live banner UI: the terminal mode real users actually run in ─────────────
#
# The tests above run the plain interface, where prompt_toolkit only owns the
# terminal while a line is being typed, so the tty keeps ISIG and Ctrl+C is a
# signal.  The live banner UI keeps a full-screen application attached for the
# whole session; that is the mode this test pins down, and Ctrl+C is delivered
# the only way a user can deliver it — as the byte 0x03 on the terminal.

_LIVE_UI_ENV = {"COLORTERM": "truecolor"}

_BLOCKING_CHILD_SOURCE = '''\
"""Credential-free child whose provider request blocks until interrupted."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import llm_clients
import main
from rich.console import Console

from agent import LocalAgent
from agent_state import AgentConfig
from terminal_banner import TerminalBannerUI

_started = threading.Event()


def _chunk(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))]
    )


class _BlockingChatClient:
    def __init__(self):
        self.calls = 0
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    def create(self, **kwargs):
        _ = kwargs
        self.calls += 1
        if self.calls == 1:
            print("REQUEST-BLOCKED", flush=True)
            # A real blocking provider read: only a signal can free it.
            time.sleep(120)
            raise AssertionError("blocked request unexpectedly completed")
        return iter([_chunk("SECOND-ANSWER-OK")])


def _build(executor, max_steps, persist_logs, verbose,
           provider_override=None, models_override=None, language_hint=None,
           ui=None, skip_planning=False):
    _ = provider_override, models_override, language_hint, ui
    config = AgentConfig(
        provider="openai",
        fast_model="offline-fast",
        smart_model="offline-smart",
        planner_model="offline-planner",
        max_steps=max_steps,
        persist_logs=persist_logs,
        stream=True,
        verbose=verbose,
        skip_planning=skip_planning,
    )
    console = Console()
    agent = LocalAgent(
        _BlockingChatClient(), executor, config,
        ui=TerminalBannerUI(console=console, verbose=verbose), console=console,
    )
    agent._init_tiktoken_encoding = lambda: None
    return agent, config, []


llm_clients.fallback_candidates_for_model = lambda *args, **kwargs: []
main.build_agent = _build
main.get_saved_role = lambda: "chat"
main.main()
'''


def _write_blocking_child(tmp_path: Path) -> Path:
    entry = tmp_path / "_pty_blocking_app.py"
    entry.write_text(_BLOCKING_CHILD_SOURCE, encoding="utf-8")
    return entry


def _live_ui_child(tmp_path: Path) -> _PtyChild:
    child = _spawn(
        tmp_path / "workspace",
        tmp_path / "home",
        entry=_write_blocking_child(tmp_path),
        extra_env=_LIVE_UI_ENV,
    )
    child.wait_for("❯", timeout=20)
    return child


def test_live_ui_keeps_terminal_able_to_raise_sigint(tmp_path):
    """The live UI must not leave Ctrl+C as an inert byte."""
    child = _live_ui_child(tmp_path)
    try:
        start = child.mark()
        child.send("first chat message\r")
        child.wait_for("REQUEST-BLOCKED", start=start, timeout=20)

        attributes = termios.tcgetattr(child.master_fd)
        assert attributes[3] & termios.ISIG, "ISIG cleared: Ctrl+C cannot signal"
        assert attributes[6][termios.VINTR] == b"\x03"
    finally:
        child.close()


def test_live_ui_ctrl_c_byte_cancels_blocked_request(tmp_path):
    child = _live_ui_child(tmp_path)
    try:
        start = child.mark()
        child.send("first chat message\r")
        child.wait_for("REQUEST-BLOCKED", start=start, timeout=20)

        # Exactly what a user does: the byte, not os.kill(SIGINT).
        child.send("\x03")
        cancelled = child.wait_for("Reply stopped by you.", start=start, timeout=15)
        assert "Traceback (most recent call last)" not in cancelled
        assert child.exit_code is None

        second = child.mark()
        child.send("second chat message\r")
        child.wait_for("SECOND-ANSWER-OK", start=second, timeout=20)

        child.send("/bye\r")
        assert child.wait(timeout=20) == 0
        assert "Traceback (most recent call last)" not in child.visible()
    finally:
        child.close()


def test_live_ui_escape_stops_a_turn_in_flight_on_the_second_press(tmp_path):
    """One Escape only asks for confirmation; the second one stops the turn."""
    child = _live_ui_child(tmp_path)
    try:
        start = child.mark()
        child.send("first chat message\r")
        child.wait_for("REQUEST-BLOCKED", start=start, timeout=20)

        armed = child.mark()
        child.send("\x1b")
        child.wait_for("Press Esc again to stop the agent", start=armed, timeout=10)
        assert "Reply stopped by you." not in child.visible(start)

        # Escape reaches the application as a key, so freeing a thread parked
        # in the provider read depends on it raising a real signal.
        child.send("\x1b")
        cancelled = child.wait_for("Reply stopped by you.", start=start, timeout=15)
        assert "Traceback (most recent call last)" not in cancelled
        assert child.exit_code is None

        second = child.mark()
        child.send("second chat message\r")
        child.wait_for("SECOND-ANSWER-OK", start=second, timeout=20)

        child.send("/bye\r")
        assert child.wait(timeout=20) == 0
    finally:
        child.close()


def test_live_ui_escape_at_the_prompt_does_not_stop_kvist(tmp_path):
    child = _live_ui_child(tmp_path)
    try:
        child.send("\x1b")
        start = child.mark()
        # Still alive and still taking work: Escape was not a stop here.
        child.send("first chat message\r")
        child.wait_for("REQUEST-BLOCKED", start=start, timeout=20)
        assert child.exit_code is None
    finally:
        child.close()


def test_live_ui_ctrl_c_byte_at_prompt_exits_130(tmp_path):
    child = _live_ui_child(tmp_path)
    try:
        # The live banner animates continuously, so ``settle`` never returns;
        # the prompt marker already proves the screen owns the terminal.
        child.send("\x03")
        assert child.wait(timeout=15) == 130
        assert "Traceback (most recent call last)" not in child.visible()
    finally:
        child.close()


def test_live_ui_stopped_turn_is_recorded_as_stopped_by_the_user(tmp_path):
    """The next turn must be able to see that this one was cut short."""
    workspace = tmp_path / "workspace"
    child = _live_ui_child(tmp_path)
    try:
        start = child.mark()
        child.send("first chat message\r")
        child.wait_for("REQUEST-BLOCKED", start=start, timeout=20)
        child.send("\x03")
        child.wait_for("Reply stopped by you.", start=start, timeout=15)
        child.send("/bye\r")
        assert child.wait(timeout=20) == 0
    finally:
        child.close()

    documents = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (workspace / ".kvist" / "sessions").glob("*.json")
    ]
    recorded = [doc for doc in documents if doc.get("session_history")]
    assert len(recorded) == 1
    entry = recorded[0]["session_history"][-1]
    assert entry["goal"] == "first chat message"
    assert entry["stopped_by_user"] is True
    assert recorded[0]["chat_history"][-1] == {
        "role": "assistant",
        "content": "[The user stopped this reply before it finished.]",
    }


def test_cli_pty_settings_api_edits_the_context_window(tmp_path):
    """The /settings-api dialog edits, persists, and reports one knob."""
    home = tmp_path / "home"
    child = _spawn(tmp_path / "workspace", home)
    try:
        child.wait_for("❯", timeout=20)

        start = child.mark()
        child.send("/settings-api\r")
        child.wait_for("API settings", start=start, timeout=15)
        child.settle()

        # Request interval · Request timeout · Retry attempts · Retry backoff ·
        # Context window — the menu opens on the first row, ↓ moves down.
        # (Not "j": choose_option now filters long lists by typing, so a
        # plain letter key is never a hidden navigation shortcut.)
        for _ in range(4):
            child.send("\x1b[B")
            child.settle(quiet=0.1)
        opened = child.mark()
        child.send("\r")
        child.wait_for("Enter value", start=opened, timeout=10)
        assert "Current: auto" in child.visible(opened)
        child.settle()

        edited = child.mark()
        child.send("\x15" "1m\r")
        child.wait_for("Context window — 1 000 000 tokens", start=edited, timeout=10)
        child.settle()

        closed = child.mark()
        child.send("\x1b")
        child.wait_for("Context window: 1 000 000 tokens", start=closed, timeout=10)

        child.send("/bye\r")
        assert child.wait() == 0
        assert "Traceback (most recent call last)" not in child.visible()
    finally:
        child.close()

    saved = json.loads((home / ".kvist" / "config.json").read_text(encoding="utf-8"))
    assert saved["api_settings"] == {"context_tokens": 1_000_000}
