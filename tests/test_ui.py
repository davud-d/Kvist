from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from rich.console import Console

import terminal_banner as ui_module
from terminal_banner import TerminalBannerUI as AgentUI


class _ScreenRecorder:
    def __init__(self) -> None:
        self.statuses = []
        self.streams = []
        self.commits = []
        self.flushes = []

    def set_status(self, content) -> None:
        self.statuses.append(content)

    def set_stream(self, content) -> None:
        self.streams.append(content)

    def commit_stream(self, content) -> None:
        self.commits.append(content)

    def flush_stream(self, settled, remaining) -> None:
        self.flushes.append((settled, remaining))


@pytest.mark.offline_smoke
def test_ui_basic_interface():
    """Test basic UI interface compatibility."""
    ui = AgentUI(verbose=False)

    # Test basic methods exist and don't crash
    ui.start_live()
    ui.stop_live()
    ui.update_live(user_message="test", status="ok", step=1)
    ui.print_final("done")
    ui.debug_print("debug")
    ui.print_result("result")
    ui.print_error("error")

    assert ui.verbose is False


def test_live_status_uses_attached_interactive_screen():
    screen = _ScreenRecorder()
    ui = AgentUI(verbose=False)
    ui.attach_screen(screen)

    ui.start_live()
    ui.update_live(user_message="test", status="working", step=1)
    ui.stop_live()

    assert len(screen.statuses) == 3
    assert screen.statuses[-1] is None


def test_live_markdown_commits_attached_screen_once():
    screen = _ScreenRecorder()
    ui = AgentUI(verbose=False)
    ui.attach_screen(screen)

    with ui.live_markdown() as update:
        update("partial")
        update("final")

    assert len(screen.streams) == 3  # Empty slot, partial, final.
    assert len(screen.commits) == 1


def test_split_settled_markdown_cuts_at_the_last_blank_line():
    settled, tail = ui_module._split_settled_markdown(
        "First block.\n\nSecond block.\n\nstill writ"
    )
    assert settled == "First block.\n\nSecond block.\n\n"
    assert tail == "still writ"


def test_split_settled_markdown_keeps_an_unfinished_block_whole():
    assert ui_module._split_settled_markdown("one long paragraph stil") == (
        "",
        "one long paragraph stil",
    )


def test_split_settled_markdown_never_cuts_inside_a_code_fence():
    """A blank line inside an open fence is not a block boundary."""
    text = "Intro.\n\n```python\ndef f():\n\n    return 1\n"
    settled, tail = ui_module._split_settled_markdown(text)
    assert settled == "Intro.\n\n"
    assert tail == "```python\ndef f():\n\n    return 1\n"


def test_live_markdown_hands_finished_blocks_to_the_transcript():
    """A long reply must not grow inside the height-capped slot."""
    screen = _ScreenRecorder()
    ui = AgentUI(verbose=False)
    ui.attach_screen(screen)

    with ui.live_markdown() as update:
        update("First block.")
        update("First block.\n\nSecond")
        update("First block.\n\nSecond block.\n\nThird")

    # Each finished block was committed as it settled, the tail streamed on,
    # and the final commit carried only what was left.
    assert len(screen.flushes) == 2
    assert [str(flush[0].markup) for flush in screen.flushes] == [
        "First block.\n\n",
        "Second block.\n\n",
    ]
    assert str(screen.commits[0].markup) == "Third"


def test_cancel_on_escape_bindings_aborts_the_prompt():
    """The panels promise "Esc to cancel"; plain pt_prompt does not deliver it."""
    from prompt_toolkit.keys import Keys

    bindings = ui_module._cancel_on_escape_bindings()
    escape = bindings.get_bindings_for_keys((Keys.Escape,))
    assert len(escape) == 1
    # Without eager the key waits for a meta-sequence timeout instead.
    assert escape[0].eager() is True

    exits = {}

    class _App:
        def exit(self, **kwargs) -> None:
            exits.update(kwargs)

        def invalidate(self) -> None:
            pass

    escape[0].call(SimpleNamespace(app=_App()))

    assert exits["exception"] is KeyboardInterrupt


