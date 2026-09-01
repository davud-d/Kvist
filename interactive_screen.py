"""Persistent prompt_toolkit screen used by Kvist's live banner UI.

The regular :class:`~prompt_toolkit.shortcuts.PromptSession` is deliberately
kept as the owner of the input buffer.  This screen only gives that buffer a
persistent, full-screen layout so history, completion, suggestions and custom
key bindings continue to behave exactly like the legacy prompt.
"""

from __future__ import annotations

import _thread
import io
import os
import queue
import re
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TextIO

if os.name != "nt":
    import termios

from prompt_toolkit.application import Application
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, has_completions, has_focus
from prompt_toolkit.formatted_text import (
    ANSI,
    AnyFormattedText,
    StyleAndTextTuples,
    split_lines,
    to_formatted_text,
)
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.bindings.auto_suggest import (
    load_auto_suggest_bindings,
)
from prompt_toolkit.key_binding.key_bindings import merge_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Dimension, Layout
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import (
    BufferControl,
    FormattedTextControl,
    UIContent,
    UIControl,
)
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import AppendAutoSuggestion, BeforeInput
from prompt_toolkit.layout.screen import Point
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from rich.console import Console

import banner
from redaction import escape_terminal_controls
from slash_commands import (
    SLASH_COMMAND_MENU_HEIGHT,
    SLASH_COMMAND_PROMPT,
    SLASH_COMMAND_PROMPT_CONTINUATION,
)


_BANNER_HEIGHT = 17
_MIN_SCREEN_HEIGHT = 31
_MAX_INPUT_HEIGHT = 8
_MAX_TRANSIENT_HEIGHT = 10
_MOUSE_SCROLL_LINES = 3
# Keep at least this many transcript rows visible while a reply streams, so
# scrolling/selecting the existing history never becomes physically impossible
# just because the growing streamed reply is claiming all the vertical space.
_MIN_TRANSCRIPT_ROWS_WHILE_STREAMING = 4
# Re-render the status slot on a time tick so its Rich spinner keeps animating
# between the seconds-apart status updates the agent emits. Without this the
# generation-keyed cache freezes the spinner (a regression from the old Rich
# Live path, which refreshed unconditionally).
_TRANSIENT_ANIM_FPS = 12
_MAX_TRANSCRIPT_CHARS = 500_000
_TRANSCRIPT_RETAIN_CHARS = 400_000
_MAX_TRANSCRIPT_ENTRIES = 2_000
_TRANSCRIPT_TRUNCATION_MARKER = "[Earlier terminal output truncated]\n"
_STOP = object()
_ANSI_CHUNK_BATCH = 64


def _restore_interrupt_signal(input_object: Any) -> None:
    """Set ``ISIG`` back on the input's terminal, ignoring non-terminals."""
    try:
        descriptor = input_object.fileno()
        attributes = termios.tcgetattr(descriptor)
    except (AttributeError, OSError, ValueError, termios.error):
        return
    if attributes[3] & termios.ISIG:
        return
    attributes[3] |= termios.ISIG
    try:
        termios.tcsetattr(descriptor, termios.TCSANOW, attributes)
    except (OSError, ValueError, termios.error):
        return


def _without_leading_newline(value: AnyFormattedText) -> list[tuple[str, str]]:
    """Return prompt fragments suitable for a one-row BufferControl."""

    fragments = list(to_formatted_text(value))
    if fragments and fragments[0][1].startswith("\n"):
        style, text, *mouse_handler = fragments[0]
        fragments[0] = (style, text[1:], *mouse_handler)  # type: ignore[list-item]
    return fragments


_INPUT_PROMPT = _without_leading_newline(SLASH_COMMAND_PROMPT)

# ``ANSI`` understands SGR color sequences, but renders unsupported cursor
# control sequences as visible text.  Keep colors and remove terminal motion,
# OSC hyperlinks/titles, and other escape commands before adding output to the
# transcript.
_OSC_RE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")
_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OTHER_ESCAPE_RE = re.compile(r"\x1b(?!\[)(?:[()][0-2A-Z]|[@-_])")


class _AnsiChunks:
    """Append-only ANSI storage with bounded list overhead and no large +=."""

    __slots__ = ("_blocks", "_pending", "_length")

    def __init__(self, value: str) -> None:
        self._blocks: list[str] = []
        self._pending: list[str] = [value]
        self._length = len(value)

    def __len__(self) -> int:
        return self._length

    def append(self, value: str) -> None:
        self._pending.append(value)
        self._length += len(value)
        if len(self._pending) >= _ANSI_CHUNK_BATCH:
            self._flush()

    def text(self) -> str:
        self._flush()
        return "".join(self._blocks)

    def _flush(self) -> None:
        if self._pending:
            self._blocks.append("".join(self._pending))
            self._pending.clear()


def _ansi_incomplete_suffix(
    value: str,
) -> tuple[int, str] | None:
    """Return the trailing incomplete escape's index and parser kind."""
    escape_index = value.rfind("\x1b")
    if escape_index < 0:
        return None
    suffix = value[escape_index:]
    if suffix.startswith("\x1b["):
        return (
            (escape_index, "csi")
            if _CSI_RE.match(suffix) is None
            else None
        )
    if suffix.startswith("\x1b]"):
        return (
            (escape_index, "osc")
            if "\x07" not in suffix and "\x1b\\" not in suffix
            else None
        )
    return (
        (escape_index, "other")
        if _OTHER_ESCAPE_RE.match(suffix) is None
        else None
    )


def _clean_ansi(value: str) -> str:
    incomplete = _ansi_incomplete_suffix(value)
    if incomplete is not None:
        value = value[:incomplete[0]]
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _OSC_RE.sub("", value)

    def keep_colors(match: re.Match[str]) -> str:
        return match.group(0) if match.group(0).endswith("m") else ""

    value = _CSI_RE.sub(keep_colors, value)
    value = _OTHER_ESCAPE_RE.sub("", value)
    return escape_terminal_controls(value, preserve_ansi_escape=True)


