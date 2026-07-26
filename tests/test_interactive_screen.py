from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.base import Size
from prompt_toolkit.shortcuts import CompleteStyle

import banner
import interactive_screen as interactive_screen_module
from agent_state import AgentConfig
from interactive_screen import InteractiveScreen
from slash_commands import (
    SLASH_COMMAND_KEY_BINDINGS,
    SLASH_COMMAND_LEXER,
    SLASH_COMMAND_MENU_HEIGHT,
    SlashCommandCompleter,
)


class ResizableDummyOutput(DummyOutput):
    """A prompt_toolkit test output whose terminal size can change."""

    def __init__(self, *, columns: int = 120, rows: int = 40) -> None:
        self.columns = columns
        self.rows = rows

    def get_size(self) -> Size:
        return Size(rows=self.rows, columns=self.columns)

    def get_rows_below_cursor_position(self) -> int:
        return self.rows


def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("prompt_toolkit did not render the expected state")


def _rendered_lines(runtime) -> list[str]:
    app = runtime.screen.app
    assert app is not None
    rendered = app.renderer.last_rendered_screen
    if rendered is None:
        return [""] * runtime.output.rows
    lines: list[str] = []
    for row in range(runtime.output.rows):
        # Screen buffers are nested defaultdicts. Indexing missing cells from
        # this test thread mutates the renderer's live structures and races
        # prompt_toolkit's UI thread. Read only existing cells instead.
        row_data = rendered.data_buffer.get(row, {})
        lines.append(
            "".join(
                cell.char if (cell := row_data.get(column)) is not None else " "
                for column in range(runtime.output.columns)
            ).rstrip()
        )
    return lines


def _rendered_text(runtime) -> str:
    return "\n".join(_rendered_lines(runtime))


@pytest.fixture
def screen_runtime(monkeypatch):
    monkeypatch.setattr(banner, "get_saved_role", lambda: "auto")
    output = ResizableDummyOutput()

    with create_pipe_input() as pipe_input:
        session = PromptSession(
            history=InMemoryHistory(),
            completer=SlashCommandCompleter(),
            complete_while_typing=True,
            complete_style=CompleteStyle.COLUMN,
            reserve_space_for_menu=SLASH_COMMAND_MENU_HEIGHT + 1,
            key_bindings=SLASH_COMMAND_KEY_BINDINGS,
            lexer=SLASH_COMMAND_LEXER,
            input=pipe_input,
            output=output,
        )
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        original_app = session.app
        original_layout = session.layout
        config = AgentConfig(
            fast_model="old-fast",
            smart_model="old-smart",
            planner_model="old-planner",
        )
        screen = InteractiveScreen(session, config)
        screen.start()
        runtime = SimpleNamespace(
            screen=screen,
            session=session,
            input=pipe_input,
            output=output,
            original_stdout=original_stdout,
            original_stderr=original_stderr,
            original_app=original_app,
            original_layout=original_layout,
        )

        try:
            _wait_until(lambda: _rendered_lines(runtime)[0].startswith("╭"))
            yield runtime
        finally:
            screen.stop()

        assert sys.stdout is original_stdout
        assert sys.stderr is original_stderr


def test_banner_is_fixed_at_top_and_prompt_is_below_it(screen_runtime):
    lines = _rendered_lines(screen_runtime)

    assert lines[0].startswith("╭")
    assert lines[16].startswith("╰")
    assert not any("❯" in line for line in lines[:17])
    assert any("❯" in line for line in lines[17:])


def test_slash_menu_opens_below_banner_at_minimum_height(screen_runtime):
    # A long transcript must not steal a row from the fixed command menu.
    screen_runtime.screen._append_output("old output\n" * 30, "stdout")
    screen_runtime.output.rows = 31
    screen_runtime.screen.invalidate()
    screen_runtime.input.send_text("/")

    _wait_until(
        lambda: "Save the active session and exit Kvist"
        in _rendered_text(screen_runtime)
    )
    lines = _rendered_lines(screen_runtime)
    command_row = next(
        index
        for index, line in enumerate(lines)
        if "List all available slash commands" in line
    )

    assert lines[0].startswith("╭")
    assert lines[16].startswith("╰")
    assert command_row > 16
    assert "Save the active session and exit Kvist" in _rendered_text(screen_runtime)


def test_five_submissions_do_not_duplicate_banner(screen_runtime):
    submitted = [f"task number {index}" for index in range(5)]
    for value in submitted:
        screen_runtime.input.send_text(value + "\r")
        assert screen_runtime.screen.read_input() == value

    _wait_until(lambda: submitted[-1] in _rendered_text(screen_runtime))
    lines = _rendered_lines(screen_runtime)
    text = "\n".join(lines)

    for value in submitted:
        assert text.count(f"❯ {value}") == 1
    assert not any("▀" in line or "╭" in line for line in lines[17:])
    assert screen_runtime.session.history.get_strings() == submitted
    assert screen_runtime.session.default_buffer.text == ""


