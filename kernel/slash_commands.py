# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
import os

from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.bindings.completion import generate_completions
from prompt_toolkit.keys import Keys
from prompt_toolkit.lexers import Lexer
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
    SlashCommand(
        "commands",
        "List all available slash commands",
        aliases=("cmds",),
    ),
    SlashCommand("new", "Start a new session (the current one is kept)"),
    SlashCommand("provider", "Switch or configure the LLM provider"),
    SlashCommand(
        "change-api",
        "Update the API key for the current provider",
        aliases=("change_api", "apikey"),
    ),
    SlashCommand("models", "Select fast and smart models"),
    SlashCommand(
        "settings-api",
        "Tune API pacing, timeouts, retries and the context window",
        aliases=("settings_api", "api"),
    ),
    SlashCommand("role", "Choose auto, coder, devops or chat role"),
    SlashCommand("effort", "Set the model's reasoning effort level"),
    SlashCommand(
        "web-search-provider",
        "Choose the web search provider and enter its API key",
        aliases=("web_search_provider", "websearch"),
    ),
    SlashCommand("session", "Create, switch or delete saved sessions"),
    SlashCommand(
        "export",
        "Import sessions from Claude Code, Codex, OpenCode, Cursor and other agents",
        aliases=("import",),
    ),
    SlashCommand("style", "Select a style for chat/agent responses"),
    SlashCommand("tui", "Choose the Ink, Claude-style, Classic or Classic Legacy terminal UI for the next launch"),
    SlashCommand("resume", "Continue the latest saved checkpoint"),
    SlashCommand("verbose", "Enable or disable verbose runtime output"),
    SlashCommand("changes", "Show agent file changes across all sessions"),
    SlashCommand("context", "Show the model's context size and how much of it is used"),
    SlashCommand("diff", "Show agent file changes recorded in this session"),
    SlashCommand("revert", "Restore agent-changed files to their baselines"),
    SlashCommand(
        "status",
        "Show the current session, role, provider, models and execution mode",
    ),
    SlashCommand(
        "permissions",
        "Show project permission rules and explain a command's decision",
    ),
    SlashCommand("bug", "Save a local bug report for a beta tester (never sent)"),
    SlashCommand("strime", "Toggle streaming for the active custom provider"),
    SlashCommand(
        "del-provider",
        "Delete the active custom provider",
        aliases=("del_provider", "delprovider"),
    ),
    SlashCommand(
        "full-reset",
        "Wipe all saved provider settings and API keys",
        aliases=("full_reset",),
    ),
    SlashCommand(
        "bye",
        "Save the active session and exit Kvist",
        aliases=("exit", "quit"),
    ),
)


def slash_commands_context() -> str:
    """Shared command reference for roles answering questions about Kvist."""
    ink = os.getenv("KVIST_COMMAND_FRONTEND") == "ink"
    classic_only = {"commands", "status", "permissions", "diff", "bug"}
    rows = []
    for command in SLASH_COMMANDS:
        if ink and command.name in classic_only:
            continue
        if ink:
            aliases = ("exit", "quit") if command.name == "bye" else ()
        else:
            aliases = command.aliases
        names = ", ".join(f"/{name}" for name in (command.name, *aliases))
        description = "Show commands and keys" if ink and command.name == "help" else command.description
        rows.append(f"- {names}: {description}")
    if ink:
        rows.extend([
            "- /rename <name>: Set the active session's title",
            "- /copy: Copy the last answer to the clipboard",
            "- /debug: Show or hide debug events",
            "- /clear: Clear the screen",
        ])
    return (
        "== KVIST COMMAND REFERENCE ==\n\n"
        "These are Kvist input commands, entered by the user in the command input, "
        "not shell commands. This reference is available when the user asks how "
        "to use Kvist or which command performs an operation. Suggest the relevant "
        "command and its effect; match the detail to the question without assuming "
        "the user is a beginner. Do not add an unsolicited tutorial. Describing a "
        "command does not execute it or change your role's capabilities. Frontends "
        "may expose these operations through menus instead.\n\n"
        "COMMAND SYNTAX: Use only the command names and aliases listed below. "
        "Do not invent slash commands, flags, subcommands or arguments from their "
        "descriptions or from other assistants' conventions. When frontend details "
        "are unknown, recommend the bare command. /role, /models, /provider, "
        "/effort, /style, /web-search-provider and /settings-api open selection/configuration dialogs; "
        "describe the choice to make in that dialog separately from the command. "
        "Some frontends also accept inline selections, but that syntax is not "
        "portable: do not assume it is available. /role offers auto, coder, reader, "
        "devops, chat and universal. Known portable argument forms are "
        "/verbose true|false, /strime true|false (custom providers only), "
        + ("" if ink else "/permissions <shell command> (explain its permission decision, not execute it), /bug <report text>, ")
        + "where supported in the list below. Angle-bracket text denotes a placeholder; "
        "true|false denotes alternatives, not literal input. For other argument "
        "forms, consult verified frontend help rather than guessing. If an "
        "operation is absent from this reference, say that its command is not "
        "documented here and point to /help. The reference does not reveal "
        "the active role, provider, models, permission rules or other live settings; "
        "use runtime information if supplied, otherwise say that these settings "
        "are unknown. Never recommend a command absent from the list below.\n\n"
        + "\n".join(rows)
    )