def _ansi_plain(value: str) -> str:
    cleaned = _clean_ansi(value)
    if "\x1b" not in cleaned:
        return cleaned
    return "".join(
        fragment[1]
        for fragment in to_formatted_text(ANSI(cleaned))
    )


def _ansi_formatted(value: str) -> list[tuple[str, str]]:
    cleaned = _clean_ansi(value)
    if "\x1b" not in cleaned:
        return [("", cleaned)] if cleaned else []
    return list(to_formatted_text(ANSI(cleaned)))


def _ansi_entry_text(value: Any) -> str:
    if isinstance(value, _AnsiChunks):
        return value.text()
    return value


def _finish_pending_ansi(
    value: str,
    kind: str,
    osc_escape: bool,
) -> tuple[str, bool, bool]:
    """Discard a continued control sequence and return unconsumed text."""
    if kind == "csi":
        for index, character in enumerate(value):
            if "@" <= character <= "~":
                return value[index + 1:], True, False
        return "", False, False
    if kind == "osc":
        if osc_escape and value.startswith("\\"):
            return value[1:], True, False
        for index, character in enumerate(value):
            if character == "\x07":
                return value[index + 1:], True, False
            if (
                character == "\x1b"
                and index + 1 < len(value)
                and value[index + 1] == "\\"
            ):
                return value[index + 2:], True, False
        return "", False, value.endswith("\x1b")
    # ESC plus one final byte.
    return (
        (value[1:], True, False)
        if value
        else ("", False, False)
    )


def _extend_lines(
    target: list[StyleAndTextTuples],
    addition: list[StyleAndTextTuples],
) -> None:
    """Append split lines, joining across an entry's mid-line boundary.

    Entries are cut where a write happened, not where a line ended, so the
    first line of an addition continues the target's last line.
    """
    if not addition:
        return
    if not target:
        target.extend(addition)
        return
    target[-1] = target[-1] + addition[0]
    target.extend(addition[1:])


class _TranscriptControl(UIControl):
    """Transcript view that keeps its lines split between render passes.

    ``FormattedTextControl`` re-splits its whole fragment list inside every
    ``create_content`` call, before its own content cache is consulted, and
    that call happens several times per frame.  The transcript is the one
    control here whose content grows without bound, so it owns the split and
    hands prompt_toolkit a ready line list instead.
    """

    def __init__(
        self,
        get_content: Callable[[], tuple[int, list[StyleAndTextTuples], Point]],
        scroll_up: Callable[[int], None] | None = None,
        scroll_down: Callable[[int], None] | None = None,
    ) -> None:
        self._get_content = get_content
        self._scroll_up = scroll_up
        self._scroll_down = scroll_down
        self._content: UIContent | None = None
        self._content_key: tuple[int, int, Point] | None = None

    def mouse_handler(self, mouse_event: MouseEvent) -> Any:
        event_type = mouse_event.event_type
        if event_type == MouseEventType.SCROLL_UP and self._scroll_up is not None:
            self._scroll_up(_MOUSE_SCROLL_LINES)
            return None
        if event_type == MouseEventType.SCROLL_DOWN and self._scroll_down is not None:
            self._scroll_down(_MOUSE_SCROLL_LINES)
            return None
        return NotImplemented

    def create_content(self, width: int, height: int | None = None) -> UIContent:
        generation, lines, cursor = self._get_content()
        key = (generation, width, cursor)
        if self._content is not None and self._content_key == key:
            return self._content

        content = UIContent(
            get_line=lambda index: lines[index],
            line_count=len(lines),
            cursor_position=cursor,
            show_cursor=False,
        )
        self._content_key = key
        self._content = content
        return content


class _BinaryOutputProxy:
    """Binary facade for libraries which write through ``sys.stdout.buffer``."""

    def __init__(self, owner: "_OutputProxy") -> None:
        self._owner = owner

    def write(self, data: bytes | bytearray) -> int:
        raw = bytes(data)
        self._owner.write(raw.decode(self._owner.encoding, errors="replace"))
        return len(raw)

    def flush(self) -> None:
        self._owner.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._owner._target.buffer, name)


class _OutputProxy(io.TextIOBase):
    """File-like object that forwards writes into an InteractiveScreen."""

    def __init__(
        self,
        screen: "InteractiveScreen",
        target: TextIO,
        stream_name: str,
    ) -> None:
        super().__init__()
        self._screen = screen
        self._target = target
        self._stream_name = stream_name
        self._binary = _BinaryOutputProxy(self)

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return getattr(self._target, "encoding", None) or "utf-8"

    @property
    def errors(self) -> str | None:  # type: ignore[override]
        return getattr(self._target, "errors", None)

    @property
    def buffer(self) -> _BinaryOutputProxy:
        return self._binary

    def write(self, value: str) -> int:
        if not isinstance(value, str):
            value = str(value)
        if value:
            self._screen._append_output(value, self._stream_name)
        return len(value)

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        # The screen invalidation performed by ``write`` is the equivalent of
        # flushing this virtual stream.  Never flush prompt_toolkit's renderer
        # through the proxy; it owns the real output object directly.
        self._screen.invalidate()

    def isatty(self) -> bool:
        return bool(self._target.isatty())

    def fileno(self) -> int:
        return self._target.fileno()

    def writable(self) -> bool:
        return True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


