# -*- coding: utf-8 -*-
"""A real helper agent: its own context, its own service session, read-only work.

The main agent starts at most one helper per task. The helper is a separate
``LocalAgent`` on its own thread with the same model client, a fresh run state
and a service session (``helper-…``) that the person can read and stop but
never types into; it never becomes the active session. Its role is the one the
main agent names, or the router's choice for the task. A reader helper only
reads; a coder, devops or universal helper changes files too, unless the main
agent itself may not write (reader role, plan mode, a "do not change" request)
or a skill started it (its ``agents.manage`` grant covers read-only helpers).
It cannot start helpers or ask the person questions; overwriting an existing
file goes through the main agent's approvals when they can be answered from
another thread, and is refused otherwise.

Coordination is ordinary code, not a polling model: the helper's thread posts
compact events (started, finished, failed, stopped, update applied) into a
queue the main loop reads before its next model call, and ``wait`` blocks the
main loop on that queue without sending any request.

A pause of the main task pauses the helper at its next safe point. It then
stays paused until the main agent sends it a message, a new task or an
explicit continue: a corrected plan reaches the helper before it acts again.
A message is ``sent`` when queued, ``received`` when the helper's run picks it
up and ``applied`` when the helper's next model request includes it.
"""
from __future__ import annotations

import copy
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from kernel.agent_tools import HelperError
from kernel.redaction import redact_text
from kernel.task_control import TaskControl

MAX_ACTIVE_HELPERS = 1
HELPER_MAX_STEPS = 40
STOP_WAIT_SECONDS = 15.0
NOTICE_CHARS = 300
# How much of a helper call's output reaches the main interface live; the
# whole output stays in the helper's service session.
ACTIVITY_LINES = 3
ACTIVITY_LINE_CHARS = 200

# Helper lifecycle states, as the main agent and the interfaces see them.
STARTING = "starting"
RUNNING = "running"
PAUSED = "paused"
DONE = "done"
FAILED = "failed"
STOPPED = "stopped"
_ACTIVE = (STARTING, RUNNING, PAUSED)


class _HelperUI:
    """What a helper shows: nothing of its own; its events go to its service session's journal.

    The main interface hears compact notices through the manager, plus each
    call as it starts and ends (``report``), so a frontend can show what the
    helper is doing while it works.
    """

    def __init__(self, report: Any) -> None:
        self._report = report

    def print_step_header(self, step: int, command: Any, reasoning_seconds: float = 0.0) -> None:
        self._report({"phase": "step", "call_id": command.call_id or f"step-{step}",
                      "action_type": str(command.action.type), "label": command.action.description})

    def print_action_result(self, result: dict) -> None:
        self._report({"phase": "result", "call_id": result.get("call_id") or f"step-{result.get('step', 0)}",
                      "action_type": str(result.get("action_type", "")), "ok": bool(result.get("ok")),
                      "summary": str(result.get("summary", "")), "output": _last_lines(str(result.get("output", "")))})

    def start_live(self) -> None:
        return None

    def stop_live(self) -> None:
        return None

    def update_live(self, *args, **kwargs) -> None:
        return None

    def print_plan(self, *args, **kwargs) -> None:
        return None

    def live_markdown(self):
        import contextlib

        @contextlib.contextmanager
        def stream():
            def update(message: str) -> None:
                return None

            update.reasoning = lambda message: None
            yield update

        return stream()

    def print_final(self, *args, **kwargs) -> None:
        return None

    def render_user_markdown(self, message: str, fallback_style: str = "") -> str:
        return message

    def print_user_markdown(self, *args, **kwargs) -> None:
        return None

    def print_sandbox_notice(self, *args, **kwargs) -> None:
        return None

    def print_result(self, *args, **kwargs) -> None:
        return None

    def print_error(self, *args, **kwargs) -> None:
        return None

    def debug_print(self, *args, **kwargs) -> None:
        return None


@dataclass(slots=True)
class Helper:
    agent_id: str
    session: str
    task: str
    role: str | None
    started_by: str
    agent: Any
    state: str = STARTING
    thread: threading.Thread | None = None
    result: str = ""
    error: str = ""
    stopped_by: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    inbox: deque = field(default_factory=deque)
    messages: list[dict[str, Any]] = field(default_factory=list)