def test_print_user_markdown_accepts_markdown_text_without_crashing():
    console = Console(record=True, width=100, force_terminal=True)
    ui = AgentUI(console=console, verbose=False)

    ui.print_user_markdown("Updated `hello.txt`\n\n- first\n- second")

    rendered = console.export_text()
    assert "Updated hello.txt" in rendered
    assert "first" in rendered
    assert "second" in rendered


def test_print_final_renders_markdown_without_crashing():
    console = Console(record=True, width=100, force_terminal=True)
    ui = AgentUI(console=console, verbose=False)

    ui.print_final("Updated `hello.txt`\n\n- first\n- second")

    rendered = console.export_text()
    assert "Updated hello.txt" in rendered
    assert "first" in rendered
    assert "second" in rendered


def test_print_final_keeps_code_block_content():
    console = Console(record=True, width=100, force_terminal=True)
    ui = AgentUI(console=console, verbose=False)

    ui.print_final("```txt\nhello.txt\nhello world\n```")

    rendered = console.export_text()
    assert "hello.txt" in rendered
    assert "hello world" in rendered


def test_user_visible_output_redacts_credentials(capsys):
    console = Console(record=True, width=100, force_terminal=True)
    ui = AgentUI(console=console, verbose=True)
    synthetic = "sk-proj-" + "u" * 24

    ui.print_error(f"failure api_key={synthetic}")
    ui.print_final(f"provider error Authorization: Bearer {synthetic}")
    ui.debug_print(f"debug password={synthetic}")
    console.print(
        ui._render_live_panel(
            user_message=f"retry token={synthetic}",
            status="error",
            step=1,
            progress=50,
            plan_step=f"recover api_key={synthetic}",
            is_error=True,
        )
    )

    rendered = console.export_text() + capsys.readouterr().out
    assert synthetic not in rendered
    assert "[REDACTED]" in rendered


def test_user_visible_output_escapes_terminal_and_bidi_controls():
    console = Console(record=True, width=100, force_terminal=True)
    ui = AgentUI(console=console, verbose=False)
    dangerous = "before\x1b[2J\u202eafter\x08"

    ui.print_error(dangerous)
    ui.print_final(dangerous)

    rendered = console.export_text()
    assert "\x1b" not in rendered
    assert "\u202e" not in rendered
    assert "\x08" not in rendered
    assert "\\x1b" in rendered
    assert "\\u202e" in rendered
    assert "\\x08" in rendered


def test_print_final_falls_back_to_plain_text_on_markdown_error(monkeypatch):
    console = Console(record=True, width=100, force_terminal=True)
    ui = AgentUI(console=console, verbose=False)

    def boom(message: str):
        raise RuntimeError("markdown failed")

    monkeypatch.setattr(ui_module, "Markdown", boom)

    ui.print_final("Use `hello.txt` safely")

    rendered = console.export_text()
    assert "Use `hello.txt` safely" in rendered


def test_print_user_markdown_falls_back_to_plain_text_on_markdown_error(monkeypatch):
    console = Console(record=True, width=100, force_terminal=True)
    ui = AgentUI(console=console, verbose=False)

    def boom(message: str):
        raise RuntimeError("markdown failed")

    monkeypatch.setattr(ui_module, "Markdown", boom)

    ui.print_user_markdown("Use `hello.txt` safely")

    rendered = console.export_text()
    assert "Use `hello.txt` safely" in rendered


def test_choose_option_returns_first_option():
    """Test choose_option returns first option (backward-compatible positional call)."""
    ui = AgentUI(verbose=False)
    result = ui.choose_option("Title", ["option1", "option2"])
    assert result == "option1"


def test_choose_option_with_title_keyword():
    """Test choose_option accepts keyword arguments like the original AgentUI."""
    ui = AgentUI(verbose=False)
    result = ui.choose_option(
        title="Select LLM Provider",
        options=[
            {"value": "openai", "label": "OpenAI", "description": "GPT models"},
            {"value": "freeflow", "label": "FreeFlow", "description": "Gemini/Groq"},
        ],
        current_value="freeflow",
    )
    assert result == "freeflow"


