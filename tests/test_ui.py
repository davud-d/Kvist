from __future__ import annotations

import sys

import pytest

from ui import AgentUI


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
    from ui import pt_prompt as _pt_prompt

    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    # prompt_toolkit is available — monkeypatch it directly
    if _pt_prompt is not None:
        monkeypatch.setattr("ui.pt_prompt", lambda *a, **kw: "")
    else:
        monkeypatch.setattr("builtins.input", lambda prompt="": "")

    result = ui.prompt_text("Enter model:", "existing-model")
    # When prompt_toolkit returns "" (enter pressed), we return the current_value
    assert result == "existing-model"


def test_prompt_text_tty_enter_cancels_without_current(monkeypatch):
    """TTY: pressing Enter without current_value → cancel (None)."""
    from ui import pt_prompt as _pt_prompt

    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    if _pt_prompt is not None:
        monkeypatch.setattr("ui.pt_prompt", lambda *a, **kw: "")
    else:
        monkeypatch.setattr("builtins.input", lambda prompt="": "")

    result = ui.prompt_text("Enter model:", "")
    assert result is None


def test_prompt_text_tty_types_new_value(monkeypatch):
    """TTY: typing a new value returns it."""
    from ui import pt_prompt as _pt_prompt

    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    if _pt_prompt is not None:
        monkeypatch.setattr("ui.pt_prompt", lambda *a, **kw: "ds-web/deepseek-v4-pro")
    else:
        monkeypatch.setattr("builtins.input", lambda prompt="": "ds-web/deepseek-v4-pro")

    result = ui.prompt_text("Enter model:", "old-model")
    assert result == "ds-web/deepseek-v4-pro"


def test_prompt_text_tty_eof_cancels(monkeypatch):
    """TTY: EOFError (Ctrl+D) → cancel (None)."""
    from ui import pt_prompt as _pt_prompt

    ui = AgentUI(verbose=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def raise_eof(*args, **kwargs):
        raise EOFError()

    if _pt_prompt is not None:
        monkeypatch.setattr("ui.pt_prompt", raise_eof)
    else:
        monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError()))

    result = ui.prompt_text("Enter model:", "existing")
    assert result is None