def test_history_navigation_and_multiline_submission(screen_runtime):
    for value in ("first history entry", "second history entry"):
        screen_runtime.input.send_text(value + "\r")
        assert screen_runtime.screen.read_input() == value

    # Buffer history is loaded into its working deque in an application task.
    # A real key press cannot race this sub-millisecond handoff, but PipeInput
    # can, so wait for prompt_toolkit's history view before sending Up.
    _wait_until(
        lambda: len(screen_runtime.session.default_buffer._working_lines) >= 3
    )
    screen_runtime.input.send_text("\x1b[A")
    _wait_until(
        lambda: screen_runtime.session.default_buffer.text
        == "second history entry"
    )
    screen_runtime.session.default_buffer.reset()

    screen_runtime.input.send_text("line one\x1b\r")
    _wait_until(
        lambda: screen_runtime.session.default_buffer.text == "line one\n"
    )
    screen_runtime.input.send_text("line two\x1b\r")

    assert screen_runtime.screen.read_input() == "line one\nline two"
    assert screen_runtime.session.history.get_strings()[-1] == "line one\nline two"
    assert screen_runtime.session.default_buffer.text == ""


def test_config_replacement_clears_long_model_tail(screen_runtime):
    long_model = "openrouter/anthropic/claude-sonnet-4.5"
    screen_runtime.screen.set_config(
        AgentConfig(
            fast_model=long_model,
            smart_model=long_model,
            planner_model=long_model,
        )
    )
    _wait_until(lambda: "claude-sonnet-4.5" in _rendered_text(screen_runtime))

    replacement = AgentConfig(
        fast_model="x",
        smart_model="x",
        planner_model="x",
    )
    screen_runtime.screen.set_config(replacement)
    _wait_until(lambda: "Fast:     [x]" in _rendered_text(screen_runtime))
    text = _rendered_text(screen_runtime)

    assert "claude-sonnet-4.5" not in text
    assert "Smart:    [x]" in text
    assert "Planner:  [x]" in text


def test_stream_slot_commits_once_and_restores_status(screen_runtime):
    screen_runtime.screen.set_status("STATUS-ONLY")
    _wait_until(lambda: "STATUS-ONLY" in _rendered_text(screen_runtime))

    screen_runtime.screen.set_stream("PARTIAL-STREAM")
    _wait_until(lambda: "PARTIAL-STREAM" in _rendered_text(screen_runtime))
    assert "STATUS-ONLY" not in _rendered_text(screen_runtime)

    screen_runtime.screen.set_stream("FINAL-STREAM")
    screen_runtime.screen.commit_stream()
    screen_runtime.screen.commit_stream()
    _wait_until(
        lambda: (
            "FINAL-STREAM" in _rendered_text(screen_runtime)
            and "STATUS-ONLY" in _rendered_text(screen_runtime)
        )
    )
    text = _rendered_text(screen_runtime)

    assert text.count("FINAL-STREAM") == 1
    assert "PARTIAL-STREAM" not in text
    assert "STATUS-ONLY" in text


def test_dynamic_control_cursor_uses_same_snapshot_as_rendered_content(
    screen_runtime,
):
    screen = screen_runtime.screen
    screen.suspend()

    screen._get_transcript()
    transcript_cursor = screen._transcript_cursor()
    screen._append_formatted([("", "new transcript line\n")])
    assert screen._transcript_cursor() == transcript_cursor
    screen._get_transcript()
    assert screen._transcript_cursor().y == 1

    screen.set_status("one line")
    screen._get_transient()
    transient_cursor = screen._transient_cursor()
    screen.set_status("first\nsecond")
    assert screen._transient_cursor() == transient_cursor
    screen._get_transient()
    assert screen._transient_cursor().y == 1


def test_split_ansi_does_not_corrupt_plain_cursor_state(screen_runtime):
    screen = screen_runtime.screen
    screen.suspend()

    screen._append_output("\x1b[", "stdout")
    screen._append_output("31mcolored\x1b[0m\n", "stdout")

    assert screen._transcript_plain == "colored\n"
    assert screen._last_transcript_line_locked() == 1
    fragments = screen._get_transcript()
    assert "".join(fragment[1] for fragment in fragments) == "colored\n"
    assert fragments[0][0] == "ansired"


def test_transcript_memory_and_entry_count_are_bounded(
    screen_runtime,
    monkeypatch,
):
    screen = screen_runtime.screen
    screen.suspend()
    monkeypatch.setattr(interactive_screen_module, "_MAX_TRANSCRIPT_CHARS", 240)
    monkeypatch.setattr(interactive_screen_module, "_TRANSCRIPT_RETAIN_CHARS", 180)
    monkeypatch.setattr(interactive_screen_module, "_MAX_TRANSCRIPT_ENTRIES", 12)

    for index in range(40):
        screen._append_formatted([("", f"bounded line {index:03d}\n")])

    assert len(screen._transcript_plain) <= 240
    assert len(screen._transcript_entries) <= 12
    assert screen._transcript_plain.startswith(
        interactive_screen_module._TRANSCRIPT_TRUNCATION_MARKER
    )
    assert "bounded line 039" in screen._transcript_plain
    assert "bounded line 000" not in screen._transcript_plain


