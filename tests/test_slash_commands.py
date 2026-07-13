from __future__ import annotations

from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.shortcuts import CompleteStyle

import main
from slash_commands import (
    SLASH_COMMANDS,
    SLASH_COMMAND_MENU_HEIGHT,
    SLASH_COMMAND_PROMPT,
    SlashCommandCompleter,
    has_slash_command_completion,
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
        ("/models", "Select fast, smart and planner models")
    ]
    assert _completion_rows("/M") == [
        ("/models", "Select fast, smart and planner models")
    ]


def test_completion_exposes_supported_aliases():
    assert _completion_rows("/ab")[0][0] == "/about"
    assert _completion_rows("/i")[0][0] == "/info"
    assert _completion_rows("/ex")[0][0] == "/exit"
    assert _completion_rows("/q")[0][0] == "/quit"


def test_completion_is_hidden_for_normal_text_and_command_arguments():
    assert _completion_rows("hello") == []
    assert _completion_rows("/new argument") == []


def test_menu_space_is_reserved_only_while_matching_slash_commands():
    assert has_slash_command_completion("/") is True
    assert has_slash_command_completion("/mod") is True
    assert has_slash_command_completion("ordinary text") is False
    assert has_slash_command_completion("/unknown") is False
    assert has_slash_command_completion("/new argument") is False


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


def test_prompt_session_uses_vertical_styled_command_menu():
    assert main.session.complete_style is CompleteStyle.COLUMN
    assert main.session.reserve_space_for_menu == SLASH_COMMAND_MENU_HEIGHT + 1
    assert main.session.style is not None

    prompt = to_formatted_text(SLASH_COMMAND_PROMPT)
    assert prompt == [("class:slash-command.prompt", "\n❯ ")]