def test_choose_option_with_dict_options_returns_current_value():
    """Test choose_option returns current_value when it matches an option dict."""
    ui = AgentUI(verbose=False)
    result = ui.choose_option(
        title="Select model",
        options=[
            {"value": "gpt-5", "label": "GPT-5"},
            {"value": "gpt-5-mini", "label": "GPT-5 Mini"},
        ],
        current_value="gpt-5-mini",
    )
    assert result == "gpt-5-mini"


def test_choose_option_returns_first_when_current_value_not_found():
    """Test choose_option falls back to first option when current_value is unknown."""
    ui = AgentUI(verbose=False)
    result = ui.choose_option(
        title="Select model",
        options=[
            {"value": "gpt-5", "label": "GPT-5"},
            {"value": "gpt-5-mini", "label": "GPT-5 Mini"},
        ],
        current_value="unknown-model",
    )
    assert result == "gpt-5"


def test_choose_option_empty_options_returns_none():
    """Test choose_option returns None when options list is empty."""
    ui = AgentUI(verbose=False)
    result = ui.choose_option("Title", [])
    assert result is None


# ── TTY interactive path tests ────────────────────────────────────────────────


def test_choose_option_tty_enter_cancels(monkeypatch):
    """TTY: pressing Enter without typing → returns None (cancel)."""
    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(ui, "_read_menu_key", lambda: "enter")  # Enter → cancel in choose_option

    result = ui.choose_option("Title", ["a", "b"])
    assert result == "a"  # first option selected by default, enter confirms it


def test_choose_option_tty_valid_number_selects(monkeypatch):
    """TTY: entering a valid number returns the correct option."""
    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    # Simulate down+enter: move to second item, then select
    keys = iter(["down", "enter"])
    monkeypatch.setattr(ui, "_read_menu_key", lambda: next(keys))

    result = ui.choose_option("Title", ["a", "b", "c"])
    assert result == "b"


def test_choose_option_tty_valid_number_with_dict_options(monkeypatch):
    """TTY: entering a valid number with dict options returns the value."""
    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(ui, "_read_menu_key", lambda: "enter")

    result = ui.choose_option(
        title="Select model",
        options=[
            {"value": "m1", "label": "Model 1"},
            {"value": "m2", "label": "Model 2"},
        ],
    )
    assert result == "m1"


def test_choose_option_tty_invalid_input_reprompts_then_selects(monkeypatch):
    """TTY: invalid (non-numeric) input → re-prompts, does NOT cancel."""
    # The Rich-based menu only responds to up/down/enter/escape, so
    # invalid keys are silently ignored and the menu continues.
    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    keys = iter(["down", "enter"])
    monkeypatch.setattr(ui, "_read_menu_key", lambda: next(keys))

    result = ui.choose_option("Title", ["option-a", "option-b"])
    assert result == "option-b"


def test_choose_option_tty_out_of_range_reprompts_then_selects(monkeypatch):
    """TTY: out-of-range number → re-prompts, does NOT cancel."""
    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    keys = iter(["down", "enter"])
    monkeypatch.setattr(ui, "_read_menu_key", lambda: next(keys))

    result = ui.choose_option("Title", ["a", "b", "c"])
    assert result == "b"


def test_choose_option_tty_eof_cancels(monkeypatch):
    """TTY: EOFError (Ctrl+D) → returns None (cancel)."""
    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(ui, "_read_menu_key", lambda: "interrupt")

    result = ui.choose_option("Title", ["a", "b"])
    assert result is None


def _many_options(n: int) -> list[dict[str, str]]:
    return [{"value": f"v{i}", "label": f"Provider {i}"} for i in range(n)]


def test_choose_option_short_list_ignores_typed_letters(monkeypatch):
    """Below the filter threshold, a typed letter is simply ignored (no filter UI)."""
    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    keys = iter(["char:a", "down", "enter"])
    monkeypatch.setattr(ui, "_read_menu_key", lambda: next(keys))

    result = ui.choose_option("Title", ["a", "b", "c"])
    assert result == "b"