class HelperManager:
    def __init__(self, main: Any) -> None:
        self._main = main
        self._helpers: dict[str, Helper] = {}
        self._events: deque = deque(maxlen=100)
        self._cond = threading.Condition()
        self._person_stopped_at_message: int | None = None

    # --- reading ---------------------------------------------------------

    def get(self, agent_id: str) -> Helper:
        helper = self._helpers.get(agent_id)
        if helper is None:
            known = ", ".join(self._helpers) or "none"
            raise HelperError(f"no helper {agent_id!r} in this task (known: {known})")
        return helper

    def active(self) -> list[Helper]:
        return [helper for helper in self._helpers.values() if helper.state in _ACTIVE]

    def status(self, agent_id: str) -> dict[str, Any]:
        helper = self.get(agent_id)
        return self._summary(helper)

    def status_all(self) -> dict[str, Any]:
        return {"helpers": [self._summary(helper) for helper in self._helpers.values()]}

    def _summary(self, helper: Helper) -> dict[str, Any]:
        state = helper.agent._state
        return {
            "agent_id": helper.agent_id,
            "state": helper.state,
            "role": state.current_agent_role or helper.role,
            "session": helper.session,
            "task": helper.task[:500],
            "steps": len(state.step_logs),
            "files_read": list(state.files_read)[-20:],
            "messages": helper.messages[-10:],
            "stopped_by": helper.stopped_by or None,
            "seconds": round((helper.finished_at or time.time()) - helper.started_at, 1),
        }

    def inspect(self, agent_id: str, cursor: int, limit: int) -> dict[str, Any]:
        """The helper's own actions, bounded: calls, outcomes, safe command output."""
        helper = self.get(agent_id)
        logs = helper.agent._state.step_logs
        page = logs[cursor:cursor + limit]
        rows = []
        for log in page:
            command = (log.get("command") or {}).get("action") or {}
            feedback = log.get("feedback") or {}
            data = feedback.get("data") if isinstance(feedback.get("data"), dict) else {}
            rows.append({
                "step": log.get("step"),
                "status": log.get("status"),
                "action": command.get("type"),
                "args": {key: value for key, value in (command.get("args") or {}).items()
                         if key in ("path", "paths", "query", "pattern", "command", "url")},
                "error": redact_text(str(feedback.get("error") or log.get("error") or ""))[:500] or None,
                "output": redact_text(str(data.get("stdout") or data.get("content") or ""))[:800] or None,
            })
        return {"agent_id": agent_id, "items": rows, "next_cursor": cursor + len(page),
                "has_more": cursor + len(page) < len(logs), "untrusted": True}

    def result(self, agent_id: str) -> dict[str, Any]:
        helper = self.get(agent_id)
        partial = helper.state not in (DONE,)
        text = helper.result or "\n".join(helper.agent._state.user_messages[-3:])
        return {"agent_id": agent_id, "state": helper.state, "partial": partial,
                "result": redact_text(text)[:20000], "error": helper.error or None,
                "files_read": list(helper.agent._state.files_read), "untrusted": True}

    # --- starting --------------------------------------------------------

    def start(self, task: str, *, role: str | None, context: str, attachments: list[str], started_by: str) -> dict[str, Any]:
        main = self._main
        if getattr(main, "is_helper", False):
            raise HelperError("a helper cannot start helpers")
        if len(self.active()) >= MAX_ACTIVE_HELPERS:
            raise HelperError(f"one helper is already working ({self.active()[0].agent_id}); this version runs at "
                              f"most {MAX_ACTIVE_HELPERS} helper per task")
        if self._person_stopped_at_message is not None and main._state.message_id == self._person_stopped_at_message:
            raise HelperError("the person stopped the previous helper; do not start another one until they write "
                              "again. Continue the independent work yourself or ask them a specific question.")
        agent_id = f"h{len(self._helpers) + 1}"
        session = f"helper-{main.current_session}-{agent_id}-{uuid.uuid4().hex[:6]}"[:120]
        helper = Helper(agent_id=agent_id, session=session, task=task, role=role, started_by=started_by, agent=None)
        # A skill's grant is "start a read-only helper": a writing one would give
        # the skill writes its manifest never declared.
        read_only = _main_cannot_write(main) or role == "reader" or started_by != "main"
        helper.agent = self._build_agent(helper, read_only=read_only)
        goal = _helper_goal(task, context, attachments, read_only=read_only)
        self._helpers[agent_id] = helper
        main.control.adopt(helper.agent.control)
        self._launch(helper, goal)
        self._emit(helper, "started")
        return {"agent_id": agent_id, "state": helper.state, "session": session,
                "note": _start_note(role, read_only, self._wakes_after_answer())}

    def _wakes_after_answer(self) -> bool:
        return getattr(self._main, "wake_listener", None) is not None

    def _build_agent(self, helper: Helper, *, read_only: bool) -> Any:
        from kernel.agent import LocalAgent
        from kernel.approvals import DenyApprovalGate
        from kernel.executor import SafeExecutor

        main = self._main
        config = copy.copy(main.config)
        config.interactive = False
        config.end_session_enabled = False
        config.max_steps = min(config.max_steps, HELPER_MAX_STEPS)
        main_gate = main.executor.approvals
        shared_gate = not read_only and getattr(main_gate, "answers_from_any_thread", False)
        executor = SafeExecutor(main.executor.workspace, approvals=main_gate if shared_gate else DenyApprovalGate(),
                                read_roots=main.executor.read_roots)
        agent = LocalAgent(main.client, executor, config, ui=_HelperUI(lambda payload: self._activity(helper, payload)),
                           console=main.console,
                           service_session={"kind": "helper", "agent_id": helper.agent_id,
                                            "parent_session": main.current_session,
                                            "parent_run": getattr(main, "_run_id", None)},
                           session=helper.session)
        agent.force_read_only = read_only
        agent.inbox_source = lambda: self._drain_inbox(helper)
        return agent

    def _role_for(self, helper: Helper) -> str:
        """The named role, or the router's choice for the task itself (not the instructions around it).

        A helper has nobody to chat with, so a "chat" verdict becomes a reader:
        a question about the code is answered by looking at it.
        """
        if helper.role:
            return helper.role
        role = helper.agent._route_decision(helper.task).role
        return "reader" if role == "chat" else role

    def _launch(self, helper: Helper, goal: str, *, resume_instruction: str | None = None) -> None:
        helper.state = RUNNING

        def run() -> None:
            agent = helper.agent
            try:
                if resume_instruction is None:
                    helper.role = self._role_for(helper)
                    text = agent.run(goal, routed_role=helper.role)
                else:
                    text = agent.run(resume_instruction)
                outcome = agent.control.state
                if outcome == "paused":
                    helper.state = PAUSED
                elif agent.control.cancel_requested():
                    helper.state = STOPPED
                    helper.result = text or helper.result
                else:
                    helper.state = DONE if not agent._state.finished_incomplete else FAILED
                    helper.result = text or ""
            except Exception as error:  # noqa: BLE001 - a helper's crash is reported, never raised into the main run
                helper.state = FAILED
                helper.error = redact_text(f"{type(error).__name__}: {error}")[:1000]
            helper.finished_at = time.time()
            self._emit(helper, helper.state)

        helper.thread = threading.Thread(target=run, name=f"kvist-helper-{helper.agent_id}", daemon=True)
        helper.thread.start()

    # --- talking to a helper ----------------------------------------------

    def message(self, agent_id: str, text: str) -> dict[str, Any]:
        helper = self.get(agent_id)
        if helper.state in (DONE, FAILED, STOPPED):
            raise HelperError(f"{agent_id} has {helper.state}; start a new helper for new work")
        entry = {"id": f"m{len(helper.messages) + 1}", "kind": "message", "text": text, "state": "sent",
                 "at": time.time()}
        helper.messages.append(entry)
        if helper.state == PAUSED:
            self._resume(helper, f"Message from the main agent: {text}")
        else:
            helper.inbox.append(entry)
        return {"agent_id": agent_id, "message_id": entry["id"], "state": entry["state"]}

    def update(self, agent_id: str, task: str) -> dict[str, Any]:
        """Replace the helper's assignment: it stops at a safe point, then resumes on the new task."""
        helper = self.get(agent_id)
        if helper.state in (DONE, FAILED, STOPPED):
            raise HelperError(f"{agent_id} has {helper.state}; start a new helper for new work")
        entry = {"id": f"m{len(helper.messages) + 1}", "kind": "update", "text": task, "state": "sent",
                 "at": time.time()}
        helper.messages.append(entry)
        instruction = (f"The main agent replaced your assignment. New assignment: {task}\n"
                       "Drop the old plan; check what you already found before acting on the new one.")
        if helper.state == RUNNING:
            helper.agent.control.request_pause("assignment_changed")
            self._join(helper, STOP_WAIT_SECONDS)
        helper.task = task
        self._resume(helper, instruction)
        return {"agent_id": agent_id, "message_id": entry["id"], "state": entry["state"]}

    def resume(self, agent_id: str) -> dict[str, Any]:
        helper = self.get(agent_id)
        if helper.state != PAUSED:
            raise HelperError(f"{agent_id} is {helper.state}, not paused")
        self._resume(helper, "Continue with your assignment.")
        return {"agent_id": agent_id, "state": helper.state}

    def _resume(self, helper: Helper, instruction: str) -> None:
        for entry in helper.messages:
            if entry["state"] == "sent":
                entry["state"] = "received"
        self._launch(helper, "", resume_instruction=instruction)

    def _drain_inbox(self, helper: Helper) -> list[str]:
        """Called by the helper's own loop right before a model request: what arrived since.

        Everything received by now is part of the request about to be made,
        so it counts as applied from here on.
        """
        texts = []
        while helper.inbox:
            entry = helper.inbox.popleft()
            entry["state"] = "received"
            texts.append(f"Message from the main agent: {entry['text']}")
        self._mark_applied(helper)
        return texts

    def _mark_applied(self, helper: Helper) -> None:
        changed = False
        for entry in helper.messages:
            if entry["state"] == "received":
                entry["state"] = "applied"
                changed = True
        if changed:
            self._emit(helper, "update_applied")

    # --- waiting and stopping --------------------------------------------

    def wait(self, agent_id: str, timeout: float) -> dict[str, Any]:
        """Block the main loop until the helper needs attention; no model request is made."""
        helper = self.get(agent_id)
        deadline = time.monotonic() + timeout
        control = self._main.control
        with self._cond:
            while helper.state in (STARTING, RUNNING):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or control.interrupted():
                    break
                self._cond.wait(min(remaining, 0.5))
        summary = self._summary(helper)
        summary["waited_seconds"] = round(timeout - max(0.0, deadline - time.monotonic()), 1)
        if helper.state in (DONE, FAILED, STOPPED):
            summary["result"] = self.result(agent_id)["result"][:4000]
        return summary

    def stop(self, agent_id: str, *, by: str) -> dict[str, Any]:
        helper = self.get(agent_id)
        if helper.state in (DONE, FAILED, STOPPED):
            return self._summary(helper)
        helper.stopped_by = by
        helper.agent.control.request_cancel("user_cancelled" if by == "person" else "parent")
        self._join(helper, STOP_WAIT_SECONDS)
        if helper.state == PAUSED or (helper.thread is not None and not helper.thread.is_alive()):
            helper.state = STOPPED
            helper.finished_at = time.time()
        if by == "person":
            self._person_stopped_at_message = self._main._state.message_id
        self._emit(helper, "stopped")
        summary = self._summary(helper)
        summary["partial_result"] = self.result(agent_id)["result"][:4000]
        return summary

    def stop_all(self, reason: str) -> list[str]:
        stopped = []
        for helper in self.active():
            self.stop(helper.agent_id, by="agent" if reason != "user_cancelled" else "person")
            stopped.append(helper.agent_id)
        return stopped

    def _join(self, helper: Helper, timeout: float) -> None:
        if helper.thread is not None:
            helper.thread.join(timeout)

    # --- notices to the main loop ----------------------------------------

    def _emit(self, helper: Helper, event: str) -> None:
        notice = {"agent_id": helper.agent_id, "event": event, "state": helper.state, "at": time.time()}
        if event in (DONE, FAILED, STOPPED):
            text = helper.result or helper.error
            notice["summary"] = redact_text(text)[:NOTICE_CHARS]
        with self._cond:
            self._events.append(notice)
            self._cond.notify_all()
        printer = getattr(self._main._ui, "print_helper", None)
        if printer is not None:
            printer({**notice, "session": helper.session, "task": helper.task[:200],
                     "stopped_by": helper.stopped_by or None})
        # A stop came from the person or the main agent, who already know.
        if event in (DONE, FAILED):
            self._main._background_finished()

    def _activity(self, helper: Helper, payload: dict[str, Any]) -> None:
        printer = getattr(self._main._ui, "print_helper_activity", None)
        if printer is not None:
            printer({"agent_id": helper.agent_id, **payload})

    def has_finish_notice(self) -> bool:
        """Whether a helper ended on its own and the main model has not been told yet."""
        with self._cond:
            return any(notice["event"] in (DONE, FAILED) for notice in self._events)

    def take_notices(self) -> str:
        """Compact lines about helper events since the last call, for the main model's next request."""
        with self._cond:
            notices = list(self._events)
            self._events.clear()
        lines = []
        for notice in notices:
            line = f"- {notice['agent_id']}: {notice['event']}"
            if notice.get("summary"):
                line += f" — {notice['summary']}"
            if notice["event"] in (DONE, FAILED, STOPPED):
                line += f" (full result: helper {{\"op\": \"result\", \"agent_id\": \"{notice['agent_id']}\"}})"
            lines.append(line)
        return "\n".join(lines)

    def prompt_section(self) -> str:
        """Standing facts about helpers for the main model (paused helpers wait for it)."""
        lines = []
        for helper in self._helpers.values():
            if helper.state == PAUSED:
                lines.append(f"- {helper.agent_id} is paused (the person paused the task). It continues only after "
                             "you send it a message, a new task, or helper {\"op\": \"message\"} with \"continue\".")
            elif helper.state in (STARTING, RUNNING):
                lines.append(f"- {helper.agent_id} is working on: {helper.task[:200]}")
        return "\n".join(lines)

    def person_wrote(self) -> None:
        """A new message from the person lifts the "do not restart a stopped helper" rule."""
        self._person_stopped_at_message = None


