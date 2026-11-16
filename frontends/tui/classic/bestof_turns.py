# -*- coding: utf-8 -*-
"""``/council <question>`` and ``/best-of [N] <task>`` in the classic terminal: commands that run as a turn.

The terminal's input loop (``kernel/main.py``) hands every line to ``run_turn_goal`` before it routes the line
as a request. A line that is one of these commands runs through ``kernel.bestof.turns.run_turn``, in this process,
on the agent the loop already has: its events and its ``final`` answer come out of the agent's own UI like any
turn's, and the dialogs it opens are the terminal's ordinary ones.
"""
from __future__ import annotations

from typing import Any

from rich.text import Text

from kernel.bestof.turns import parse_turn_goal, run_turn, take_follow_up
from kernel.sandbox.redaction import sanitize_display_text


def run_follow_up_turn(agent: Any, console: Any, interactive_screen: Any = None) -> None:
    """Start the best-of turn the person agreed to after the turn that offered it, if there is one.

    It runs as the typed ``/best-of N <task>`` does, so its output and its failures are the same.
    """
    follow_up = take_follow_up(agent)
    if follow_up is None:
        return
    name, question = follow_up
    run_turn_goal(agent, f"/{name} {question}", console, interactive_screen)


def run_turn_goal(agent: Any, goal: str, console: Any, interactive_screen: Any = None) -> bool:
    """Run ``goal`` as a turn command; ``False`` when it is not one and the caller routes it as a request."""
    turn = parse_turn_goal(goal)
    if turn is None:
        return False
    name, question = turn
    if not question:
        console.print(Text(f"Usage: /{name} <question> / Использование: /{name} <вопрос>", style="yellow"))
        return True
    if interactive_screen is not None:
        interactive_screen.pause_handler = agent.request_pause
    try:
        result = run_turn(agent, name, question)
    except KeyboardInterrupt:
        # Stops the members too: a council's agents work on threads the interrupt did not reach.
        agent.request_cancel()
        console.print(Text("Agent run stopped by you. / Работа Kvist остановлена вами.", style="yellow"))
        return True
    except Exception as error:  # noqa: BLE001 - a failed turn is reported, the session stays open
        detail = sanitize_display_text(str(error).strip() or type(error).__name__)
        console.print(Text(f"Task failed safely: {detail}. You can retry or switch provider.", style="red"))
        return True
    if not result.final:
        # No answer was produced (a closed session refuses before any work): the refusal is the reply.
        console.print(Text(sanitize_display_text(result.message), style="yellow"))
    return True