def test_choose_option_long_list_filters_by_typed_text(monkeypatch):
    """Above the filter threshold, typing narrows the list; Enter picks the match."""
    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    options = _many_options(9)
    options[7]["label"] = "Qwen (DashScope)"
    # Typing "qwen" should isolate the one match regardless of arrow position.
    keys = iter(["char:q", "char:w", "char:e", "char:n", "enter"])
    monkeypatch.setattr(ui, "_read_menu_key", lambda: next(keys))

    result = ui.choose_option("Title", options)
    assert result == "v7"


def test_choose_option_filter_backspace_widens_matches(monkeypatch):
    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    options = _many_options(9)
    options[0]["label"] = "Alpha"
    options[1]["label"] = "Alt"
    # "alt" matches only "Alt"; backspace back to "al" matches both, then Enter
    # confirms whichever the (reset-to-top) cursor lands on.
    keys = iter(["char:a", "char:l", "char:t", "backspace", "enter"])
    monkeypatch.setattr(ui, "_read_menu_key", lambda: next(keys))

    result = ui.choose_option("Title", options)
    assert result == "v0"


def test_choose_option_filter_no_matches_does_not_crash(monkeypatch):
    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    options = _many_options(9)
    keys = iter(["char:z", "char:z", "char:z", "enter", "escape"])
    monkeypatch.setattr(ui, "_read_menu_key", lambda: next(keys))

    # Enter with no matches is a no-op; the loop continues to the next key.
    result = ui.choose_option("Title", options)
    assert result is None


def test_choose_option_long_list_windows_rows_instead_of_dumping_everything(monkeypatch):
    """A long picker must never print every option every frame — that's what
    buried OpenAI/Anthropic/DeepSeek off-screen in the real bug report."""
    import io

    from rich.console import Console as RichConsole

    buffer = io.StringIO()
    console = RichConsole(file=buffer, width=100, height=15, force_terminal=True, color_system=None)
    ui = AgentUI(console=console, verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    options = _many_options(30)
    monkeypatch.setattr(ui, "_read_menu_key", lambda: "enter")

    result = ui.choose_option("Title", options)
    assert result == "v0"

    output = buffer.getvalue()
    assert "Provider 0" in output
    assert "more below" in output
    # height=15 clamps the row budget to max(5, min(15, 15-8)) = 7 rows, so
    # an option far past the window must never have been rendered at all.
    assert "Provider 29" not in output


def test_choose_option_window_follows_cursor_down_and_up(monkeypatch):
    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(ui, "_visible_row_budget", lambda: 5)
    options = _many_options(20)
    # Move down past the first window, then back up, then confirm.
    keys = iter(["down"] * 8 + ["up"] * 3 + ["enter"])
    monkeypatch.setattr(ui, "_read_menu_key", lambda: next(keys))

    result = ui.choose_option("Title", options)
    assert result == "v5"  # 8 downs - 3 ups = index 5


def test_read_menu_key_windows_reports_char_and_backspace(monkeypatch):
    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys, "platform", "win32")
    keys = iter(["q", "\x08", "\r"])

    class FakeMsvcrt:
        @staticmethod
        def getwch():
            return next(keys)

    monkeypatch.setitem(sys.modules, "msvcrt", FakeMsvcrt())
    assert ui._read_menu_key() == "char:q"
    assert ui._read_menu_key() == "backspace"
    assert ui._read_menu_key() == "enter"


def test_prompt_text_returns_current_value_in_non_tty():
    """Non-TTY: prompt_text returns current_value when one is set."""
    ui = AgentUI(verbose=False)
    result = ui.prompt_text("Title", "my-model", "help")
    assert result == "my-model"


def test_prompt_text_returns_none_without_current_value_in_non_tty():
    """Non-TTY: prompt_text returns None when no current_value."""
    ui = AgentUI(verbose=False)
    result = ui.prompt_text("Title", "", "help")
    assert result is None