class InteractiveScreen:
    """One persistent banner, transcript and input application.

    ``start`` launches prompt_toolkit in a UI thread.  The calling (main)
    thread remains synchronous and waits in ``read_input`` for accepted input.
    Normal stdout/stderr writes are captured as transcript entries while the
    screen is active.
    """

    def __init__(self, session: Any, config: Any) -> None:
        self.session = session
        self._config = config

        # PromptSession creates these before stdout is replaced.  Retaining
        # them is important: the renderer must never write into our proxy.
        self._input = session.input
        self._preserve_interrupt_signal()
        self._output = session.output
        self._real_stdout: TextIO = sys.stdout
        self._real_stderr: TextIO = sys.stderr
        self._stdout_proxy = _OutputProxy(self, self._real_stdout, "stdout")
        self._stderr_proxy = _OutputProxy(self, self._real_stderr, "stderr")

        self._state_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._ready = threading.Event()
        self._input_queue: queue.Queue[object] = queue.Queue()

        self._app: Application[None] | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._suspended = False
        self._run_error: BaseException | None = None

        self._saved_accept_handler: Any = None
        self._handler_is_installed = False
        self._original_app = session.app
        self._original_layout = session.layout

        # Each entry is either raw ANSI or already formatted prompt_toolkit
        # fragments.  Adjacent stdout writes are coalesced so split SGR escape
        # sequences remain parseable.
        self._transcript_entries: list[tuple[str, Any]] = []
        self._transcript_line_cache: list[StyleAndTextTuples] | None = None
        self._transcript_prefix_lines: list[StyleAndTextTuples] = []
        self._transcript_prefix_entries = 0
        self._last_entry_lines: list[StyleAndTextTuples] = []
        self._last_entry_key: tuple[int, int] | None = None
        self._live: Any = banner.LivePanelState()
        self._transcript_generation = 0
        self._transcript_plain_parts: list[str] = []
        self._transcript_plain_cache: str | None = ""
        self._transcript_chars = 0
        self._transcript_storage_chars = 0
        self._transcript_line_count = 0
        self._transcript_last_line_chars = 0
        self._ansi_pending_stream: str | None = None
        self._ansi_pending_kind: str | None = None
        self._ansi_pending_osc_escape = False
        self._follow_tail = True
        self._transcript_cursor_line = 0

        self._status: Any | None = None
        self._stream: Any | None = None
        # Arrow-key question asked from the agent thread and answered by the
        # UI thread. It renders inside the running application, directly under
        # the banner, so the live panel keeps updating while the user chooses.
        self._choice: dict[str, Any] | None = None
        self._choice_answer: str | None = None
        self._choice_done = threading.Event()
        self._slot_generation = 0
        self._slot_cache: (
            tuple[int, int, int, list[tuple[str, str]]] | None
        ) = None
        self._status_cache: (
            tuple[int, int, int, list[tuple[str, str]]] | None
        ) = None
        self._transient_render_cursor = Point(x=0, y=0)

        self._layout: Layout | None = None
        self._transcript_window: Window | None = None
        self._banner_content: Callable[[], AnyFormattedText] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def app(self) -> Application[None] | None:
        """The active application (also exposed as ``session.app``)."""

        return self._app

    @property
    def layout(self) -> Layout | None:
        return self._layout

    @property
    def _transcript_plain(self) -> str:
        """Materialized compatibility view of the chunked plain transcript."""
        with self._state_lock:
            return self._materialize_transcript_plain_locked()

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._running

    def start(self) -> None:
        """Start the full-screen renderer and install output capture."""

        with self._lifecycle_lock:
            if self._running:
                return

            self._suspended = False
            self._run_error = None
            self._ready.clear()

            try:
                self._install_accept_handler()
                app = self._build_application()
                self._app = app
                assert self._layout is not None
                self.session.app = app
                self.session.layout = self._layout

                sys.stdout = self._stdout_proxy
                sys.stderr = self._stderr_proxy

                thread = threading.Thread(
                    target=self._run_application,
                    args=(app,),
                    name="kvist-prompt-toolkit-ui",
                    daemon=True,
                )
                self._thread = thread
                self._running = True
                thread.start()

                # ``pre_run`` fires after prompt_toolkit owns the terminal.
                # A short wait makes start failures deterministic without
                # hanging on unusual/custom Input implementations.
                self._ready.wait(timeout=2.0)
                if self._run_error is not None:
                    raise RuntimeError("Unable to start interactive screen") from self._run_error
                if not thread.is_alive() and not self._ready.is_set():
                    raise RuntimeError("Interactive screen stopped during startup")
            except BaseException:
                self._shutdown(final=False)
                raise

    def stop(self) -> None:
        """Stop the renderer and restore the process streams.

        This method is intentionally safe after a partially completed start.
        A blocked ``read_input`` is released as EOF.
        """

        with self._lifecycle_lock:
            self._suspended = False
            self._shutdown(final=True)

    def suspend(self) -> None:
        """Temporarily yield the terminal to a Rich/input wizard."""

        with self._lifecycle_lock:
            if self._suspended:
                return
            self._suspended = True
            self._shutdown(final=False)

    def resume(self) -> None:
        """Restore a previously suspended screen, preserving transcript."""

        with self._lifecycle_lock:
            if not self._suspended:
                return
            self._suspended = False
            self.start()

    def _run_application(self, app: Application[None]) -> None:
        def ready() -> None:
            self._ready.set()

        try:
            app.run(pre_run=ready, handle_sigint=False)
        except BaseException as error:
            self._run_error = error
            self._ready.set()
        finally:
            with self._state_lock:
                if self._app is app:
                    self._running = False

    def _request_exit(self, app: Application[None]) -> None:
        def exit_application() -> None:
            if not app.is_done:
                app.exit()

        try:
            loop = app.loop
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(exit_application)
        else:
            try:
                exit_application()
            except Exception:
                # The application can be between construction and ``run``.
                # Stream/session restoration below is still required.
                pass

    def _shutdown(self, *, final: bool) -> None:
        app = self._app
        thread = self._thread

        # A worker thread may be blocked inside ask_choice; releasing it with
        # no answer is what keeps the screen from taking the run down with it.
        self._close_choice(None)

        if app is not None:
            self._request_exit(app)
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=3.0)

        if sys.stdout is self._stdout_proxy:
            sys.stdout = self._real_stdout
        if sys.stderr is self._stderr_proxy:
            sys.stderr = self._real_stderr

        self._restore_accept_handler()
        if getattr(self.session, "app", None) is app:
            self.session.app = self._original_app
        if getattr(self.session, "layout", None) is self._layout:
            self.session.layout = self._original_layout

        self._running = False
        self._thread = None
        self._app = None
        self._ready.clear()
        if final:
            self._input_queue.put(_STOP)

    # ------------------------------------------------------------------
    # Synchronous input
    # ------------------------------------------------------------------
    def set_input_text(self, text: str) -> None:
        """Put ``text`` back into the input buffer, cursor at the end.

        Called from the synchronous main thread while the prompt_toolkit event
        loop runs in the render thread, so the buffer mutation is scheduled on
        that loop rather than performed here.  A no-op for empty text and for a
        screen that is not currently running.
        """
        if not text:
            return
        app = self._app
        buffer = self.session.default_buffer

        def restore() -> None:
            buffer.document = Document(text, len(text))

        try:
            loop = app.loop if app is not None else None
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(restore)
        else:
            restore()
        self.invalidate()

    def read_input(self) -> str:
        """Wait for the next accepted input without stopping the renderer."""

        if not self.is_running and not self._suspended:
            self.start()
        item = self._input_queue.get()
        if item is _STOP:
            raise EOFError
        return str(item).strip()

    def _preserve_interrupt_signal(self) -> None:
        """Keep ``ISIG`` set whenever prompt_toolkit takes the input raw.

        Raw mode clears ``ISIG``, which stops Ctrl+C from being a signal and
        turns it into an ordinary ``\\x03`` byte that only this application
        can act on.  It cannot: between accepting a line and asking for the
        next one the screen reads no input at all, and the main thread spends
        that time blocked inside a provider socket read where no Python
        bytecode runs — which is why ``interrupt_main`` alone cannot free it
        either.  Restoring the flag inside the same context manager that
        cleared it delegates Ctrl+C back to the terminal driver, whose signal
        interrupts that syscall immediately.  Every other key still reaches
        the application, so editing, history, completion and navigation are
        unchanged.
        """
        if os.name == "nt":
            return
        enter_raw_mode = getattr(self._input, "raw_mode", None)
        if enter_raw_mode is None:
            return

        @contextmanager
        def raw_mode_keeping_signals() -> Iterator[None]:
            with enter_raw_mode():
                _restore_interrupt_signal(self._input)
                yield

        self._input.raw_mode = raw_mode_keeping_signals

    def _install_accept_handler(self) -> None:
        if self._handler_is_installed:
            return
        buffer = self.session.default_buffer
        self._saved_accept_handler = buffer.accept_handler
        buffer.accept_handler = self._accept_input
        self._handler_is_installed = True

    def _restore_accept_handler(self) -> None:
        if not self._handler_is_installed:
            return
        self.session.default_buffer.accept_handler = self._saved_accept_handler
        self._handler_is_installed = False

    def _accept_input(self, buffer: Any) -> bool:
        value = buffer.document.text.strip()
        if not value:
            # Never wake the main loop for an empty line: it has nothing to run
            # and would only print a validation message. Returning False lets
            # prompt_toolkit reset the buffer and redraw the prompt.
            self.invalidate()
            return False
        if value:
            fragments = list(_INPUT_PROMPT)
            fragments.extend(
                [
                    ("class:slash-command.input", value),
                    ("", "\n"),
                ]
            )
            self._append_formatted(fragments)
        # Wake the synchronous main loop only after validate_and_handle has
        # returned to prompt_toolkit.  That method appends history and resets
        # the buffer *after* this callback; putting immediately would let a
        # fast caller type the next command into the old document.
        app = self._app
        try:
            loop = app.loop if app is not None else None
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            loop.call_soon(self._input_queue.put, value)
        else:
            self._input_queue.put(value)
        self.invalidate()
        # Buffer.validate_and_handle appends to history, then resets the
        # buffer when the accept handler returns False.
        return False

    # ------------------------------------------------------------------
    # Public screen updates
    # ------------------------------------------------------------------
    def set_config(self, config: Any) -> None:
        with self._state_lock:
            self._config = config
        self.invalidate()

    def set_status(self, content: Any | None) -> None:
        with self._state_lock:
            self._status = content
            self._slot_generation += 1
            self._slot_cache = None
        self.invalidate()

    def set_live(self, snapshot: Any) -> None:
        """Replace the live-telemetry snapshot rendered in the pinned banner."""
        with self._state_lock:
            self._live = snapshot
        self.invalidate()

    def _get_live(self) -> Any:
        with self._state_lock:
            return self._live

    def ask_choice(
        self, title: str, options: list[tuple[str, str]]
    ) -> str | None:
        """Ask the user to pick one option; blocks the calling worker thread.

        Returns the chosen value, or ``None`` when the question was cancelled
        (Esc) or the screen shut down while it was open — callers must treat
        ``None`` as "no answer" and fall back to their safe default.
        """
        if not options or not self.is_running:
            return None

        with self._state_lock:
            if self._choice is not None:
                return None
            self._choice_answer = None
            self._choice_done.clear()
            self._choice = {"title": title, "options": list(options), "index": 0}
        self.invalidate()

        self._choice_done.wait()
        with self._state_lock:
            answer = self._choice_answer
            self._choice = None
            self._choice_answer = None
        self.invalidate()
        return answer

    def _close_choice(self, answer: str | None) -> None:
        with self._state_lock:
            if self._choice is None:
                return
            self._choice_answer = answer
        self._choice_done.set()

    def _move_choice(self, delta: int) -> None:
        with self._state_lock:
            if self._choice is None:
                return
            total = len(self._choice["options"])
            self._choice["index"] = (self._choice["index"] + delta) % total
        self.invalidate()

    def _has_choice(self) -> bool:
        with self._state_lock:
            return self._choice is not None

    def _choice_height(self) -> int:
        with self._state_lock:
            if self._choice is None:
                return 0
            # title + blank + one row per option + hint
            return len(self._choice["options"]) + 3

    def _get_choice(self) -> AnyFormattedText:
        with self._state_lock:
            choice = self._choice
            if choice is None:
                return []
            title = str(choice["title"])
            options = list(choice["options"])
            index = int(choice["index"])

        fragments: list[tuple[str, str]] = [
            ("class:choice.title", f"  {title}\n\n")
        ]
        for position, (_, label) in enumerate(options):
            selected = position == index
            fragments.append(
                (
                    "class:choice.option.current" if selected else "class:choice.option",
                    f"{'❯' if selected else ' '} {label}\n",
                )
            )
        fragments.append(
            ("class:choice.hint", "\n  ↑/↓ — выбор, Enter — подтвердить\n")
        )
        return fragments

    def set_stream(self, content: Any | None) -> None:
        with self._state_lock:
            self._stream = content
            self._slot_generation += 1
            self._slot_cache = None
        self.invalidate()

    def commit_stream(self, content: Any | None = None) -> None:
        """Commit the mutable stream slot once and clear it."""

        with self._state_lock:
            # The active slot is the commit token.  Once cleared, repeated
            # finalization (even with explicit content) is a no-op and cannot
            # duplicate the response in the transcript.
            if self._stream is None:
                return
            committed = self._stream if content is None else content
            fragments = self._format_content(committed, self._terminal_width())
            if fragments:
                if not self._fragments_end_in_newline(fragments):
                    fragments.append(("", "\n"))
                self._transcript_entries.append(("formatted", fragments))
                plain = "".join(
                    fragment[1] for fragment in fragments
                )
                self._append_transcript_plain_locked(plain)
                self._transcript_storage_chars += len(plain)
                self._trim_transcript_locked()
                self._invalidate_transcript_lines_locked()
                if self._follow_tail:
                    self._transcript_cursor_line = (
                        self._last_transcript_line_locked()
                    )
            self._stream = None
            self._slot_generation += 1
            self._slot_cache = None

        self.invalidate()

    def invalidate(self) -> None:
        app = self._app
        if app is not None:
            try:
                app.invalidate()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Transcript/output capture
    # ------------------------------------------------------------------
    def _append_transcript_plain_locked(self, value: str) -> None:
        if not value:
            return
        self._transcript_plain_parts.append(value)
        self._transcript_plain_cache = None
        self._transcript_chars += len(value)
        newlines = value.count("\n")
        self._transcript_line_count += newlines
        if newlines:
            self._transcript_last_line_chars = len(
                value.rsplit("\n", 1)[-1]
            )
        else:
            self._transcript_last_line_chars += len(value)

    def _set_transcript_plain_locked(self, value: str) -> None:
        self._transcript_plain_parts = [value] if value else []
        self._transcript_plain_cache = value
        self._transcript_chars = len(value)
        self._transcript_line_count = value.count("\n")
        self._transcript_last_line_chars = len(
            value.rsplit("\n", 1)[-1]
        )

    def _materialize_transcript_plain_locked(self) -> str:
        if self._transcript_plain_cache is None:
            value = "".join(self._transcript_plain_parts)
            self._transcript_plain_parts = [value] if value else []
            self._transcript_plain_cache = value
        return self._transcript_plain_cache

    def _plain_from_ansi_chunk_locked(
        self,
        value: str,
        stream_name: str,
        *,
        continues_entry: bool,
    ) -> str:
        if (
            not continues_entry
            or self._ansi_pending_stream != stream_name
        ):
            self._ansi_pending_stream = None
            self._ansi_pending_kind = None
            self._ansi_pending_osc_escape = False

        remainder = value
        if self._ansi_pending_kind is not None:
            remainder, finished, osc_escape = _finish_pending_ansi(
                remainder,
                self._ansi_pending_kind,
                self._ansi_pending_osc_escape,
            )
            if not finished:
                self._ansi_pending_osc_escape = osc_escape
                return ""
            self._ansi_pending_stream = None
            self._ansi_pending_kind = None
            self._ansi_pending_osc_escape = False

        incomplete = _ansi_incomplete_suffix(remainder)
        if incomplete is not None:
            index, kind = incomplete
            complete = remainder[:index]
            self._ansi_pending_stream = stream_name
            self._ansi_pending_kind = kind
            self._ansi_pending_osc_escape = (
                kind == "osc" and remainder.endswith("\x1b")
            )
        else:
            complete = remainder
        return _ansi_plain(complete)

    def _append_output(self, value: str, stream_name: str) -> None:
        if not value:
            return
        with self._state_lock:
            continues_entry = bool(
                self._transcript_entries
                and self._transcript_entries[-1][0] == f"ansi:{stream_name}"
            )
            if continues_entry:
                chunks = self._transcript_entries[-1][1]
                assert isinstance(chunks, _AnsiChunks)
                chunks.append(value)
            else:
                self._transcript_entries.append(
                    (f"ansi:{stream_name}", _AnsiChunks(value))
                )
            plain = self._plain_from_ansi_chunk_locked(
                value,
                stream_name,
                continues_entry=continues_entry,
            )
            self._append_transcript_plain_locked(plain)
            self._transcript_storage_chars += len(value)
            self._trim_transcript_locked()
            self._invalidate_transcript_lines_locked()
            if self._follow_tail:
                self._transcript_cursor_line = self._last_transcript_line_locked()
        self.invalidate()

    def _append_formatted(self, fragments: list[tuple[str, str]]) -> None:
        if not fragments:
            return
        plain = "".join(fragment[1] for fragment in fragments)
        with self._state_lock:
            self._transcript_entries.append(("formatted", fragments))
            self._append_transcript_plain_locked(plain)
            self._transcript_storage_chars += len(plain)
            self._trim_transcript_locked()
            self._invalidate_transcript_lines_locked()
            if self._follow_tail:
                self._transcript_cursor_line = self._last_transcript_line_locked()
        self.invalidate()

    def _trim_transcript_locked(self) -> None:
        """Bound transcript memory while retaining a useful recent tail."""
        if (
            self._transcript_chars <= _MAX_TRANSCRIPT_CHARS
            and self._transcript_storage_chars <= _MAX_TRANSCRIPT_CHARS
            and len(self._transcript_entries) <= _MAX_TRANSCRIPT_ENTRIES
        ):
            return

        marker = _TRANSCRIPT_TRUNCATION_MARKER
        remaining_chars = max(0, _TRANSCRIPT_RETAIN_CHARS - len(marker))
        plain = self._materialize_transcript_plain_locked()
        while plain.startswith(marker):
            plain = plain[len(marker):]
        retained_tail = plain[-remaining_chars:] if remaining_chars else ""
        retained_plain = marker + retained_tail
        # Once a bound is reached, historical color metadata is less valuable
        # than predictable append/render cost. Compact the retained visible
        # tail into one literal entry and let new output retain its formatting.
        self._transcript_entries = [
            ("formatted", [("", retained_plain)]),
        ]
        self._set_transcript_plain_locked(retained_plain)
        self._transcript_storage_chars = len(retained_plain)
        self._ansi_pending_stream = None
        self._ansi_pending_kind = None
        self._ansi_pending_osc_escape = False
        self._reset_transcript_lines_locked()
        last = self._last_transcript_line_locked()
        if self._follow_tail:
            self._transcript_cursor_line = last
        else:
            self._transcript_cursor_line = min(
                self._transcript_cursor_line,
                last,
            )

    def _invalidate_transcript_lines_locked(self) -> None:
        self._transcript_line_cache = None
        self._transcript_generation += 1

    def _reset_transcript_lines_locked(self) -> None:
        self._transcript_prefix_lines = []
        self._transcript_prefix_entries = 0
        self._last_entry_lines = []
        self._last_entry_key = None
        self._invalidate_transcript_lines_locked()

    @staticmethod
    def _entry_lines(kind: str, value: Any) -> list[StyleAndTextTuples]:
        if kind.startswith("ansi:"):
            return list(split_lines(_ansi_formatted(_ansi_entry_text(value))))
        return list(split_lines(value))

    def _build_transcript_lines_locked(self) -> list[StyleAndTextTuples]:
        """Split only what changed: every entry but the last is immutable.

        The banner is no longer part of the transcript: it lives in its own
        pinned window, so the transcript starts at line 0 and its scroll math
        carries no banner offset.
        """
        entries = self._transcript_entries
        while self._transcript_prefix_entries < len(entries) - 1:
            kind, value = entries[self._transcript_prefix_entries]
            _extend_lines(
                self._transcript_prefix_lines,
                self._entry_lines(kind, value),
            )
            self._transcript_prefix_entries += 1

        lines: list[StyleAndTextTuples] = []
        _extend_lines(lines, self._transcript_prefix_lines)
        if entries:
            _extend_lines(lines, self._last_entry_lines_locked())
        return lines or [[]]

    def _last_entry_lines_locked(self) -> list[StyleAndTextTuples]:
        """Re-split the growing entry only when it actually grew."""
        kind, value = self._transcript_entries[-1]
        key = (id(value), len(value))
        if self._last_entry_key != key:
            self._last_entry_lines = self._entry_lines(kind, value)
            self._last_entry_key = key
        return self._last_entry_lines

    def _transcript_content(
        self,
    ) -> tuple[int, list[StyleAndTextTuples], Point]:
        """Return the transcript lines and the cursor belonging to them.

        Returning both from one locked read is what keeps the rendered content
        and the cursor position on the same snapshot.  The line list is never
        mutated in place, so the caller may hold it without copying.
        """
        with self._state_lock:
            if self._transcript_line_cache is None:
                self._transcript_line_cache = (
                    self._build_transcript_lines_locked()
                )
            if self._follow_tail:
                cursor = Point(
                    x=self._transcript_last_line_chars,
                    y=self._last_transcript_line_locked(),
                )
            else:
                cursor = Point(x=0, y=self._transcript_cursor_line)
            return (
                self._transcript_generation,
                self._transcript_line_cache,
                cursor,
            )

    def _last_transcript_line_locked(self) -> int:
        return max(0, self._transcript_line_count)

    def _page_size(self) -> int:
        """One page measured in transcript lines, not in screen rows.

        Scroll positions are line numbers, but the window wraps: a single long
        answer paragraph can fill it on its own.  Stepping by the window height
        would then jump over hundreds of lines instead of one screenful.
        ``displayed_lines`` holds one entry per screen row, so the distinct
        line numbers in it are what is actually on screen.
        """
        window = self._transcript_window
        render_info = window.render_info if window is not None else None
        if render_info is None:
            return 8
        return max(1, len(set(render_info.displayed_lines)) - 1)

    def _page_up(self) -> None:
        self._scroll_up(self._page_size())

    def _page_down(self) -> None:
        self._scroll_down(2 * self._page_size())

    def _current_scroll_top_locked(self) -> int:
        render_info = (
            self._transcript_window.render_info
            if self._transcript_window is not None
            else None
        )
        return (
            render_info.vertical_scroll
            if render_info is not None
            else self._transcript_cursor_line
        )

    def _scroll_up(self, amount: int) -> None:
        with self._state_lock:
            current_top = self._current_scroll_top_locked()
            self._follow_tail = False
            self._transcript_cursor_line = max(0, current_top - amount)
        self.invalidate()

    def _scroll_down(self, amount: int) -> None:
        with self._state_lock:
            last = self._last_transcript_line_locked()
            current_top = self._current_scroll_top_locked()
            # Put the virtual cursor past the current bottom so prompt_toolkit
            # advances the viewport instead of merely keeping the old tail
            # visible.
            target = current_top + amount
            if target >= last:
                self._follow_tail = True
                self._transcript_cursor_line = last
            else:
                self._transcript_cursor_line = target
        self.invalidate()

    def _follow_transcript_tail(self) -> None:
        with self._state_lock:
            self._follow_tail = True
            self._transcript_cursor_line = self._last_transcript_line_locked()
        self.invalidate()

    # ------------------------------------------------------------------
    # Layout/rendering
    # ------------------------------------------------------------------
    def _build_application(self) -> Application[None]:
        # Keep the callable across suspend/resume so its frame/cache state is
        # preserved and modal wizards don't replay the boot animation.
        if self._banner_content is None:
            self._banner_content = banner.make_banner_content(
                lambda: self._config,
                self._terminal_width,
                self._get_live,
            )

        banner_window = ConditionalContainer(
            Window(
                FormattedTextControl(self._get_banner, show_cursor=False),
                height=Dimension.exact(_BANNER_HEIGHT),
                dont_extend_height=True,
                wrap_lines=False,
                always_hide_cursor=True,
            ),
            filter=Condition(self._show_banner),
        )

        status = ConditionalContainer(
            Window(
                FormattedTextControl(self._get_status, show_cursor=False),
                height=Dimension(min=1, max=_MAX_TRANSIENT_HEIGHT),
                dont_extend_height=True,
                wrap_lines=True,
                always_hide_cursor=True,
            ),
            filter=Condition(self._has_status),
        )

        transcript_control = _TranscriptControl(
            self._transcript_content,
            scroll_up=lambda amount: self._scroll_up(amount),
            scroll_down=lambda amount: self._scroll_down(amount),
        )
        transcript = Window(
            transcript_control,
            height=Dimension(min=1, weight=1),
            wrap_lines=True,
            always_hide_cursor=True,
            allow_scroll_beyond_bottom=False,
        )
        self._transcript_window = transcript

        transient = ConditionalContainer(
            Window(
                FormattedTextControl(
                    self._get_transient,
                    show_cursor=False,
                    get_cursor_position=self._transient_cursor,
                ),
                # A fixed small cap here clips the growing reply into a tiny
                # box instead of letting it fill down from where it started;
                # an unbounded one instead starves the transcript window of
                # any rows, making it unscrollable/unselectable mid-stream.
                # ``_transient_height`` recomputes the cap against the current
                # terminal size on every render, keeping a scrollable floor.
                height=self._transient_height,
                dont_extend_height=True,
                wrap_lines=True,
                always_hide_cursor=True,
            ),
            filter=Condition(self._has_transient),
        )

        buffer = self.session.default_buffer
        # Lock out typing while a run's status panel is live so nothing can be
        # entered mid-run; the input row is also hidden (see ``input_area``).
        buffer.read_only = Condition(self._has_status)
        input_control = BufferControl(
            buffer=buffer,
            lexer=self.session.lexer,
            key_bindings=self._create_transcript_navigation_bindings(),
            input_processors=[
                BeforeInput(_INPUT_PROMPT),
                AppendAutoSuggestion(),
            ],
            focusable=True,
        )
        input_window = Window(
            input_control,
            height=self._input_height,
            dont_extend_height=True,
            wrap_lines=True,
            get_line_prefix=self._input_line_prefix,
        )

        completion_menu = ConditionalContainer(
            HSplit(
                [
                    CompletionsMenu(
                        max_height=SLASH_COMMAND_MENU_HEIGHT,
                        scroll_offset=0,
                    )
                ],
                height=Dimension(
                    min=1,
                    max=SLASH_COMMAND_MENU_HEIGHT,
                ),
            ),
            filter=has_completions,
        )
        input_area = ConditionalContainer(
            HSplit(
                [
                    Window(height=1, char=" "),
                    input_window,
                ]
            ),
            filter=Condition(self._input_visible),
        )
        choice = ConditionalContainer(
            Window(
                FormattedTextControl(self._get_choice, show_cursor=False),
                height=self._choice_height,
                dont_extend_height=True,
                always_hide_cursor=True,
            ),
            filter=Condition(self._has_choice),
        )
        root = HSplit(
            [
                banner_window,
                choice,
                status,
                transcript,
                transient,
                input_area,
                completion_menu,
            ]
        )
        layout = Layout(root, focused_element=input_control)
        self._layout = layout

        key_bindings = self._create_key_bindings()
        return Application(
            layout=layout,
            key_bindings=key_bindings,
            style=self.session.style,
            include_default_pygments_style=self.session.include_default_pygments_style,
            mouse_support=True,
            editing_mode=self.session.app.editing_mode,
            color_depth=self.session.color_depth,
            refresh_interval=0.045,
            input=self._input,
            output=self._output,
            full_screen=True,
        )

    def _create_key_bindings(self) -> Any:
        bindings = KeyBindings()
        choosing = Condition(self._has_choice)

        @bindings.add(Keys.Up, filter=choosing, eager=True)
        def choice_up(event: Any) -> None:
            self._move_choice(-1)

        @bindings.add(Keys.Down, filter=choosing, eager=True)
        def choice_down(event: Any) -> None:
            self._move_choice(1)

        @bindings.add(Keys.Enter, filter=choosing, eager=True)
        def choice_accept(event: Any) -> None:
            with self._state_lock:
                current = self._choice
                value = (
                    current["options"][current["index"]][0]
                    if current is not None
                    else None
                )
            self._close_choice(value)

        @bindings.add(Keys.Escape, filter=choosing, eager=True)
        def choice_cancel(event: Any) -> None:
            self._close_choice(None)

        buffer_focused = has_focus(self.session.default_buffer)
        single_line = Condition(
            lambda: "\n" not in self.session.default_buffer.text
        )

        @bindings.add(Keys.Enter, filter=buffer_focused & single_line, eager=True)
        def accept(event: Any) -> None:
            event.current_buffer.validate_and_handle()

        @bindings.add(
            Keys.Escape,
            Keys.Enter,
            filter=buffer_focused & single_line,
            eager=True,
        )
        def begin_multiline(event: Any) -> None:
            """Alt/Meta+Enter opens a multiline edit on the first line.

            Once a newline exists, prompt_toolkit's standard Meta+Enter
            binding accepts the complete multiline buffer.  Plain Enter adds
            further lines while that buffer is multiline.
            """

            event.current_buffer.insert_text("\n")

        @bindings.add(Keys.ControlC, eager=True)
        @bindings.add("<sigint>", eager=True)
        def interrupt_main(event: Any) -> None:
            _thread.interrupt_main()

        @bindings.add(Keys.ControlD, filter=buffer_focused, eager=True)
        def eof_or_delete(event: Any) -> None:
            current = event.current_buffer
            if current.text:
                current.delete(count=1)
            else:
                self._append_formatted(
                    [
                        ("class:slash-command.prompt", "❯ "),
                        ("class:slash-command.input", "/bye"),
                        ("", "\n"),
                    ]
                )
                self._input_queue.put("/bye")

        history_mode = buffer_focused & ~has_completions & single_line

        @bindings.add(Keys.Up, filter=history_mode, eager=True)
        def previous_history(event: Any) -> None:
            event.current_buffer.history_backward(count=event.arg or 1)

        @bindings.add(Keys.Down, filter=history_mode, eager=True)
        def next_history(event: Any) -> None:
            event.current_buffer.history_forward(count=event.arg or 1)

        supplied = self.session.key_bindings
        groups = [load_auto_suggest_bindings()]
        if supplied is not None:
            groups.append(supplied)
        groups.append(bindings)
        return merge_key_bindings(groups)

    def _create_transcript_navigation_bindings(self) -> KeyBindings:
        """Override BufferControl's PageUp/PageDown history bindings."""
        bindings = KeyBindings()

        @bindings.add(Keys.PageUp, eager=True)
        def page_up(event: Any) -> None:
            self._page_up()

        @bindings.add(Keys.PageDown, eager=True)
        def page_down(event: Any) -> None:
            self._page_down()

        @bindings.add(
            Keys.End,
            filter=Condition(lambda: not self._follow_tail),
            eager=True,
        )
        def end(event: Any) -> None:
            self._follow_transcript_tail()

        return bindings

    def _get_banner(self) -> AnyFormattedText:
        content = self._banner_content
        if content is None:
            return ""
        try:
            return content()
        except Exception:
            # A resize can briefly produce an unusable width between the
            # condition check and rendering.  A blank frame is preferable to
            # a partially drawn banner or a crashed renderer.
            return ""

    def _show_banner(self) -> bool:
        size = self._terminal_size()
        if size[1] < _MIN_SCREEN_HEIGHT:
            return False
        min_width = (
            banner.FIXED_WIDTH
            + banner.INFO_LABEL_WIDTH
            + banner.INFO_DECORATION_WIDTH
            + banner.MIN_VALUE_ROOM
            + 2
        )
        return size[0] >= min_width

    def _terminal_size(self) -> tuple[int, int]:
        try:
            size = self._output.get_size()
            return int(size.columns), int(size.rows)
        except Exception:
            return 80, 24

    def _terminal_width(self) -> int:
        return self._terminal_size()[0]

    def _input_height(self) -> int:
        return min(
            _MAX_INPUT_HEIGHT,
            max(1, self.session.default_buffer.document.line_count),
        )

    @staticmethod
    def _input_line_prefix(lineno: int, wrap_count: int) -> AnyFormattedText:
        if lineno > 0 or wrap_count > 0:
            return SLASH_COMMAND_PROMPT_CONTINUATION
        return ""

    def _has_status(self) -> bool:
        with self._state_lock:
            return self._status is not None

    def _input_visible(self) -> bool:
        """Hide the input row while a run's status panel is live."""
        with self._state_lock:
            return self._status is None

    def _has_transient(self) -> bool:
        with self._state_lock:
            return self._stream is not None

    def _transient_height(self) -> Dimension:
        """Cap the streaming-reply window so history stays scrollable.

        The reply is allowed to grow well past the old fixed 10-line cap (see
        module docstring context), but never so far that it starves the
        transcript window below ``_MIN_TRANSCRIPT_ROWS_WHILE_STREAMING`` —
        otherwise scrolling/selecting the existing history becomes physically
        impossible while a reply is streaming in.
        """
        _, rows = self._terminal_size()
        reserved = _MIN_TRANSCRIPT_ROWS_WHILE_STREAMING
        if self._show_banner():
            reserved += _BANNER_HEIGHT
        if self._has_status():
            reserved += 1
        if self._input_visible():
            reserved += self._input_height()
        return Dimension(min=1, max=max(1, rows - reserved))

    def _get_status(self) -> AnyFormattedText:
        """Render the run-status panel pinned under the banner.

        The status slot carries an animated spinner, so its cache key includes
        the animation tick.
        """
        width = self._terminal_width()
        with self._state_lock:
            content = self._status
            if content is None:
                return []
            tick = int(time.monotonic() * _TRANSIENT_ANIM_FPS)
            generation = self._slot_generation
            cached = self._status_cache
            if cached is not None and cached[0:3] == (generation, width, tick):
                return list(cached[3])

        fragments = self._format_content(content, width)
        with self._state_lock:
            if generation == self._slot_generation:
                self._status_cache = (generation, width, tick, fragments)
        return fragments

    def _get_transient(self) -> AnyFormattedText:
        """Render the streaming-reply slot at the bottom, above the input.

        A committed stream is static, so it keeps the cheap generation-only
        cache and tracks the cursor for the typing indicator.
        """
        width = self._terminal_width()
        with self._state_lock:
            content = self._stream
            if content is None:
                return []
            generation = self._slot_generation
            cached = self._slot_cache
            if cached is not None and cached[0:2] == (generation, width):
                return list(cached[3])

        fragments = self._format_content(content, width)
        with self._state_lock:
            if generation == self._slot_generation:
                self._slot_cache = (generation, width, 0, fragments)
            plain = "".join(fragment[1] for fragment in fragments)
            lines = plain.split("\n")
            self._transient_render_cursor = Point(
                x=len(lines[-1]),
                y=max(0, len(lines) - 1),
            )
        return fragments

    def _transient_cursor(self) -> Point:
        with self._state_lock:
            return self._transient_render_cursor

    def _format_content(self, content: Any, width: int) -> list[tuple[str, str]]:
        if content is None:
            return []
        if isinstance(content, str):
            return list(to_formatted_text(ANSI(_clean_ansi(content))))
        if hasattr(content, "__pt_formatted_text__"):
            return list(to_formatted_text(content))
        if isinstance(content, (list, tuple)):
            try:
                return list(to_formatted_text(content))
            except Exception:
                pass

        destination = io.StringIO()
        try:
            renderer = Console(
                file=destination,
                force_terminal=True,
                color_system="truecolor",
                width=max(20, width),
                legacy_windows=False,
                soft_wrap=True,
            )
            renderer.print(content, end="")
            return list(to_formatted_text(ANSI(_clean_ansi(destination.getvalue()))))
        except Exception:
            return [("", str(content))]

    @staticmethod
    def _fragments_end_in_newline(fragments: list[tuple[str, str]]) -> bool:
        return bool(fragments and fragments[-1][1].endswith("\n"))


__all__ = ["InteractiveScreen"]
