# -*- coding: utf-8 -*-
"""Injectable human-in-the-loop approval gates.

Placing approvals behind a small Protocol lets non-terminal frontends
supply their own confirmation flow without touching the security-sensitive
executor/terminal code. The default gate reproduces the exact terminal
behavior and stays fail-closed off a TTY.

``terminal`` is imported lazily inside the methods so that ``terminal`` can
import ``TerminalApprovalGate`` at module load time without an import cycle.
"""
from __future__ import annotations

import json
from typing import Protocol, runtime_checkable


@runtime_checkable
class ApprovalGate(Protocol):
    """Confirmation seam for both approval primitives."""

    def confirm_command(self, command: list[str], reason: str) -> bool:
        """Dangerous-command gate. May raise to fail closed."""
        ...

    def confirm(self, title: str, body: str, question: str) -> bool:
        """Generic yes/no confirmation. May raise to fail closed."""
        ...

    def commit_remembered_rule(self) -> None:
        """Save the "don't ask again" choice made with the last approved command.

        Called after that command ran. Saving inside ``confirm_command`` changes
        the project policy the approval is bound to, and the run is then
        refused as a changed execution context.
        """
        ...


class TerminalApprovalGate:
    """Default gate: interactive terminal prompts, fail-closed off a TTY."""

    def commit_remembered_rule(self) -> None:
        """Nothing to save: the terminal offers its rule itself, after the run."""

    def confirm(self, title: str, body: str, question: str) -> bool:
        from kernel.terminal import request_user_confirmation

        return request_user_confirmation(title, body, question)

    def confirm_command(self, command: list[str], reason: str) -> bool:
        """Interactive approval prompt (Human-in-the-Loop).

        Displays a warning panel via rich inside the terminal's alternate
        screen buffer, then prompts for y/N via standard input(). Leaving the
        alternate buffer restores the previous screen exactly, so the prompt
        has hard boundaries and never erases output above it.

        Before input:
        - Pauses background Live TUI (via suspend callback)
        - Sets global HITL flag (blocks update_live)

        After input:
        - Leaves the alternate screen buffer (previous screen restored)
        - Clears HITL flag
        - Resumes Live TUI (via resume callback)

        If stdin is not a TTY (tests, CI, pipes) — raises the original
        validation error.
        """
        import sys

        from kernel.terminal import (
            ApprovalRequiredError,
            TerminalExecutor,
            _alt_screen,
            _enter_hitl,
            _exit_hitl,
            sanitize_display_text,
        )

        if not sys.stdin.isatty():
            raise ApprovalRequiredError(reason)

        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text

        # Pause background TUI rendering
        if TerminalExecutor._hitl_suspend_fn is not None:
            TerminalExecutor._hitl_suspend_fn()

        _enter_hitl()
        try:
            console = Console()
            command_text = sanitize_display_text(
                json.dumps(command, ensure_ascii=False)
            )
            panel_content = Text()
            panel_content.append(
                "⚠️  DANGEROUS COMMAND DETECTED\n\n",
                style="bold red",
            )
            panel_content.append("Argv: ", style="bold")
            panel_content.append(command_text, style="yellow")
            panel_content.append("\nReason: ", style="bold")
            panel_content.append(
                sanitize_display_text(reason)
            )
            panel_content.append(
                "\n\nThis command requires explicit authorization. "
                "Proceed only if every argv element and the reason are expected.",
                style="dim",
            )
            panel = Panel(
                panel_content,
                border_style="red",
                title=Text(
                    "Security Check — Human-in-the-Loop",
                    style="bold",
                ),
                title_align="left",
                padding=(1, 2),
            )

            with _alt_screen(console):
                console.print(panel)
                try:
                    choice = input("Allow execution? [y/N]: ").strip().lower()
                except EOFError:
                    raise ApprovalRequiredError(reason)

            return choice in ("y", "yes")
        finally:
            _exit_hitl()
            # Resume background TUI rendering
            if TerminalExecutor._hitl_resume_fn is not None:
                TerminalExecutor._hitl_resume_fn()


class DenyApprovalGate:
    """For an agent nobody can answer for (a helper): every approval is refused, never assumed."""

    def confirm(self, title: str, body: str, question: str) -> bool:
        return False

    def confirm_command(self, command: list[str], reason: str) -> bool:
        return False
