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
    return [
        "".join(
            rendered.data_buffer[row][column].char
            for column in range(runtime.output.columns)
        ).rstrip()
        for row in range(runtime.output.rows)
    ]


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
    _wait_until(lambda: "FINAL-STREAM" in _rendered_text(screen_runtime))
    text = _rendered_text(screen_runtime)

    assert text.count("FINAL-STREAM") == 1
    assert "PARTIAL-STREAM" not in text
    assert "STATUS-ONLY" in text


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