def _last_lines(text: str) -> str:
    """The end of a command's output: where a test run or a build says how it went."""
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(line[:ACTIVITY_LINE_CHARS] for line in lines[-ACTIVITY_LINES:])


def _main_cannot_write(main: Any) -> bool:
    """A helper never gets more than the agent that started it."""
    state = main._state
    return (
        main.force_read_only
        or state.current_agent_role == "reader"
        or state.read_only_goal
        or state.plan_phase == "planning"
    )


def _helper_goal(task: str, context: str, attachments: list[str], *, read_only: bool) -> str:
    parts = [task.strip()]
    if context.strip():
        parts.append(f"Context from the main agent:\n{context.strip()}")
    if attachments:
        parts.append("Attachments mentioned by the main agent (ids only; you cannot open them): "
                     + ", ".join(attachments))
    if read_only:
        parts.append("You are a helper of another Kvist agent: work read-only, do not ask the person anything, and "
                     "end with a concise report of what you found, with file paths and line numbers.")
    else:
        parts.append("You are a helper of another Kvist agent, which may be changing files of this workspace at the "
                     "same time: change only what this assignment covers, do not ask the person anything, and end "
                     "with a concise report of what you changed and checked, with file paths.")
    return "\n\n".join(parts)


def _start_note(role: str | None, read_only: bool, wakes: bool) -> str:
    role_part = f"It works as {role}" if role else "The router picks its role from the task; it works"
    access = ("read-only" if read_only else
              "and may change files: do not edit the files of its assignment yourself until it reports")
    after = ("If you give your final answer before it finishes, it keeps working, and its result reaches you "
             "as a new turn when it ends." if wakes else
             "It is stopped when you give your final answer.")
    return (f"{role_part} {access}, in its own session. You get a short notice when it finishes; use helper wait to "
            f"block until then, or continue other work. {after}")
