# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol


class AgentUI(Protocol):
    """The agent's UI API — what the agent emits, independent of any frontend.

    A frontend (terminal, web, gui, Telegram bot, ...) implements this protocol
    to receive the agent's output: live status/progress, streaming and final
    responses, plan/step reasoning, and to gather user input. The default
    terminal implementation is ``terminal_banner.TerminalBannerUI``.

    Conformance is structural — implementations need not subclass this.
    """

    verbose: bool

    # --- live status / progress (thoughts) ---
    def start_live(self) -> None:
        """Begin showing the live status indicator for a run."""
        ...

    def stop_live(self) -> None:
        """Stop the live status indicator."""
        ...

    def update_live(
        self,
        user_message: str,
        status: str,
        step: int,
        progress: int = 0,
        plan_step: str = "working",
        is_error: bool = False,
    ) -> None:
        """Update the current public status/progress of the run."""
        ...

    def print_plan(self, plan_info: Any) -> None:
        """Surface the planned goal / steps / success criteria."""
        ...

    def print_step_header(
        self, step: int, command: Any, reasoning_seconds: float = 0.0
    ) -> None:
        """Surface the reasoning and action taken for one loop step.

        ``reasoning_seconds`` is how long the model spent emitting reasoning
        tokens before this action (0 when the model does not think).
        """
        ...

    def debug_print(self, message: str) -> None:
        """Emit a verbose-only diagnostic line."""
        ...

    # --- streaming and responses ---
    def live_markdown(self) -> AbstractContextManager[Callable[[str], None]]:
        """Open a streaming Markdown response.

        Yields an ``update(message)`` callback; each call replaces the current
        rendered response with ``message`` (a cumulative snapshot).

        The callback may expose a ``update.reasoning(message)`` attribute that
        streams the model's cumulative thinking text on a separate channel;
        callers probe it with ``getattr(update, "reasoning", None)`` so
        implementations without a reasoning channel keep working.
        """
        ...

    # Optional capability, probed with ``getattr(ui, "print_usage", None)``:
    # ``print_usage(usage: dict)`` receives per-model-call token usage
    # (``input_tokens``/``output_tokens`` ints, plus ``model``, ``kind``,
    # ``step``). Implementations without a metrics surface simply omit it.

    # Optional capabilities, probed the same way: ``print_action_result(result:
    # dict)`` receives the outcome of each executed action (``step``,
    # ``action_type``, ``ok``, ``summary``, ``output``, ``exit_code``, ``path``,
    # ``first_line``), and ``print_route(role: str, source: str)`` receives the
    # role a turn runs under, once per turn. The classic terminal omits both.

    def render_user_markdown(self, message: str, fallback_style: str = "") -> Any:
        """Return a renderable for ``message`` (frontend-specific)."""
        ...

    def print_user_markdown(self, message: str) -> None:
        """Render a Markdown message to the user."""
        ...

    def print_final(
        self, final_message: str, user_messages: list[str] | None = None, outcome: str = "done"
    ) -> None:
        """Render the final answer of a run; ``outcome`` is "done" or "incomplete"."""
        ...

    def print_result(self, message: str) -> None:
        """Render a success/result message."""
        ...

    def print_error(self, message: str) -> None:
        """Render an error message."""
        ...

    def print_sandbox_notice(self, note: str) -> None:
        """Render the one-time sandbox honesty notice."""
        ...

    # --- user input ---
    def choose_option(
        self,
        title: str,
        options: list[str] | list[dict[str, str]],
        current_value: str = "",
        help_text: str = "",
    ) -> str | None:
        """Ask the user to pick one option; ``None`` if cancelled/unavailable."""
        ...

    def prompt_text(
        self,
        title: str,
        current_value: str = "",
        help_text: str = "",
    ) -> str | None:
        """Ask the user for a line of text; ``None`` if cancelled/unavailable."""
        ...

    def prompt_secret(
        self,
        title: str,
        current_value: str = "",
        help_text: str = "",
    ) -> str | None:
        """Ask the user for a masked secret; ``None`` if cancelled/unavailable."""
        ...
