from __future__ import annotations

from types import SimpleNamespace

import pytest
from prompt_toolkit.auto_suggest import Suggestion
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.shortcuts import CompleteStyle

from kernel import main
from kernel.slash_commands import (
    SLASH_COMMANDS,
    SLASH_COMMAND_LEXER,
    SLASH_COMMAND_MENU_HEIGHT,
    SLASH_COMMAND_PROMPT,
    SlashCommandAutoSuggest,
    SlashCommandCompleter,
    accept_inline_suggestion,
    has_slash_command_completion,
    move_cursor_left,
    normalize_slash_command,
)


def _completion_rows(text: str) -> list[tuple[str, str]]:
    completions = SlashCommandCompleter().get_completions(
        Document(text=text, cursor_position=len(text)),
        complete_event=None,
    )
    return [
        (completion.text, completion.display_meta_text)
        for completion in completions
    ]


def test_empty_slash_lists_every_canonical_command_with_description():
    rows = _completion_rows("/")

    assert [command for command, _description in rows] == [
        f"/{command.name}" for command in SLASH_COMMANDS
    ]
    assert all(description for _command, description in rows)


def test_completion_filters_case_insensitively():
    assert _completion_rows("/m") == [
        ("/models", "Select fast and smart models")
    ]
    assert _completion_rows("/M") == [
        ("/models", "Select fast and smart models")
    ]
    assert _completion_rows("/ver") == [
        ("/verbose", "Enable or disable verbose runtime output")
    ]
    assert _completion_rows("/str") == [
        ("/strime", "Toggle streaming for the active custom provider")
    ]


def test_completion_exposes_supported_aliases():
    assert _completion_rows("/ab")[0][0] == "/about"
    assert _completion_rows("/i")[0][0] == "/info"
    assert _completion_rows("/exi")[0][0] == "/exit"
    assert _completion_rows("/q")[0][0] == "/quit"


def test_completion_is_hidden_for_normal_text_and_command_arguments():
    assert _completion_rows("hello") == []
    assert _completion_rows("/new argument") == []


def test_menu_space_is_reserved_only_while_matching_slash_commands():
    assert has_slash_command_completion("/") is True
    assert has_slash_command_completion("/mod") is True
    assert has_slash_command_completion("/verb") is True
    assert has_slash_command_completion("/verbose") is False
    assert has_slash_command_completion("/stri") is True
    assert has_slash_command_completion("/strime") is False
    assert has_slash_command_completion("ordinary text") is False
    assert has_slash_command_completion("/unknown") is False
    assert has_slash_command_completion("/new argument") is False


def test_left_arrow_keeps_cursor_after_prefix_while_slash_palette_is_open():
    slash_buffer = Buffer(document=Document(text="/", cursor_position=0))

    move_cursor_left(slash_buffer)

    assert slash_buffer.cursor_position == 1


def test_left_arrow_keeps_normal_cursor_navigation_outside_slash_palette():
    text_buffer = Buffer(document=Document(text="hello", cursor_position=5))

    move_cursor_left(text_buffer)

    assert text_buffer.cursor_position == 4


def test_verbose_inline_suggestion_tracks_current_state():
    suggester = SlashCommandAutoSuggest(verbose_enabled=False)
    buffer = Buffer()

    suggestion = suggester.get_suggestion(
        buffer,
        Document(text="/verbose", cursor_position=len("/verbose")),
    )
    assert suggestion is not None
    assert suggestion.text == " <true>"

    suggester.set_verbose(True)
    suggestion = suggester.get_suggestion(
        buffer,
        Document(text="/verbose ", cursor_position=len("/verbose ")),
    )
    assert suggestion is not None
    assert suggestion.text == "<false>"


def test_tab_accepts_verbose_inline_suggestion_with_angle_brackets():
    buffer = Buffer(
        document=Document(text="/verbose", cursor_position=len("/verbose"))
    )
    buffer.suggestion = Suggestion(" <true>")

    assert accept_inline_suggestion(buffer) is True
    assert buffer.text == "/verbose <true>"


def test_strime_inline_suggestion_tracks_custom_provider_state():
    suggester = SlashCommandAutoSuggest()
    buffer = Buffer()

    suggester.set_streaming(False, available=True)
    suggestion = suggester.get_suggestion(
        buffer,
        Document(text="/strime", cursor_position=len("/strime")),
    )
    assert suggestion is not None
    assert suggestion.text == " <true>"

    suggester.set_streaming(True, available=True)
    suggestion = suggester.get_suggestion(
        buffer,
        Document(text="/strime ", cursor_position=len("/strime ")),
    )
    assert suggestion is not None
    assert suggestion.text == "<false>"

    suggester.set_streaming(False, available=False)
    assert (
        suggester.get_suggestion(
            buffer,
            Document(text="/strime", cursor_position=len("/strime")),
        )
        is None
    )


def test_tab_accepts_strime_inline_suggestion_with_angle_brackets():
    buffer = Buffer(
        document=Document(text="/strime", cursor_position=len("/strime"))
    )
    buffer.suggestion = Suggestion(" <false>")

    assert accept_inline_suggestion(buffer) is True
    assert buffer.text == "/strime <false>"


