# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.text import Text


class AgentUI:
    """Responsible for all visual output of the agent: live panel, final result, verbose logs."""

    def __init__(self, console: Console | None = None, verbose: bool = False) -> None:
        self.console = console or Console()
        self.verbose = verbose
        self._live: Live | None = None

    def start_live(self) -> None:
        if self.verbose:
            return

        initial = self._render_live_panel(
            user_message="I'm starting work and gathering context",
            status="preparing",
            step=0,
            progress=0,
            plan_step="initialization",
            is_error=False,
        )
        self._live = Live(
            initial,
            console=self.console,
            refresh_per_second=8,
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

    def print_final(self, final_message: str, user_messages: list[str]) -> None:
        history = self._unique_messages(user_messages)
        if history:
            history_text = "\n".join(f"{index}. {item}" for index, item in enumerate(history, start=1))
        else:
            history_text = "1. I completed the task"

        thoughts_panel = Panel(
            Text(history_text, style="bright_cyan"),
            title="> thoughts and actions",
            title_align="left",
            border_style="bright_magenta",
            box=box.ROUNDED,
            padding=(1, 2),
        )

        final_panel = Panel(
            Text(final_message, style="bright_green"),
            title="Final Answer",
            title_align="left",
            border_style="bright_green",
            box=box.ROUNDED,
            padding=(1, 2),
        )

        self.console.print(thoughts_panel)
        self.console.print(Rule(style="grey35"))
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
        if is_error:
            status_style = "bright_red"
        elif "complet" in status:
            status_style = "bright_green"
        else:
            status_style = "bright_yellow"

        title_text = Text("Agent working", style="bold bright_magenta")
        spinner = Spinner("dots", text=Text("executing task", style="bright_cyan"))
        message_text = Text(user_message or "continuing execution", style="bold bright_cyan")
        status_text = Text(f"Status: {status}", style=status_style)
        meta_text = Text(
            f"Step {step} • Progress {max(0, min(progress, 100))}% • {plan_step}",
            style="grey70",
        )

        content = Group(
            Align.center(title_text),
            spinner,
            Text(""),
            message_text,
            status_text,
            Text(""),
            meta_text,
        )

        return Panel(
            content,
            box=box.ROUNDED,
            border_style="bright_magenta",
            padding=(1, 2),
        )

    @staticmethod
    def _normalize_message(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().casefold()

    def _unique_messages(self, messages: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for item in messages:
            normalized = self._normalize_message(item)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique.append(item.strip())
        return unique