def test_split_osc_is_hidden_without_delaying_following_text(screen_runtime):
    screen = screen_runtime.screen
    screen.suspend()

    screen._append_output("\x1b]0;hidden title", "stdout")
    screen._append_output("\x1b", "stdout")
    screen._append_output("\\visible text\n", "stdout")

    assert screen._transcript_plain == "visible text\n"
    rendered = "".join(fragment[1] for fragment in screen._get_transcript())
    assert rendered == "visible text\n"


def test_invisible_ansi_storage_and_small_write_latency_are_bounded(
    screen_runtime,
    monkeypatch,
):
    screen = screen_runtime.screen
    screen.suspend()
    monkeypatch.setattr(interactive_screen_module, "_MAX_TRANSCRIPT_CHARS", 240)
    monkeypatch.setattr(interactive_screen_module, "_TRANSCRIPT_RETAIN_CHARS", 180)

    invisible = "\x1b]0;" + ("x" * 40) + "\x07"
    for _ in range(40):
        screen._append_output(invisible, "stdout")

    assert screen._transcript_storage_chars <= 240
    assert len(screen._transcript_plain) <= 240
    assert "x" * 40 not in screen._transcript_plain

    monkeypatch.setattr(interactive_screen_module, "_MAX_TRANSCRIPT_CHARS", 500_000)
    monkeypatch.setattr(
        interactive_screen_module,
        "_TRANSCRIPT_RETAIN_CHARS",
        400_000,
    )
    started = time.monotonic()
    for index in range(5_000):
        screen._append_output(
            f"small write {index:05d} " + ("x" * 88) + "\n",
            "stdout",
        )
    elapsed = time.monotonic() - started

    assert elapsed < 3.0
    assert screen._transcript_storage_chars <= 500_000


def test_suspend_resume_restores_streams_and_prompt_session(screen_runtime):
    screen_runtime.screen.suspend()

    assert screen_runtime.screen.is_running is False
    assert sys.stdout is screen_runtime.original_stdout
    assert sys.stderr is screen_runtime.original_stderr
    assert screen_runtime.session.app is screen_runtime.original_app
    assert screen_runtime.session.layout is screen_runtime.original_layout

    screen_runtime.screen.resume()
    _wait_until(lambda: screen_runtime.screen.is_running)

    assert sys.stdout is not screen_runtime.original_stdout
    assert sys.stderr is not screen_runtime.original_stderr
    assert screen_runtime.session.app is screen_runtime.screen.app
    assert screen_runtime.session.layout is screen_runtime.screen.layout


@pytest.mark.parametrize(
    ("columns", "rows"),
    [
        (86, 40),
        (120, 30),
    ],
)
def test_resize_hides_banner_atomically_and_restores_it(
    screen_runtime,
    columns,
    rows,
):
    screen_runtime.output.columns = columns
    screen_runtime.output.rows = rows
    screen_runtime.screen.invalidate()
    _wait_until(lambda: not any("╭" in line for line in _rendered_lines(screen_runtime)))

    narrow_lines = _rendered_lines(screen_runtime)
    assert not any("▀" in line or "╭" in line for line in narrow_lines)
    assert any("❯" in line for line in narrow_lines)

    screen_runtime.output.columns = 120
    screen_runtime.output.rows = 40
    screen_runtime.screen.invalidate()
    _wait_until(lambda: _rendered_lines(screen_runtime)[0].startswith("╭"))

    restored = _rendered_lines(screen_runtime)
    assert restored[0].startswith("╭")
    assert restored[16].startswith("╰")


def test_page_keys_scroll_transcript_without_recalling_input_history(
    screen_runtime,
):
    screen_runtime.screen._append_output(
        "".join(f"scroll line {index:03d}\n" for index in range(100)),
        "stdout",
    )
    _wait_until(lambda: "scroll line 099" in _rendered_text(screen_runtime))

    screen_runtime.input.send_text("\x1b[5~")
    _wait_until(lambda: not screen_runtime.screen._follow_tail)
    _wait_until(lambda: "scroll line 099" not in _rendered_text(screen_runtime))

    assert screen_runtime.session.default_buffer.text == ""

    screen_runtime.input.send_text("\x1b[6~")
    _wait_until(lambda: screen_runtime.screen._follow_tail)
    _wait_until(lambda: "scroll line 099" in _rendered_text(screen_runtime))

    assert screen_runtime.session.default_buffer.text == ""