def test_slash_lexer_highlights_command_and_subdues_argument():
    get_line = SLASH_COMMAND_LEXER.lex_document(
        Document(text="/verbose <true>", cursor_position=len("/verbose <true>"))
    )

    assert get_line(0) == [
        ("class:slash-command.input", "/verbose"),
        ("class:slash-command.argument", " <true>"),
    ]


def test_normalizer_maps_empty_slash_and_aliases_to_canonical_commands():
    assert normalize_slash_command("/") == "/help"
    assert normalize_slash_command("/ABOUT") == "/help"
    assert normalize_slash_command("/info") == "/help"
    assert normalize_slash_command("/exit") == "/bye"
    assert normalize_slash_command("/QUIT") == "/bye"


def test_normalizer_preserves_unknown_commands_and_arguments():
    assert normalize_slash_command("/unknown") == "/unknown"
    assert normalize_slash_command("/new later") == "/new later"
    assert normalize_slash_command(" ordinary task ") == "ordinary task"
    assert normalize_slash_command("/verbose <TRUE>") == "/verbose <TRUE>"
    assert normalize_slash_command("/strime <false>") == "/strime <false>"


def test_verbose_command_parser_accepts_toggle_and_bracketed_values():
    assert main.parse_verbose_value("/verbose", current=False) is True
    assert main.parse_verbose_value("/verbose", current=True) is False
    assert main.parse_verbose_value("/verbose <true>", current=False) is True
    assert main.parse_verbose_value("/verbose false", current=True) is False
    assert main.parse_verbose_value("/verbose maybe", current=False) is None


def test_strime_command_parser_accepts_toggle_and_bracketed_values():
    assert main.parse_strime_value("/strime", current=False) is True
    assert main.parse_strime_value("/strime", current=True) is False
    assert main.parse_strime_value("/strime <true>", current=False) is True
    assert main.parse_strime_value("/strime false", current=True) is False
    assert main.parse_strime_value("/strime maybe", current=False) is None


def test_apply_verbose_mode_updates_active_runtime_objects():
    agent = SimpleNamespace(
        config=SimpleNamespace(verbose=False),
        _ui=SimpleNamespace(verbose=False),
        client=SimpleNamespace(_verbose=False),
    )

    try:
        main.apply_verbose_mode(agent, True)

        assert agent.config.verbose is True
        assert agent._ui.verbose is True
        assert agent.client._verbose is True
        assert main.slash_command_auto_suggest.verbose_enabled is True
    finally:
        main.slash_command_auto_suggest.set_verbose(False)


def test_apply_strime_mode_persists_and_updates_active_custom_provider(monkeypatch):
    saved = {}
    provider_info = {
        "label": "Local",
        "protocol": "openai",
        "base_url": "http://localhost:8000/v1",
        "api_key": "",
        "models": {"fast": "local", "smart": "local"},
        "streaming": False,
    }
    monkeypatch.setattr(
        main,
        "get_custom_provider_by_name",
        lambda name: dict(provider_info) if name == "local" else None,
    )
    monkeypatch.setattr(main, "save_custom_provider", saved.__setitem__)
    agent = SimpleNamespace(
        config=SimpleNamespace(provider="custom:local", stream=False)
    )

    try:
        assert main.apply_strime_mode(agent, True) is True

        assert agent.config.stream is True
        assert saved["local"]["streaming"] is True
        assert main.slash_command_auto_suggest.streaming_available is True
        assert main.slash_command_auto_suggest.streaming_enabled is True
    finally:
        main.slash_command_auto_suggest.set_streaming(False, available=False)


def test_apply_strime_mode_rejects_builtin_provider():
    agent = SimpleNamespace(
        config=SimpleNamespace(provider="openai", stream=True)
    )

    assert main.apply_strime_mode(agent, False) is False
    assert agent.config.stream is True


def test_ink_model_reference_matches_frontend_commands(monkeypatch):
    import re
    from pathlib import Path
    from kernel.slash_commands import slash_commands_context

    monkeypatch.setenv("KVIST_COMMAND_FRONTEND", "ink")
    context = slash_commands_context()
    source = (Path(__file__).resolve().parents[2] / "frontends/tui/ink/commands.ts").read_text()
    frontend_names = set(re.findall(r'name: "([\w-]+)"', source))
    rows = context.split("\n\n")[-1]
    reference_names = {
        name for row in rows.splitlines()
        for name in re.findall(r"/([\w-]+)", row.split(":", 1)[0])
    }
    assert reference_names == frontend_names | {"bye", "exit"}
    for unavailable in ("/status", "/permissions", "/commands", "/diff", "/bug"):
        assert unavailable not in context


def test_prompt_session_uses_vertical_styled_command_menu():
    try:
        session = main._get_session()
    except Exception as error:  # host has no attached console (e.g. CI service process)
        pytest.skip(f"PromptSession requires a real console: {error}")
    assert session.complete_style is CompleteStyle.COLUMN
    assert session.reserve_space_for_menu == SLASH_COMMAND_MENU_HEIGHT + 1
    assert session.style is not None

    prompt = to_formatted_text(SLASH_COMMAND_PROMPT)
    assert prompt == [("class:slash-command.prompt", "\n❯ ")]
