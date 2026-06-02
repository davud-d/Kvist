# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str
    aliases: tuple[str, ...] = ()


SLASH_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("help", "Show slash command help"),
    SlashCommand("new", "Reset the session"),
    SlashCommand("provider", "Switch LLM provider"),
    SlashCommand("models", "Select fast/smart/planner models"),
    SlashCommand("resume", "Resume last checkpoint"),
    SlashCommand("bye", "Exit DevAgentXD", aliases=("exit", "quit")),
)


class SlashCommandCompleter(Completer):
    def __init__(self, commands: tuple[SlashCommand, ...] = SLASH_COMMANDS) -> None:
        self._commands = commands

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor

        # Show the menu only if input starts with /
        if not text.startswith("/"):
            return

        # Do not show the menu after arguments: "/new something"
        if " " in text:
            return

        query = text[1:].casefold()

        for command in self._commands:
            names = (command.name, *command.aliases)

            matched_name = next(
                (name for name in names if name.casefold().startswith(query)),
                None,
            )
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

    command = text[1:].split(maxsplit=1)[0].casefold()

    for item in SLASH_COMMANDS:
        if command == item.name.casefold() or command in {a.casefold() for a in item.aliases}:
            return "/" + item.name

    return text