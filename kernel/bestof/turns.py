# -*- coding: utf-8 -*-
"""Commands that run as a turn of the main agent: ``/council <question>`` and ``/best-of [N] <task>``.

A wizard command (``role``, ``models``) ends with ``command_result`` and has no turn. These commands are the
other kind: the person asked for work, so the frontend gets what a goal produces (the turn's events, then
``turn_end``) plus a final ``command_result``, which is what frontends that wait for one are looking for.

``run_turn`` gives such a command what ``LocalAgent.run`` gives a goal: a journal turn that opens with the
typed text, the tray and memory notices, a run state of its own, a ``final`` answer, and an entry in the session
history so the next ordinary message knows what was decided. What the work itself is lives in a runner,
``(agent, question) -> TurnResult``; the runner has to be called on the session's worker thread.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from kernel.context.language_policy import resolve_response_language
from kernel.plan import end_session

TURN_COMMANDS = ("council", "best-of")

_GOAL = re.compile(r"/(" + "|".join(map(re.escape, TURN_COMMANDS)) + r")(?:\s+(.*))?", re.IGNORECASE | re.DOTALL)

# The role a turn command's history entry carries. The command is not one of the router's roles; a later
# "continue" has to land on a role that exists, and reading is what a council does and what a review of
# results is.
HISTORY_ROLE = "reader"


@dataclass(slots=True)
class TurnResult:
    """How a turn command ended.

    ``message`` is the one line of ``command_result``; ``final`` is the Markdown answer of the turn. ``stopped``
    means the person stopped it (cancel or pause), which is not a failure of the work.
    """

    ok: bool
    message: str
    final: str
    stopped: bool = False
    effects: dict = field(default_factory=dict)


TurnRunner = Callable[[Any, str], TurnResult]


def parse_turn_goal(text: str) -> tuple[str, str] | None:
    """``(command, question)`` when a goal line is ``/command question``; the question may be empty."""
    match = _GOAL.fullmatch(text.strip())
    if match is None:
        return None
    return match.group(1).casefold(), (match.group(2) or "").strip()


def turn_refusal(name: str, question: str) -> str | None:
    """Why the command cannot start, in ``English / Русский``, or ``None`` when it can.

    A refused command is answered with ``command_result`` alone: it opens no turn, so nothing is journaled.
    """
    if name == "best-of":
        from kernel.bestof.run import request_refusal

        return request_refusal(question)
    if not question.strip():
        return f"Usage: /{name} <question> / Использование: /{name} <вопрос>"
    return None


def turn_runner(name: str) -> TurnRunner:
    # Imported when used: both modes pull in the agent, and the transport imports this module at start.
    if name == "council":
        from kernel.bestof.council import run_council

        return run_council
    if name == "best-of":
        from kernel.bestof.run import run_bestof

        return run_bestof
    raise KeyError(name)


def run_turn(agent: Any, name: str, question: str) -> TurnResult:
    """Run one turn command on ``agent`` the way ``agent.run`` runs a goal."""
    refusal = agent._refuse_closed_session()
    if refusal is not None:
        return TurnResult(False, refusal, "")
    typed = f"/{name} {question}"
    runner = turn_runner(name)
    agent._journal.begin_turn(typed)
    agent._announce_turn_started()
    try:
        return _run(agent, typed, question, runner)
    finally:
        agent._journal.end_turn()
        agent._announce_turn_finished()


def _run(agent: Any, typed: str, question: str, runner: TurnRunner) -> TurnResult:
    agent._reset_cancel()
    agent._reset_run_state()
    if agent.helpers is not None:
        agent.helpers.person_wrote()
    state = agent._state
    state.message_id = end_session.next_message_id(agent, from_person=True)
    state.user_goal = typed
    # The question decides the language, not the command word in front of it.
    state.response_language = resolve_response_language(question, previous_language=agent._last_response_language)
    agent._last_response_language = state.response_language
    try:
        result = runner(agent, question)
    except Exception:
        agent.control.finish(failed=True)
        raise
    if result.stopped:
        # A pause cannot keep a council: its members are not a task that waits for the person.
        if not agent.control.cancel_requested():
            agent.control.request_cancel("user_cancelled")
        agent.control.reached_safe_point()
    agent._add_to_session_history(typed, HISTORY_ROLE, result.final, stopped_by_user=result.stopped)
    agent._ui.print_final(result.final, [], outcome="done" if result.ok else "incomplete")
    if not result.stopped:
        agent.control.finish()
    return result
