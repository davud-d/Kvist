# -*- coding: utf-8 -*-
"""A calendar schedule's run: a session of its own, and the report copied back to where the schedule was set.

Every occurrence starts from nothing, so the tenth morning costs what the first did and yesterday's search
output cannot colour today's answer. The conversation that set the schedule keeps only the reports
(``scheduled_report``); the run's own session is deleted once the report is there. When the report cannot be
delivered (that conversation is gone) the session is kept, so the report is not lost.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from kernel.agent import scheduled_report
from kernel.agent.scheduler import Fired
from kernel.sandbox.approvals import DenyApprovalGate
from kernel.sandbox.redaction import redact_value

# The goal of a run the runtime starts, not the person. The reasons are in it so the model can judge the
# edge cases itself: nobody answers, so a question has to travel inside the report.
SCHEDULED_RUN_GOAL = (
    "(Runtime: no new message from the person. A schedule you set in another conversation came due: run {run} "
    "of schedule {id}, {when}{late}. This run is a session of its own and holds nothing from the earlier runs. "
    "Nobody is watching it, so nobody can answer: approvals are refused and ask_user is not offered. If you "
    "need a decision or an approval, put it as a question in your report; the person reads the report, not "
    "this session. Your final answer is that report: it is copied into the conversation that set the "
    "schedule, so make it complete without this session. The instruction you recorded:\n\n{task})"
)


@contextmanager
def refusing_approvals(executor: Any) -> Iterator[None]:
    """Every approval question is refused for the duration: a run nobody watches must never wait or assume."""
    gate, terminal_gate = executor._approvals, executor._terminal._approvals
    executor._approvals = executor._terminal._approvals = DenyApprovalGate()
    try:
        yield
    finally:
        executor._approvals, executor._terminal._approvals = gate, terminal_gate


def run_scheduled(agent: Any, fired: Fired) -> str:
    """Carry out one occurrence in a fresh session and copy its report back; returns the report text."""
    previous = agent.current_session
    role = agent._background_role or None
    at = agent.scheduler.now()
    scheduled = scheduled_report.session_name(fired.id, at)
    agent.new_session(scheduled)
    # Titled up front: the title model call would otherwise race the deletion of the session.
    agent._session_title = f"scheduled {fired.id}"
    status, text = "failed", "The run ended before it produced a report."
    agent.unattended = True
    try:
        with refusing_approvals(agent.executor):
            text = _carry_out(agent, fired, role)
        status = "ok" if text.strip() else "failed"
        if status == "failed":
            text = "The run ended without a report."
    finally:
        agent.unattended = False
        _deliver(agent, fired, previous, scheduled, status, text, at)
    return text


def _carry_out(agent: Any, fired: Fired, role: str | None) -> str:
    when = datetime.fromtimestamp(fired.due_at).strftime("%Y-%m-%d %H:%M")
    late = ", late: Kvist was not running at that time" if fired.late else ""
    goal = SCHEDULED_RUN_GOAL.format(run=fired.run, id=fired.id, when=when, late=late, task=fired.task)
    agent._journal.begin_turn("", woken=True)
    agent._announce_turn_started()
    try:
        return agent._run_turn(goal, role, woken=True)
    finally:
        agent._journal.end_turn()
        agent._announce_turn_finished()


def _deliver(agent: Any, fired: Fired, previous: str, scheduled: str, status: str, text: str, at: datetime) -> None:
    origin = fired.origin
    if not origin or origin == scheduled or not _exists(agent, origin):
        _announce_kept(agent, fired, scheduled)
        return
    agent.switch_session(origin)
    entry = scheduled_report.history_entry(fired.id, fired.run, at.strftime("%Y-%m-%d %H:%M"), status, text)
    agent._session_history.append(redact_value(entry))
    agent.save_session()
    # The person was somewhere else when the schedule came due: they stay there, not where the report went.
    if previous != origin and _exists(agent, previous):
        agent.switch_session(previous)
    agent._delete_session(scheduled)


def _exists(agent: Any, session: str) -> bool:
    return bool(agent._read_session_document(session))


def _announce_kept(agent: Any, fired: Fired, scheduled: str) -> None:
    if agent._state.response_language == "ru":
        agent._ui.print_result(
            f"Расписание {fired.id} отработало, но разговор, где оно было поставлено, не найден. "
            f"Отчёт остался в сессии {scheduled}."
        )
        return
    agent._ui.print_result(
        f"Schedule {fired.id} ran, but the conversation it was set in is gone. The report is in session {scheduled}."
    )
