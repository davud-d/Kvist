# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import sys
import time
from typing import Any

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from terminal import is_hitl_active


class AgentUI:
    """Responsible for all visual output of the agent."""

    def __init__(self, console: Console | None = None, verbose: bool = False, perf_logger: Any = None) -> None:
        self.console = console or Console()
        self.verbose = verbose
        self._live: Live | None = None
        self._perf_logger = perf_logger

    def start_live(self) -> None:
        if self.verbose or is_hitl_active():
            return

        initial = self._render_live_panel(
            user_message="Analyzing task",
            status="preparing",
            step=0,
            progress=0,
            plan_step="initialization",
            is_error=False,
        )

        self._live = Live(
            initial,
            console=self.console,
            refresh_per_second=10,
            transient=True,
        )
        self._live.start()

    def stop_live(self) -> None:
        if self._live is None:
            return

        self._live.stop()
        self._live = None

    def update_live(
        self,
        user_message: str,
        status: str,
        step: int,
        progress: int,
        plan_step: str,
        is_error: bool = False,
    ) -> None:
        if self.verbose or self._live is None or is_hitl_active():
            return

        panel = self._render_live_panel(
            user_message=user_message,
            status=status,
            step=step,
            progress=progress,
            plan_step=plan_step,
            is_error=is_error,
        )

        render_start = time.monotonic()
        self._live.update(panel, refresh=True)
        render_duration_ms = (time.monotonic() - render_start) * 1000

        if self._perf_logger is not None:
            self._perf_logger.log_render(render_duration_ms=render_duration_ms)

    def print_plan(self, plan_info: Any) -> None:
        if not self.verbose:
            return

        print("\n[Plan]")
        print(f"Goal: {plan_info.goal}")
        print(f"Complexity: {plan_info.complexity}")

        for index, item in enumerate(plan_info.plan, start=1):
            print(f"{index}. {item}")

        if plan_info.success_criteria:
            print("Success criteria:")
            for item in plan_info.success_criteria:
                print(f"- {item}")

    def print_step_header(self, step: int, command: Any) -> None:
        if not self.verbose:
            return

        print(f"\n[Step {step}]")
        print(f"Stage: {command.plan_step}")
        print(f"Thought: {command.thought}")
        print(f"Message: {command.user_message}")
        print(f"Action: {command.action.type} {command.action.args}")
        print(f"Reported progress: {command.progress}%")

    def print_final(self, final_message: str, user_messages: list[str] | None = None) -> None:
        _ = user_messages
        final_panel = Panel(
            Text(final_message, style="bright_green"),
            title="Final Answer",
            title_align="left",
            border_style="bright_green",
            box=box.ROUNDED,
            padding=(1, 2),
        )

        self.console.print(final_panel)

    def debug_print(self, message: str) -> None:
        if self.verbose:
            print(message)

    def choose_option(
        self,
        title: str,
        options: list[dict[str, str]],
        current_value: str,
        help_text: str = "↑/↓ — выбрать, Enter — подтвердить, Esc — отменить",
    ) -> str | None:
        if not options:
            return None
        if not sys.stdin.isatty():
            return None

        values = [item["value"] for item in options]
        index = values.index(current_value) if current_value in values else 0

        while True:
            self.console.clear()
            self.console.print(
                Panel(
                    Text(help_text, style="grey70"),
                    title=title,
                    title_align="left",
                    border_style="bright_cyan",
                    box=box.ROUNDED,
                    padding=(1, 2),
                )
            )

            rows: list[Text] = []
            for option_index, option in enumerate(options):
                is_selected = option_index == index
                prefix = "❯ " if is_selected else "  "
                row = Text()
                row.append(prefix, style="bright_green" if is_selected else "grey50")
                row.append(
                    option["label"],
                    style="bold bright_green" if is_selected else "white",
                )
                description = option.get("description", "").strip()
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
                index = (index - 1) % len(options)
                continue
            if pressed == "down":
                index = (index + 1) % len(options)
                continue
            if pressed == "enter":
                self.console.clear()
                return options[index]["value"]
            if pressed in {"escape", "interrupt"}:
                self.console.clear()
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

        import termios
        import tty

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                key = sys.stdin.read(1)
                if key in {"\r", "\n"}:
                    return "enter"
                if key == "\x03":
                    return "interrupt"
                if key == "\x1b":
                    next_char = sys.stdin.read(1)
                    if next_char == "[":
                        final_char = sys.stdin.read(1)
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
        safe_progress = max(0, min(progress, 100))

        title = Text.assemble(
            (f"{light_symbol} ", light_style),
            ("DevAgentXD", "bold bright_white"),
            (f" • {state_label}", "grey70"),
        )

        spinner_text = "fixing step" if state_label == "adjusting" else "executing task"

        spinner = Spinner(
            "dots",
            text=Text(spinner_text, style=light_style),
        )

        message_text = Text(public_message, style="bold bright_cyan")
        meta_text = Text(
            f"Step {step} • Progress {safe_progress}% • {plan_step}",
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
        cleaned = re.sub(r"\s+", " ", text).strip()

        if not cleaned:
            return "Working on the task"

        if len(cleaned) <= 160:
            return cleaned

        return cleaned[:157].rstrip() + "..."