# The completion popup is intentionally styled like a compact command palette:
# command on the left, explanation on the right, with a quiet lavender accent.
# ``noreverse`` is required because prompt_toolkit's default selected-row style
# uses reverse video and would otherwise override the palette below.
SLASH_COMMAND_STYLE = Style.from_dict(
    {
        "slash-command.prompt": "fg:#cdd6f4 bold",
        "slash-command.prompt-continuation": "fg:#6c7086",
        "slash-command.input": "fg:#f9e2af bold",
        "slash-command.argument": "fg:#a6adc8",
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
        # In-screen arrow-key question (step-budget prompt), styled like the
        # palette above so the two selection surfaces read as one thing.
        "choice.title": "fg:#f9e2af bold",
        "choice.option": "fg:#a6adc8",
        "choice.option.current": "fg:#f5e0dc bold noreverse",
        "choice.hint": "fg:#6c7086",
        # A closed session's notice in place of the input: muted, never red.
        "closed-bar": "fg:#7f849c",
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
# Cap the palette so a growing command list can never outgrow the terminal.
# Extra matches stay reachable by scrolling the selection with the arrow keys.
SLASH_COMMAND_MENU_MAX_ROWS = 6
SLASH_COMMAND_MENU_HEIGHT = min(len(SLASH_COMMANDS), SLASH_COMMAND_MENU_MAX_ROWS)


def _matching_name(command: SlashCommand, query: str) -> str | None:
    return next(
        (
            name
            for name in (command.name, *command.aliases)
            if name.casefold().startswith(query)
        ),
        None,
    )


def _has_slash_command_prefix(text: str) -> bool:
    if not text.startswith("/") or any(character.isspace() for character in text):
        return False

    query = text[1:].casefold()
    return any(_matching_name(command, query) for command in SLASH_COMMANDS)


def has_slash_command_completion(text: str) -> bool:
    """Return whether ``text`` should open the slash-command palette."""

    # Once a toggle command is complete, its inline <true>/<false> suggestion
    # takes over and the regular command row should disappear.
    if text.casefold() in {"/verbose", "/strime"}:
        return False
    return _has_slash_command_prefix(text)


class SlashCommandAutoSuggest(AutoSuggest):
    """Inline argument suggestion for commands plus history fallback."""

    def __init__(
        self,
        verbose_enabled: bool = False,
        streaming_enabled: bool = False,
        streaming_available: bool = False,
        fallback: AutoSuggest | None = None,
    ) -> None:
        self.verbose_enabled = verbose_enabled
        self.streaming_enabled = streaming_enabled
        self.streaming_available = streaming_available
        self.fallback = fallback

    def set_verbose(self, enabled: bool) -> None:
        self.verbose_enabled = enabled

    def set_streaming(self, enabled: bool, *, available: bool) -> None:
        self.streaming_enabled = enabled
        self.streaming_available = available

    def get_suggestion(
        self,
        buffer: Buffer,
        document: Document,
    ) -> Suggestion | None:
        if document.is_cursor_at_the_end:
            value = "false" if self.verbose_enabled else "true"
            if document.text.casefold() == "/verbose":
                return Suggestion(f" <{value}>")
            if document.text.casefold() == "/verbose ":
                return Suggestion(f"<{value}>")

            if self.streaming_available:
                value = "false" if self.streaming_enabled else "true"
                if document.text.casefold() == "/strime":
                    return Suggestion(f" <{value}>")
                if document.text.casefold() == "/strime ":
                    return Suggestion(f"<{value}>")

        if self.fallback is not None:
            return self.fallback.get_suggestion(buffer, document)
        return None


class SlashCommandLexer(Lexer):
    """Highlight the command token and keep its arguments subdued."""

    def lex_document(self, document: Document):
        def get_line(lineno: int):
            line = document.lines[lineno]
            if lineno != 0 or not line.startswith("/"):
                return [("", line)]

            command, separator, arguments = line.partition(" ")
            known_names = {
                name.casefold()
                for item in SLASH_COMMANDS
                for name in (item.name, *item.aliases)
            }
            if command[1:].casefold() not in known_names:
                return [("", line)]

            fragments = [("class:slash-command.input", command)]
            if separator:
                fragments.append(
                    ("class:slash-command.argument", separator + arguments)
                )
            return fragments

        return get_line


SLASH_COMMAND_LEXER = SlashCommandLexer()


def move_cursor_left(buffer: Buffer) -> None:
    """Keep the cursor at the command input while its palette is open."""

    if _has_slash_command_prefix(buffer.text):
        buffer.cursor_position = len(buffer.text)
        return
    buffer.cursor_left(count=1)


SLASH_COMMAND_KEY_BINDINGS = KeyBindings()


@SLASH_COMMAND_KEY_BINDINGS.add(Keys.Left)
def _handle_left_arrow(event) -> None:
    move_cursor_left(event.current_buffer)


def accept_inline_suggestion(buffer: Buffer) -> bool:
    """Accept a dynamic toggle argument if it is currently visible."""

    if (
        buffer.document.is_cursor_at_the_end
        and buffer.text.casefold()
        in {"/verbose", "/verbose ", "/strime", "/strime "}
        and buffer.suggestion is not None
    ):
        buffer.insert_text(buffer.suggestion.text)
        return True
    return False


@SLASH_COMMAND_KEY_BINDINGS.add(Keys.ControlI)
def _handle_tab(event) -> None:
    if not accept_inline_suggestion(event.current_buffer):
        generate_completions(event)


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

        if text.casefold() in {"/verbose", "/strime"}:
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
