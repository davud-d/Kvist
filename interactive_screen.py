"""Persistent prompt_toolkit screen used by Kvist's live banner UI.

The regular :class:`~prompt_toolkit.shortcuts.PromptSession` is deliberately
kept as the owner of the input buffer.  This screen only gives that buffer a
persistent, full-screen layout so history, completion, suggestions and custom
key bindings continue to behave exactly like the legacy prompt.
"""

from __future__ import annotations

import _thread
import io
import queue
import re
import sys
import threading
from collections.abc import Callable
from typing import Any, TextIO

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition, has_completions, has_focus
from prompt_toolkit.formatted_text import ANSI, AnyFormattedText, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.bindings.auto_suggest import (
    load_auto_suggest_bindings,
)
from prompt_toolkit.key_binding.key_bindings import merge_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Dimension, Layout
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import AppendAutoSuggestion, BeforeInput
from prompt_toolkit.layout.screen import Point
from rich.console import Console

import banner
from slash_commands import (
    SLASH_COMMAND_MENU_HEIGHT,
    SLASH_COMMAND_PROMPT,
    SLASH_COMMAND_PROMPT_CONTINUATION,
)


_BANNER_HEIGHT = 17
_MIN_SCREEN_HEIGHT = 31
_MAX_INPUT_HEIGHT = 8
_MAX_TRANSIENT_HEIGHT = 10
_STOP = object()


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


def _clean_ansi(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _OSC_RE.sub("", value)

    def keep_colors(match: re.Match[str]) -> str:
        return match.group(0) if match.group(0).endswith("m") else ""

    value = _CSI_RE.sub(keep_colors, value)
    return _OTHER_ESCAPE_RE.sub("", value)


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
        self._transcript_cache: list[tuple[str, str]] | None = None
        self._transcript_plain = ""
        self._follow_tail = True
        self._transcript_cursor_line = 0

        self._status: Any | None = None
        self._stream: Any | None = None
        self._slot_generation = 0
        self._slot_cache: tuple[int, int, list[tuple[str, str]]] | None = None

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
    def read_input(self) -> str:
        """Wait for the next accepted input without stopping the renderer."""

        if not self.is_running and not self._suspended:
            self.start()
        item = self._input_queue.get()
        if item is _STOP:
            raise EOFError
        return str(item).strip()

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
            self._stream = None
            self._slot_generation += 1
            self._slot_cache = None

        if committed is not None:
            fragments = self._format_content(committed, self._terminal_width())
            if fragments:
                if not self._fragments_end_in_newline(fragments):
                    fragments.append(("", "\n"))
                self._append_formatted(fragments)
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
    def _append_output(self, value: str, stream_name: str) -> None:
        if not value:
            return
        with self._state_lock:
            if (
                self._transcript_entries
                and self._transcript_entries[-1][0] == f"ansi:{stream_name}"
            ):
                kind, previous = self._transcript_entries[-1]
                self._transcript_entries[-1] = (kind, previous + value)
            else:
                self._transcript_entries.append((f"ansi:{stream_name}", value))
            self._transcript_plain += _clean_ansi(value)
            self._transcript_cache = None
            if self._follow_tail:
                self._transcript_cursor_line = self._last_transcript_line_locked()
        self.invalidate()

    def _append_formatted(self, fragments: list[tuple[str, str]]) -> None:
        if not fragments:
            return
        plain = "".join(fragment[1] for fragment in fragments)
        with self._state_lock:
            self._transcript_entries.append(("formatted", fragments))
            self._transcript_plain += plain
            self._transcript_cache = None
            if self._follow_tail:
                self._transcript_cursor_line = self._last_transcript_line_locked()
        self.invalidate()

    def _get_transcript(self) -> AnyFormattedText:
        with self._state_lock:
            if self._transcript_cache is None:
                result: list[tuple[str, str]] = []
                for kind, value in self._transcript_entries:
                    if kind.startswith("ansi:"):
                        result.extend(to_formatted_text(ANSI(_clean_ansi(value))))
                    else:
                        result.extend(value)
                self._transcript_cache = result
            return list(self._transcript_cache)

    def _transcript_cursor(self) -> Point:
        with self._state_lock:
            if self._follow_tail:
                line = self._last_transcript_line_locked()
                column = len(self._transcript_plain.rsplit("\n", 1)[-1])
            else:
                line = self._transcript_cursor_line
                column = 0
        return Point(x=column, y=max(0, line))

    def _last_transcript_line_locked(self) -> int:
        return max(0, self._transcript_plain.count("\n"))

    def _page_size(self) -> int:
        window = self._transcript_window
        render_info = window.render_info if window is not None else None
        return max(1, getattr(render_info, "window_height", 8) - 1)

    def _page_up(self) -> None:
        with self._state_lock:
            last = self._last_transcript_line_locked()
            current = last if self._follow_tail else self._transcript_cursor_line
            self._follow_tail = False
            self._transcript_cursor_line = max(0, current - self._page_size())
        self.invalidate()

    def _page_down(self) -> None:
        with self._state_lock:
            last = self._last_transcript_line_locked()
            target = self._transcript_cursor_line + self._page_size()
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
            )

        header = ConditionalContainer(
            Window(
                FormattedTextControl(self._get_banner),
                height=_BANNER_HEIGHT,
                dont_extend_height=True,
                always_hide_cursor=True,
            ),
            filter=Condition(self._show_banner),
        )

        transcript_control = FormattedTextControl(
            self._get_transcript,
            show_cursor=False,
            get_cursor_position=self._transcript_cursor,
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
                height=Dimension(min=1, max=_MAX_TRANSIENT_HEIGHT),
                dont_extend_height=True,
                wrap_lines=True,
                always_hide_cursor=True,
            ),
            filter=Condition(self._has_transient),
        )

        buffer = self.session.default_buffer
        input_control = BufferControl(
            buffer=buffer,
            lexer=self.session.lexer,
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
                    min=SLASH_COMMAND_MENU_HEIGHT,
                    max=SLASH_COMMAND_MENU_HEIGHT,
                ),
            ),
            filter=has_completions,
        )
        root = HSplit(
            [
                header,
                transcript,
                transient,
                Window(height=1, char=" "),
                input_window,
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
            mouse_support=self.session.mouse_support,
            editing_mode=self.session.app.editing_mode,
            color_depth=self.session.color_depth,
            refresh_interval=0.045,
            input=self._input,
            output=self._output,
            full_screen=True,
        )

    def _create_key_bindings(self) -> Any:
        bindings = KeyBindings()
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

        supplied = self.session.key_bindings
        groups = [load_auto_suggest_bindings()]
        if supplied is not None:
            groups.append(supplied)
        groups.append(bindings)
        return merge_key_bindings(groups)

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

    def _has_transient(self) -> bool:
        with self._state_lock:
            return self._stream is not None or self._status is not None

    def _get_transient(self) -> AnyFormattedText:
        width = self._terminal_width()
        with self._state_lock:
            content = self._stream if self._stream is not None else self._status
            generation = self._slot_generation
            cached = self._slot_cache
            if cached is not None and cached[0:2] == (generation, width):
                return list(cached[2])

        fragments = self._format_content(content, width)
        with self._state_lock:
            if generation == self._slot_generation:
                self._slot_cache = (generation, width, fragments)
        return fragments

    def _transient_cursor(self) -> Point:
        plain = "".join(fragment[1] for fragment in self._get_transient())
        lines = plain.split("\n")
        return Point(x=len(lines[-1]), y=max(0, len(lines) - 1))

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
