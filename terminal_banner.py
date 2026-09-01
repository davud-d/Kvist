# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Iterator

import banner

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
try:
    from rich.markdown import Markdown
except ImportError:
    Markdown = None  # type: ignore[assignment]
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from redaction import redact_value, sanitize_display_text
from terminal import is_hitl_active

try:
    from prompt_toolkit import prompt as pt_prompt
    from prompt_toolkit.styles import Style as PtStyle
    _PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    pt_prompt = None  # type: ignore[assignment]
    PtStyle = None  # type: ignore[assignment]
    _PROMPT_TOOLKIT_AVAILABLE = False


# Keep the live thinking preview short so a long chain of thought does not push
# the rest of the banner off screen; the answer replaces it once it begins.
_REASONING_PREVIEW_CHARS = 400


def _reasoning_renderable(message: str) -> Text:
    text = sanitize_display_text(message)
    if len(text) > _REASONING_PREVIEW_CHARS:
        text = "…" + text[-_REASONING_PREVIEW_CHARS:]
    return Text(f"🤔 {text}", style="grey50")


def _ignore_reasoning(message: str) -> None:
    _ = message


class TerminalBannerUI:
    """Terminal frontend for the agent UI API (``ui.AgentUI`` protocol).

    On a real terminal it renders the Rich live-status banner, streaming
    Markdown and interactive menus. Without a terminal (output piped, not a
    TTY) it degrades to ``_plain`` mode: a flat stream of log lines instead of
    Rich panels/alt-screen, so the agent still works when the terminal is the
    only available frontend.
    """

    def __init__(self, console: Console | None = None, verbose: bool = False, perf_logger: Any = None) -> None:
        self.console = console or Console()
        self.verbose = verbose
        self._live: Live | None = None
        self._screen: Any | None = None
        self._perf_logger = perf_logger
        self._spinner_cache: dict[tuple[str, str], Spinner] = {}
        # Plain (log-stream) mode when there is no real terminal to render to.
        # A default Console reports is_terminal from stdout.isatty(); tests can
        # force it either way. An attached TUI screen overrides this per-call.
        self._plain = not self.console.is_terminal
        self._last_log_line: str | None = None
        # Live telemetry accumulated across a run and pushed to the pinned
        # banner. Kept here (not in the persisted RunState) because it is a
        # transient view concern that must not enter the /resume checkpoint.
        self._live_panel = banner.LivePanelState()
        self._live_run_start = 0.0

    def attach_screen(self, screen: Any | None) -> None:
        """Route transient agent output through a persistent TUI screen.

        The screen intentionally stays duck-typed so the UI remains usable
        without prompt_toolkit and by the legacy, non-banner startup path.
        Passing ``None`` detaches the current screen.
        """
        self._screen = screen

    def _publish_live(self) -> None:
        """Push a snapshot of the live telemetry to the pinned banner."""
        screen = self._screen
        setter = getattr(screen, "set_live", None)
        if setter is None:
            return
        if self._live_panel.running and self._live_run_start:
            self._live_panel.elapsed_seconds = (
                time.monotonic() - self._live_run_start
            )
        setter(replace(self._live_panel))

    def set_run_context(
        self,
        *,
        action: str | None = None,
        files_changed: int | None = None,
        model_tier: str | None = None,
        plan_done: int | None = None,
        plan_total: int | None = None,
        plan_current: str | None = None,
        blocker: str | None = None,
        stall: int | None = None,
        stall_limit: int | None = None,
        max_steps: int | None = None,
    ) -> None:
        """Merge per-step run facts the status channel does not already carry."""
        if action is not None:
            self._live_panel.action = sanitize_display_text(str(action))
        if files_changed is not None:
            self._live_panel.files_changed = max(0, int(files_changed))
        if model_tier is not None:
            self._live_panel.model_tier = str(model_tier)
            self._live_panel.escalated = str(model_tier) not in ("", "fast")
        if plan_done is not None:
            self._live_panel.plan_done = max(0, int(plan_done))
        if plan_total is not None:
            self._live_panel.plan_total = max(0, int(plan_total))
        if plan_current is not None:
            self._live_panel.plan_current = sanitize_display_text(str(plan_current))
        if blocker is not None:
            self._live_panel.blocker = sanitize_display_text(str(blocker))
        if stall is not None:
            self._live_panel.stall = max(0, int(stall))
        if stall_limit is not None:
            self._live_panel.stall_limit = max(0, int(stall_limit))
        if max_steps is not None:
            self._live_panel.max_steps = max(0, int(max_steps))
        self._publish_live()

    def ask_choice(self, title: str, options: list[tuple[str, str]]) -> str | None:
        """Ask an arrow-key question inside the pinned screen, if there is one.

        Returns None without a screen (plain mode, no TTY, headless frontend),
        which callers must treat as "no answer" and resolve their own way.
        """
        screen = self._screen
        ask = getattr(screen, "ask_choice", None)
        if ask is None:
            return None
        return ask(title, options)

    def set_thinking(self, text: str) -> None:
        """Stream the model's reasoning into the pinned banner.

        An empty ``text`` clears the buffer at the start of a model call so a
        finished step's reasoning is never shown as the next step's thinking.
        """
        self._live_panel.thinking = text
        if text:
            self._live_panel.reasoning_seen = True
        self._publish_live()

    def set_last_thought(self, text: str) -> None:
        """Record the thought of the latest action for the thinking column.

        This is what fills the column for providers that stream no reasoning.
        """
        self._live_panel.last_thought = sanitize_display_text(str(text))
        self._publish_live()

    def start_live(self) -> None:
        self._live_panel = banner.LivePanelState(
            running=True, state_label="working"
        )
        self._live_run_start = time.monotonic()
        self._publish_live()

        if self.verbose or is_hitl_active():
            return
        if self._plain and self._screen is None:
            return

        initial = self._render_live_panel(
            user_message="Analyzing task",
            status="preparing",
            step=0,
            progress=0,
            plan_step="initialization",
            is_error=False,
        )

        if self._screen is not None:
            self._screen.set_status(initial)
            return

        self._live = Live(
            initial,
            console=self.console,
            refresh_per_second=10,
            transient=True,
        )
        self._live.start()

    def stop_live(self) -> None:
        if self._live_run_start:
            # Keep the finished run's totals on screen instead of snapping the
            # panel back to zero; only a fresh UI shows the not-started state.
            self._live_panel.elapsed_seconds = (
                time.monotonic() - self._live_run_start
            )
            self._live_panel.running = False
            self._live_panel.finished = True
            self._live_panel.state_label = "done"
        else:
            self._live_panel = banner.LivePanelState()
        self._live_run_start = 0.0
        self._publish_live()

        if self._screen is not None:
            self._screen.set_status(None)

        if self._live is None:
            return

        self._live.stop()
        self._live = None

    @contextmanager
    def _bounded_screen(self) -> Iterator[None]:
        """Draw interactive UI inside the terminal's alternate screen buffer.

        The alternate buffer is a separate screen: entering it preserves the
        current one, and leaving restores it byte-for-byte. Nothing outside
        this bounded region can be erased. Outside a TTY it is a no-op, and
        when the console is already on the alternate screen (an enclosing
        wizard owns it) the ownership stays with the outer context.
        """
        use_alt = (
            sys.stdin.isatty()
            and self.console.is_terminal
            and not self.console.is_alt_screen
        )
        if use_alt:
            self.console.set_alt_screen(True)
        try:
            yield
        finally:
            if use_alt:
                self.console.set_alt_screen(False)

    def update_live(
        self,
        user_message: str,
        status: str,
        step: int,
        progress: int = 0,
        plan_step: str = "working",
        is_error: bool = False,
    ) -> None:
        _, _, state_label = self._status_light(status, is_error)
        self._live_panel.running = True
        self._live_panel.state_label = state_label
        self._live_panel.step = step
        self._live_panel.plan_step = sanitize_display_text(str(plan_step))
        self._publish_live()

        if self._plain and self._screen is None:
            if not self.verbose:
                self._log_status(user_message, status, step, is_error)
            return

        if (
            self.verbose
            or is_hitl_active()
            or (self._screen is None and self._live is None)
        ):
            return

        panel = self._render_live_panel(
            user_message=user_message,
            status=status,
            step=step,
            progress=progress,
            plan_step=plan_step,
            is_error=is_error,
        )

        if self._screen is not None:
            if self._perf_logger is not None:
                render_start = time.monotonic()
                self._screen.set_status(panel)
                render_duration_ms = (time.monotonic() - render_start) * 1000
                self._perf_logger.log_render(render_duration_ms=render_duration_ms)
            else:
                self._screen.set_status(panel)
        elif self._perf_logger is not None:
            render_start = time.monotonic()
            assert self._live is not None
            self._live.update(panel, refresh=True)
            render_duration_ms = (time.monotonic() - render_start) * 1000
            self._perf_logger.log_render(render_duration_ms=render_duration_ms)
        else:
            assert self._live is not None
            self._live.update(panel, refresh=True)

    def _log_status(self, user_message: str, status: str, step: int, is_error: bool) -> None:
        """Emit one compact status line in plain mode, skipping repeats."""
        _, _, state_label = self._status_light(status, is_error)
        line = f"[step {step}] {state_label}: {self._clean_public_status(user_message)}"
        if line == self._last_log_line:
            return
        self._last_log_line = line
        print(line)

    @contextmanager
    def live_markdown(self) -> Iterator[Callable[[str], None]]:
        """Expose one mutable Markdown entry for a streaming response.

        A persistent screen keeps the entry in its stream slot while tokens
        arrive, then commits that slot to the transcript exactly once.  The
        legacy path deliberately retains Rich's existing ``Live`` behavior.
        In plain mode nothing is drawn per token; the final text is printed
        once when the stream closes.
        """
        if self._plain and self._screen is None:
            latest = ""

            def update(message: str) -> None:
                nonlocal latest
                latest = message

            update.reasoning = _ignore_reasoning
            try:
                yield update
            finally:
                if latest.strip():
                    print(sanitize_display_text(latest))
            return

        if self._screen is not None:
            latest = ""

            def update(message: str) -> None:
                nonlocal latest
                latest = message
                self._screen.set_stream(self.render_user_markdown(message))

            def reasoning(message: str) -> None:
                self._screen.set_stream(_reasoning_renderable(message))
                self._live_panel.thinking = message
                if message:
                    self._live_panel.reasoning_seen = True
                self._publish_live()

            update.reasoning = reasoning
            self._screen.set_stream(self.render_user_markdown(latest))
            try:
                yield update
            finally:
                self._screen.commit_stream(self.render_user_markdown(latest))
            return

        with Live(
            self.render_user_markdown(""),
            console=self.console,
            refresh_per_second=15,
        ) as live:

            def update(message: str) -> None:
                live.update(self.render_user_markdown(message))

            def reasoning(message: str) -> None:
                live.update(_reasoning_renderable(message))

            update.reasoning = reasoning
            yield update

    def print_plan(self, plan_info: Any) -> None:
        if self._plain and not self.verbose:
            print(f"[plan] {sanitize_display_text(str(plan_info.goal))}")
            return

        if not self.verbose:
            return

        print("\n[Plan]")
        print(f"Goal: {sanitize_display_text(str(plan_info.goal))}")
        print(f"Complexity: {sanitize_display_text(str(plan_info.complexity))}")

        for index, item in enumerate(plan_info.plan, start=1):
            print(f"{index}. {sanitize_display_text(str(item))}")

        if plan_info.success_criteria:
            print("Success criteria:")
            for item in plan_info.success_criteria:
                print(f"- {sanitize_display_text(str(item))}")

    def print_step_header(
        self, step: int, command: Any, reasoning_seconds: float = 0.0
    ) -> None:
        # In plain mode the compact per-step line comes from update_live.
        if not self.verbose:
            return

        print(f"\n[Step {step}]")
        print(f"Stage: {sanitize_display_text(str(command.plan_step))}")
        if reasoning_seconds > 0:
            print(f"Thought for {reasoning_seconds:.0f}s")
        print(f"Thought: {sanitize_display_text(str(command.thought))}")
        print(f"Message: {sanitize_display_text(str(command.user_message))}")
        print(
            f"Action: {command.action.type} "
            f"{redact_value(command.action.args)}"
        )

    def print_final(self, final_message: str, user_messages: list[str] | None = None) -> None:
        _ = user_messages
        if self._plain:
            print(f"[final] {sanitize_display_text(final_message)}")
            return
        try:
            final_panel = Panel(
                self.render_user_markdown(final_message, fallback_style="bright_green"),
                title="Final Answer",
                title_align="left",
                border_style="bright_green",
                box=box.ROUNDED,
                padding=(1, 2),
            )
            self.console.print(final_panel)
        except Exception:
            self.console.print(self._build_plain_final_panel(final_message))

    def render_user_markdown(self, message: str, fallback_style: str = "") -> Any:
        message = sanitize_display_text(message)
        if Markdown is None:
            return Text(message, style=fallback_style)
        try:
            return Markdown(message)
        except Exception:
            return Text(message, style=fallback_style)

    def print_user_markdown(self, message: str) -> None:
        if self._plain:
            print(sanitize_display_text(message))
            return
        try:
            self.console.print(self.render_user_markdown(message))
        except Exception:
            self.console.print(Text(sanitize_display_text(message)))

    def _build_plain_final_panel(self, message: str) -> Panel:
        return Panel(
            Text(sanitize_display_text(message), style="bright_green"),
            title="Final Answer",
            title_align="left",
            border_style="bright_green",
            box=box.ROUNDED,
            padding=(1, 2),
        )

    def debug_print(self, message: str) -> None:
        if self.verbose:
            print(sanitize_display_text(message))

    def print_usage(self, usage: dict) -> None:
        """Optional token-usage channel. Verbose-only in the terminal; event
        frontends (GUI) surface it as live run metrics instead."""
        self._live_panel.tokens_in += max(0, int(usage.get("input_tokens", 0)))
        step_output = max(0, int(usage.get("output_tokens", 0)))
        self._live_panel.tokens_out += step_output
        # Real generation speed: output tokens over the model call's own wall
        # time, not the gap between steps (which folds in idle/execute time).
        duration = float(usage.get("duration_seconds", 0.0) or 0.0)
        if duration > 0 and step_output > 0:
            self._live_panel.tokens_per_second = step_output / duration
        self._publish_live()

        self.debug_print(
            "tokens: "
            f"in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)} "
            f"model={usage.get('model', '')} kind={usage.get('kind', '')}"
        )

    def print_result(self, message: str) -> None:
        """Print a success/result message (backward-compatible)."""
        if self._plain:
            print(sanitize_display_text(message))
            return
        self.console.print(Text(sanitize_display_text(message), style="bright_green"))

    def print_error(self, message: str) -> None:
        """Print an error message (backward-compatible)."""
        if self._plain:
            print(f"[error] {sanitize_display_text(message)}")
            return
        self.console.print(Text(sanitize_display_text(message), style="bright_yellow"))

    def print_sandbox_notice(self, note: str) -> None:
        """Show a one-time sandbox honesty notice as a dim info panel.

        The default mode is workspace-only — not OS isolation. This notice
        makes the boundary explicit without looking like an error or warning.
        """
        if not note:
            return
        note = sanitize_display_text(note)
        if self._plain:
            print(f"[sandbox] {note}")
            return
        panel = Panel(
            Text(note, style="grey70"),
            title="Sandbox",
            title_align="left",
            border_style="grey50",
            box=box.ROUNDED,
            padding=(0, 2),
        )
        self.console.print(panel)

    def choose_option(
        self,
        title: str,
        options: list[str] | list[dict[str, str]],
        current_value: str = "",
        help_text: str = "↑/↓ — select, Enter — confirm, Esc — cancel",
    ) -> str | None:
        if not options:
            return None

        # Normalize options to list of dicts (handle legacy list-of-strings)
        if isinstance(options[0], str):
            normalized_options = [{"value": opt, "label": opt} for opt in options if isinstance(opt, str)]
        else:
            normalized_options = [opt for opt in options if isinstance(opt, dict)]

        values = [item["value"] for item in normalized_options]

        if not sys.stdin.isatty():
            # No interactive terminal: fall back to current value or first option.
            default = current_value if current_value in values else (values[0] if values else None)
            if self._plain:
                print(f"[warn] no TTY: '{sanitize_display_text(title)}' -> default '{default}'")
            return default

        index = values.index(current_value) if current_value in values else 0

        with self._bounded_screen():
            while True:
                self.console.clear()
                self.console.print(
                    Panel(
                        Text(sanitize_display_text(help_text), style="grey70"),
                        title=Text(sanitize_display_text(title)),
                        title_align="left",
                        border_style="bright_cyan",
                        box=box.ROUNDED,
                        padding=(1, 2),
                    )
                )

                rows: list[Text] = []
                for option_index, option in enumerate(normalized_options):
                    is_selected = option_index == index
                    prefix = "❯ " if is_selected else "  "
                    row = Text()
                    row.append(prefix, style="bright_green" if is_selected else "grey50")
                    row.append(
                        sanitize_display_text(option["label"]),
                        style="bold bright_green" if is_selected else "white",
                    )
                    description = sanitize_display_text(
                        option.get("description", "").strip()
                    )
                    if description:
                        row.append(f" — {description}", style="grey70")
                    rows.append(row)

                self.console.print(
                    Panel(
                        Group(*rows),
                        border_style="grey50",
                        box=box.ROUNDED,
                        padding=(0, 1),
                    )
                )

                pressed = self._read_menu_key()
                if pressed == "up":
                    index = (index - 1) % len(normalized_options)
                    continue
                if pressed == "down":
                    index = (index + 1) % len(normalized_options)
                    continue
                if pressed == "enter":
                    return normalized_options[index]["value"]
                if pressed in {"escape", "interrupt"}:
                    return None

    def prompt_text(
        self,
        title: str,
        current_value: str = "",
        help_text: str = "",
    ) -> str | None:
        """Prompt user for text input. Returns None if stdin is not TTY or user cancelled.

        Uses prompt_toolkit for full line editing (arrow keys, Ctrl+A/E, etc.)
        when available; falls back to built-in input().
        """
        if not sys.stdin.isatty():
            default = current_value if current_value else None
            if self._plain:
                print(f"[warn] no TTY: '{sanitize_display_text(title)}' -> default '{default}'")
            return default

        # Build panel content
        content_parts: list[Text | str] = []
        if help_text:
            content_parts.append(
                Text(sanitize_display_text(help_text), style="grey70")
            )
            content_parts.append("")

        if current_value:
            content_parts.append(
                Text(
                    f"Current: {sanitize_display_text(current_value)}",
                    style="bright_yellow",
                )
            )
            content_parts.append("")

        content_parts.append(Text("Enter value (Esc to cancel):", style="white"))

        with self._bounded_screen():
            self.console.clear()
            self.console.print(
                Panel(
                    Group(*content_parts),
                    title=Text(sanitize_display_text(title)),
                    title_align="left",
                    border_style="bright_cyan",
                    box=box.ROUNDED,
                    padding=(1, 2),
                )
            )

            try:
                if _PROMPT_TOOLKIT_AVAILABLE:
                    user_input = pt_prompt(
                        "> ",
                        default=current_value if current_value else "",
                        style=PtStyle.from_dict({"": "#ffffff"}),
                    ).strip()
                    return user_input if user_input else (current_value or None)
                else:
                    user_input = input("> ").strip()
                    return user_input if user_input else (current_value or None)
            except (KeyboardInterrupt, EOFError):
                return None

    def prompt_secret(
        self,
        title: str,
        current_value: str = "",
        help_text: str = "",
    ) -> str | None:
        """Prompt user for SECRET text input (masked).

        Uses prompt_toolkit with is_password=True for hidden input
        while preserving full line editing (arrow keys, etc.).
        Returns None if stdin is not TTY or user cancelled.
        """
        if not sys.stdin.isatty():
            if self._plain:
                print(f"[warn] no TTY: '{sanitize_display_text(title)}' -> no secret input")
            return None

        if not _PROMPT_TOOLKIT_AVAILABLE:
            from getpass import getpass
            return getpass("> ").strip() or None

        content_parts: list[Text | str] = []
        if help_text:
            content_parts.append(
                Text(sanitize_display_text(help_text), style="grey70")
            )
            content_parts.append("")

        if current_value:
            content_parts.append(Text("Current: *** (already configured)", style="bright_yellow"))
            content_parts.append("")

        content_parts.append(Text("Enter value (input hidden, Esc to cancel):", style="white"))

        with self._bounded_screen():
            self.console.clear()
            self.console.print(
                Panel(
                    Group(*content_parts),
                    title=Text(sanitize_display_text(title)),
                    title_align="left",
                    border_style="bright_cyan",
                    box=box.ROUNDED,
                    padding=(1, 2),
                )
            )

            try:
                user_input = pt_prompt(
                    "> ",
                    is_password=True,
                    style=PtStyle.from_dict({"": "#ffffff"}),
                ).strip()
                return user_input
            except (KeyboardInterrupt, EOFError):
                return None

    @staticmethod
    def _read_menu_key() -> str:
        if sys.platform == "win32":
            import msvcrt

            while True:
                key = msvcrt.getwch()
                if key in {"\x00", "\xe0"}:
                    extended = msvcrt.getwch()
                    if extended == "H":
                        return "up"
                    if extended == "P":
                        return "down"
                    continue
                if key in {"\r", "\n"}:
                    return "enter"
                if key == "\x1b":
                    return "escape"
                if key == "\x03":
                    return "interrupt"
                if key.lower() == "k":
                    return "up"
                if key.lower() == "j":
                    return "down"

        import os
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                key = os.read(fd, 1).decode("utf-8", errors="ignore")
                if key in {"\r", "\n"}:
                    return "enter"
                if key == "\x03":
                    return "interrupt"
                if key == "\x1b":
                    ready, _, _ = select.select([fd], [], [], 0.05)
                    if not ready:
                        return "escape"
                    next_char = os.read(fd, 1).decode("utf-8", errors="ignore")
                    if next_char == "[":
                        ready, _, _ = select.select([fd], [], [], 0.05)
                        if not ready:
                            return "escape"
                        final_char = os.read(fd, 1).decode(
                            "utf-8",
                            errors="ignore",
                        )
                        if final_char == "A":
                            return "up"
                        if final_char == "B":
                            return "down"
                        continue
                    return "escape"
                if key.lower() == "k":
                    return "up"
                if key.lower() == "j":
                    return "down"
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def _render_live_panel(
        self,
        user_message: str,
        status: str,
        step: int,
        progress: int,
        plan_step: str,
        is_error: bool,
    ) -> Panel:
        light_symbol, light_style, state_label = self._status_light(status, is_error)
        public_message = self._clean_public_status(user_message)
        public_plan_step = sanitize_display_text(str(plan_step))
        safe_progress = max(0, min(progress, 100))

        title = Text.assemble(
            (f"{light_symbol} ", light_style),
            ("Kvist", "bold bright_white"),
            (f" • {state_label}", "grey70"),
        )

        spinner_text = "fixing step" if state_label == "adjusting" else "executing task"
        spinner_key = (spinner_text, light_style)
        spinner = self._spinner_cache.get(spinner_key)
        if spinner is None:
            spinner = Spinner(
                "dots",
                text=Text(spinner_text, style=light_style),
            )
            self._spinner_cache[spinner_key] = spinner

        message_text = Text(public_message, style="bold bright_cyan")
        meta_text = Text(
            f"Step {step} • Progress {safe_progress}% • {public_plan_step}",
            style="grey70",
        )

        content = Group(
            Align.left(spinner),
            Text(""),
            message_text,
            Text(""),
            meta_text,
        )

        return Panel(
            content,
            title=title,
            title_align="left",
            box=box.ROUNDED,
            border_style=light_style,
            padding=(1, 2),
        )

    def _status_light(self, status: str, is_error: bool) -> tuple[str, str, str]:
        normalized = status.casefold()

        attention_words = (
            "error",
            "failed",
            "failure",
            "fixing",
            "blocked",
            "limit",
            "invalid",
            "retry",
            "repeat",
            "correction",
            "wrong",
        )

        if is_error or any(word in normalized for word in attention_words):
            return "●", "bright_yellow", "adjusting"

        return "●", "bright_green", "working"

    @staticmethod
    def _clean_public_status(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", sanitize_display_text(text)).strip()

        if not cleaned:
            return "Working on the task"

        if len(cleaned) <= 160:
            return cleaned

        return cleaned[:157].rstrip() + "..."
