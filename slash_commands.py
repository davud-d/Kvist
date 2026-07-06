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
    SlashCommand("role", "Select agent role (auto/coder/devops/chat)"),
    SlashCommand("session", "Manage and switch sessions"),
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