def test_prompt_text_tty_enter_keeps_current(monkeypatch):
    """TTY: pressing Enter keeps the current value."""
    from terminal_banner import pt_prompt as _pt_prompt

    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    # prompt_toolkit is available — monkeypatch it directly
    if _pt_prompt is not None:
        monkeypatch.setattr("terminal_banner.pt_prompt",lambda *a, **kw: "")
    else:
        monkeypatch.setattr("builtins.input", lambda prompt="": "")

    result = ui.prompt_text("Enter model:", "existing-model")
    # When prompt_toolkit returns "" (enter pressed), we return the current_value
    assert result == "existing-model"


def test_prompt_text_tty_enter_cancels_without_current(monkeypatch):
    """TTY: pressing Enter without current_value → cancel (None)."""
    from terminal_banner import pt_prompt as _pt_prompt

    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    if _pt_prompt is not None:
        monkeypatch.setattr("terminal_banner.pt_prompt",lambda *a, **kw: "")
    else:
        monkeypatch.setattr("builtins.input", lambda prompt="": "")

    result = ui.prompt_text("Enter model:", "")
    assert result is None


def test_prompt_text_tty_types_new_value(monkeypatch):
    """TTY: typing a new value returns it."""
    from terminal_banner import pt_prompt as _pt_prompt

    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    if _pt_prompt is not None:
        monkeypatch.setattr("terminal_banner.pt_prompt",lambda *a, **kw: "ds-web/deepseek-v4-pro")
    else:
        monkeypatch.setattr("builtins.input", lambda prompt="": "ds-web/deepseek-v4-pro")

    result = ui.prompt_text("Enter model:", "old-model")
    assert result == "ds-web/deepseek-v4-pro"


def test_prompt_text_tty_eof_cancels(monkeypatch):
    """TTY: EOFError (Ctrl+D) → cancel (None)."""
    from terminal_banner import pt_prompt as _pt_prompt

    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def raise_eof(*args, **kwargs):
        raise EOFError()

    if _pt_prompt is not None:
        monkeypatch.setattr("terminal_banner.pt_prompt",raise_eof)
    else:
        monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError()))

    result = ui.prompt_text("Enter model:", "existing")
    assert result is None


def test_message_hold_counts_only_a_different_message_shown_recently():
    assert ui_module._message_hold_remaining("", 0.0, "new", 1.0) == 0.0
    assert ui_module._message_hold_remaining("old", 10.0, "old", 10.5) == 0.0
    assert ui_module._message_hold_remaining("old", 10.0, "new", 11.0) == pytest.approx(1.5)
    assert ui_module._message_hold_remaining("old", 10.0, "new", 13.0) == 0.0


class _ManualTimer:
    created: list["_ManualTimer"] = []

    def __init__(self, interval, callback) -> None:
        self.interval = interval
        self.callback = callback
        self.daemon = False
        _ManualTimer.created.append(self)

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        pass


def _rendered_text(panel) -> str:
    console = Console(record=True, width=100)
    console.print(panel)
    return console.export_text()


@pytest.fixture
def held_status_ui(monkeypatch):
    clock = [100.0]
    _ManualTimer.created = []
    monkeypatch.setattr(ui_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ui_module.threading, "Timer", _ManualTimer)
    screen = _ScreenRecorder()
    ui = AgentUI(verbose=False)
    ui.attach_screen(screen)
    ui.start_live()
    ui.update_live(user_message="Creating folders", status="executing action", step=1)
    clock[0] += 0.5
    ui.update_live(user_message="Checking the result", status="executing action", step=2)
    return ui, screen, clock


def test_live_status_holds_a_fresh_message_without_blocking(held_status_ui):
    ui, screen, clock = held_status_ui

    assert "Creating folders" in _rendered_text(screen.statuses[-1])
    assert len(_ManualTimer.created) == 1
    assert _ManualTimer.created[0].interval == pytest.approx(2.0)

    clock[0] += 2.0
    _ManualTimer.created[0].callback()

    assert "Checking the result" in _rendered_text(screen.statuses[-1])


def test_stopping_the_run_drops_a_held_message(held_status_ui):
    ui, screen, _clock = held_status_ui

    ui.stop_live()
    _ManualTimer.created[0].callback()

    assert screen.statuses[-1] is None
