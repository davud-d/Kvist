# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.styles import Style


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str
    aliases: tuple[str, ...] = ()


SLASH_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand(
        "help",
        "Show Kvist information and platform support",
        aliases=("about", "info"),
    ),
    SlashCommand("new", "Reset the active session and start fresh"),
    SlashCommand("provider", "Switch or configure the LLM provider"),
    SlashCommand("models", "Select fast, smart and planner models"),
    SlashCommand("role", "Choose auto, coder, devops or chat role"),
    SlashCommand("session", "Create, switch or delete saved sessions"),
    SlashCommand("resume", "Continue the latest saved checkpoint"),
    SlashCommand(
        "bye",
        "Save the active session and exit Kvist",
        aliases=("exit", "quit"),
    ),
)


# The completion popup is intentionally styled like a compact command palette:
# command on the left, explanation on the right, with a quiet lavender accent.
# ``noreverse`` is required because prompt_toolkit's default selected-row style
# uses reverse video and would otherwise override the palette below.
SLASH_COMMAND_STYLE = Style.from_dict(
    {
        "slash-command.prompt": "fg:#cdd6f4 bold",
        "slash-command.prompt-continuation": "fg:#6c7086",
        "completion-menu": "bg:default",
        "completion-menu.completion": "fg:#a6adc8 bg:default",
        "completion-menu.completion.current": (
            "fg:#f5e0dc bg:default bold noreverse"
        ),
        "completion-menu.meta.completion": "fg:#7f849c bg:default",
        "completion-menu.meta.completion.current": (
            "fg:#cdd6f4 bg:default noreverse"
        ),
        "auto-suggestion": "fg:#585b70 italic",
        "scrollbar.background": "bg:default",
        "scrollbar.button": "bg:default",
    }
)

SLASH_COMMAND_PROMPT = FormattedText(
    [("class:slash-command.prompt", "\n❯ ")]
)
SLASH_COMMAND_PROMPT_CONTINUATION = FormattedText(
    [("class:slash-command.prompt-continuation", "  · ")]
)
SLASH_COMMAND_MENU_HEIGHT = len(SLASH_COMMANDS)


def _matching_name(command: SlashCommand, query: str) -> str | None:
    return next(
        (
            name
            for name in (command.name, *command.aliases)
            if name.casefold().startswith(query)
        ),
        None,
    )


def has_slash_command_completion(text: str) -> bool:
    """Return whether ``text`` should open the slash-command palette."""

    if not text.startswith("/") or any(character.isspace() for character in text):
        return False

    query = text[1:].casefold()
    return any(_matching_name(command, query) for command in SLASH_COMMANDS)


class SlashCommandCompleter(Completer):
    def __init__(self, commands: tuple[SlashCommand, ...] = SLASH_COMMANDS) -> None:
        self._commands = commands

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor

        # Show the menu only if input starts with /
        if not text.startswith("/"):
            return

        # Do not show the menu after arguments: "/new something"
        if any(character.isspace() for character in text):
            return

        query = text[1:].casefold()

        for command in self._commands:
            matched_name = _matching_name(command, query)
            if matched_name is None:
                continue

            yield Completion(
                text=f"/{matched_name}",
                start_position=-len(text),
                display=f"/{matched_name}",
                display_meta=command.description,
            )


def normalize_slash_command(raw: str) -> str:
    text = raw.strip()

    if not text.startswith("/"):
        return text

    body = text[1:].strip()

    # The user entered only "/"
    # Do not crash. Let main.py show help or simply continue input.
    if not body:
        return "/help"

    parts = body.split(maxsplit=1)
    command = parts[0].casefold()
    args = parts[1] if len(parts) > 1 else ""

    for item in SLASH_COMMANDS:
        aliases = {alias.casefold() for alias in item.aliases}

        if command == item.name.casefold() or command in aliases:
            normalized = "/" + item.name
            return f"{normalized} {args}".strip()

    return text
