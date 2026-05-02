# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text


class AgentUI:
    """Responsible for all visual output of the agent."""

    def __init__(self, console: Console | None = None, verbose: bool = False) -> None:
        self.console = console or Console()
        self.verbose = verbose
        self._live: Live | None = None

    def start_live(self) -> None:
        if self.verbose:
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
        if self.verbose or self._live is None:
            return

        panel = self._render_live_panel(
            user_message=user_message,
            status=status,
            step=step,
            progress=progress,
            plan_step=plan_step,
            is_error=is_error,
        )

        self._live.update(panel, refresh=True)

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