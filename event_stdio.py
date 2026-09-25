# -*- coding: utf-8 -*-
"""Stdio-JSON transport for an ``EventSession`` (one JSON object per line).

This proves the event protocol works across a process boundary: outbound events
are written as newline-delimited JSON to ``out_stream``; inbound input responses
are read as newline-delimited JSON from ``in_reader`` and delivered back to the
session. ``pump`` is pure and testable with in-memory streams; ``main`` wires a
real workspace agent to ``sys.stdin``/``sys.stdout``.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
from contextlib import redirect_stdout
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar

from durable_json import JsonStorageError
from event_session import EventApprovalGate, EventSession
from event_stream_ui import EventStreamUI
from executor import SafeExecutor
from frontend_protocol import (
    FrontendChannel,
    HelloEvent,
    InputResponse,
    SessionEvent,
    is_cancel_message,
)
from redaction import redact_text, redact_value
from user_config import get_saved_provider_or_none
from version import kvist_version


def _read_responses(session: EventSession, in_reader) -> None:
    try:
        for line in in_reader:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            if is_cancel_message(data):
                session.cancel()
                continue
            response = InputResponse.from_dict(data)
            session.respond(
                response.request_id,
                value=response.value,
                approved=response.approved,
            )
    finally:
        session.close()


def _stream_events(session: EventSession, out_stream) -> None:
    """Write every event of the current run to ``out_stream`` until it ends."""
    for event in session.events():
        out_stream.write(json.dumps(event.to_dict()) + "\n")
        out_stream.flush()


def pump(
    session: EventSession,
    out_stream,
    in_reader,
    *,
    hello_event: HelloEvent | None = None,
) -> None:
    """Stream session events to ``out_stream`` while feeding it responses.

    Returns when the event stream ends (worker done + queue drained). The reader
    thread is a daemon: if it is still blocked on ``in_reader`` at that point,
    process teardown reclaims it.
    """
    out_stream.write(json.dumps((hello_event or HelloEvent()).to_dict()) + "\n")
    out_stream.flush()

    reader = threading.Thread(
        target=_read_responses,
        args=(session, in_reader),
        daemon=True,
    )
    reader.start()
    _stream_events(session, out_stream)


def build_session_hello(
    agent,
    *,
    routed_role: str | None = None,
    approval_mode: str | None = None,
    commands: list[str] | None = None,
) -> HelloEvent:
    """One authoritative launch-time hello snapshot for every stdio entrypoint.

    Sampled from the agent BEFORE the worker starts, so ``session``/
    ``has_checkpoint`` describe the state at connection time and cannot race a
    worker that clears the checkpoint (e.g. a fast ``--resume`` run).
    """
    config = agent.config
    skip_planning = getattr(config, "skip_planning", None)
    return HelloEvent(
        provider=config.provider,
        models={
            "fast": config.fast_model,
            "smart": config.smart_model,
            "planner": config.planner_model,
        },
        role=routed_role or "auto",
        approval_mode=approval_mode,
        session=agent.current_session,
        has_checkpoint=agent._peek_checkpoint() is not None,
        effort=getattr(config, "reasoning_effort", None),
        planning=None if skip_planning is None else not skip_planning,
        commands=commands,
        kvist_version=kvist_version(),
    )


def serve_session(
    session: EventSession,
    *,
    out_stream,
    in_reader,
    goal: str | None = None,
    resume: bool = False,
    routed_role: str | None = None,
    approval_mode: str | None = None,
) -> None:
    """The single stdio lifecycle: snapshot hello, submit work, pump, close.

    Every stdio entrypoint (the CLI ``launch`` and the builtin frontend
    ``serve_stdio``) routes through here so hello/session semantics can never
    diverge between them. ``goal is None`` reads the goal from ``in_reader``;
    ``resume=True`` continues the active checkpoint instead.
    """
    hello = build_session_hello(
        session.agent,
        routed_role=routed_role,
        approval_mode=approval_mode,
    )
    if resume:
        session.submit_resume()
    elif goal is None:
        first_line = in_reader.readline()
        session.submit(first_line.strip(), routed_role=routed_role)
    else:
        session.submit(goal, routed_role=routed_role)
    try:
        pump(session, out_stream, in_reader, hello_event=hello)
    finally:
        session.close()


def serve_stdio(session: EventSession) -> None:
    """Serve an already-built session over real stdio (goal = first stdin line)."""
    serve_session(session, out_stream=sys.stdout, in_reader=sys.stdin)


@dataclass(slots=True)
class CommandResult:
    """What a command handler reports; ``effects`` says what changed."""

    ok: bool
    message: str
    effects: dict = field(default_factory=dict)


@dataclass(slots=True)
class CommandContext:
    """Everything a command handler may touch, bound to one stdio process.

    ``rebuild`` builds a fresh agent for the same executor and UI (a provider or
    model switch); the handler adopts it with ``session.replace_agent``.
    ``routed_role`` is the role every following goal is submitted with, so a
    handler can change it for the rest of the process.
    """

    session: EventSession
    ui: EventStreamUI | None
    rebuild: Callable[..., tuple] | None
    routed_role: str | None
    args: list[str] = field(default_factory=list)


CommandHandler = Callable[[CommandContext], CommandResult]


@dataclass(slots=True)
class CommandResultEvent:
    name: str
    ok: bool
    message: str
    effects: dict
    type: ClassVar[str] = "command_result"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "name": self.name,
            "ok": self.ok,
            "message": redact_text(self.message),
            "effects": redact_value(self.effects),
        }


@dataclass(slots=True)
class _Command:
    name: str
    args: list[str]


def _rejected(name: str, message: str) -> CommandResultEvent:
    return CommandResultEvent(name, False, message, {})


# Sentinel the reader pushes onto the goal queue so the multi-turn loop wakes up
# and exits when stdin reaches EOF (the process's normal termination signal).
_STDIN_EOF = object()

# Queued by the reader for a `resume` command: unlike a wizard it is a turn of
# the agent, so the loop runs it with `submit_resume` instead of `submit_call`.
_RESUME = object()


class _Gate:
    """Whether the serving loop is occupied, decided under one lock.

    The reader sets ``busy`` when it queues a goal or command and the loop
    clears it once that work (and every refusal that raced its end) is written.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.busy = False


def _read_multi(
    session: EventSession,
    in_reader,
    goal_queue: queue.Queue,
    gate: _Gate | None = None,
) -> None:
    """One long-lived reader for the whole multi-turn process.

    Unlike ``_read_responses`` (single turn, control messages only) this reader
    lives across every turn and classifies each stdin line into one of four:
    a cancel control message, an ``InputResponse`` for the active run, a
    ``command`` message, or — for anything else — the next turn's goal, handed
    to the loop via ``goal_queue``. On EOF it closes the session (unblocking
    any pending input request) and releases the loop.
    """
    gate = gate if gate is not None else _Gate()
    try:
        for line in in_reader:
            stripped = line.strip()
            if not stripped:
                continue
            data = None
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                data = parsed
            if data is not None and is_cancel_message(data):
                session.cancel()
                continue
            if data is not None and "request_id" in data:
                response = InputResponse.from_dict(data)
                session.respond(
                    response.request_id,
                    value=response.value,
                    approved=response.approved,
                )
                continue
            if data is not None and data.get("type") == "command":
                _queue_command(session, data, goal_queue, gate)
                continue
            with gate.lock:
                gate.busy = True
                goal_queue.put(stripped)
    finally:
        session.close()
        goal_queue.put(_STDIN_EOF)


def _refuse(
    session: EventSession,
    goal_queue: queue.Queue,
    gate: _Gate,
    event: CommandResultEvent,
) -> None:
    """Answer a command that will not run.

    Mid-turn the running stream carries the answer out at once; when idle the
    loop is parked on the queue and nothing drains the channel, so the answer
    goes through the queue instead.
    """
    with gate.lock:
        if gate.busy:
            session.emit(event)
        else:
            goal_queue.put(event)


def _queue_command(
    session: EventSession,
    data: dict,
    goal_queue: queue.Queue,
    gate: _Gate,
) -> None:
    name = data.get("name")
    args = data.get("args", [])
    if not isinstance(name, str) or not name.strip():
        _refuse(session, goal_queue, gate, _rejected("", "Command needs a name."))
        return
    name = name.strip().lstrip("/").casefold()
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        _refuse(
            session,
            goal_queue,
            gate,
            _rejected(name, "Command args must be a list of strings."),
        )
        return
    with gate.lock:
        if gate.busy:
            session.emit(
                _rejected(
                    name,
                    "A turn or another command is running; wait for it to finish.",
                )
            )
            return
        gate.busy = True
        goal_queue.put(_RESUME if name == "resume" else _Command(name, args))


def _run_command(
    session: EventSession,
    command: _Command,
    handlers: dict[str, CommandHandler],
    context: CommandContext,
    out_stream,
) -> None:
    """Serve one command: its dialogs stream as events, then ``command_result``."""
    handler = handlers.get(command.name)
    if handler is None:
        result = CommandResult(
            False,
            f"Unknown command: {command.name}",
        )
    else:
        context.args = command.args
        session.submit_call(lambda: _guarded(handler, context))
        _stream_events(session, out_stream)
        result = session.result
    _write_event(
        CommandResultEvent(command.name, result.ok, result.message, result.effects),
        out_stream,
    )


def _resumable_session(agent) -> str | None:
    """The session whose checkpoint ``resume`` would continue, if any."""
    if agent._peek_checkpoint() is not None:
        return agent.current_session
    return agent._find_resumable_session()


def _run_resume(session: EventSession, out_stream) -> None:
    """Continue an interrupted run as an ordinary turn (events, then turn_end)."""
    try:
        holder = _resumable_session(session.agent)
    except JsonStorageError as exc:
        detail = str(exc).strip() or type(exc).__name__
        _write_event(
            CommandResultEvent(
                "resume",
                False,
                f"Resume failed: {detail}",
                {},
            ),
            out_stream,
        )
        return
    if holder is None:
        _write_event(
            _rejected(
                "resume",
                "No saved checkpoint to resume.",
            ),
            out_stream,
        )
        return
    # The run may switch sessions itself; announcing the target first lets the
    # frontend show the right conversation before the first turn event.
    session.emit(SessionEvent(holder, True))
    session.submit_resume()
    _stream_events(session, out_stream)
    out_stream.write(json.dumps({"type": "turn_end"}) + "\n")
    out_stream.flush()


def _write_event(event: CommandResultEvent, out_stream) -> None:
    out_stream.write(json.dumps(event.to_dict()) + "\n")
    out_stream.flush()


def _guarded(handler: CommandHandler, context: CommandContext) -> CommandResult:
    try:
        return handler(context)
    except Exception as exc:
        # A wizard failing halfway (storage, provider, network) must still end
        # in a command_result, or the frontend would wait for one forever.
        detail = str(exc).strip() or type(exc).__name__
        return CommandResult(False, f"Command failed: {detail}")


def serve_session_multi(
    session: EventSession,
    *,
    out_stream,
    in_reader,
    routed_role: str | None = None,
    approval_mode: str | None = None,
    commands: dict[str, CommandHandler] | None = None,
    ui: EventStreamUI | None = None,
    rebuild: Callable[..., tuple] | None = None,
) -> None:
    """Multi-turn stdio lifecycle: one hello, one reader, one run per goal line.

    The process and its agent outlive every turn: ``hello`` is emitted once, a
    single reader thread owns stdin for the process lifetime, and each goal line
    drives one submit + event drain. Cancellation (phase 1) interrupts only the
    turn it targets; the process keeps serving. EOF on stdin ends the process.

    A ``{"type": "command", ...}`` line runs the matching entry of ``commands``
    in this same process between turns (its dialogs are ordinary input
    requests) and ends with a ``command_result`` event.

    ``serve_session`` (single turn) is untouched, so every pre-existing stdio
    frontend keeps its exact behavior.
    """
    hello = build_session_hello(
        session.agent,
        routed_role=routed_role,
        approval_mode=approval_mode,
        commands=sorted({*(commands or {}), "resume"}),
    )
    out_stream.write(json.dumps(hello.to_dict()) + "\n")
    out_stream.flush()

    goal_queue: queue.Queue = queue.Queue()
    gate = _Gate()
    context = CommandContext(session, ui, rebuild, routed_role)
    reader = threading.Thread(
        target=_read_multi,
        args=(session, in_reader, goal_queue, gate),
        daemon=True,
    )
    reader.start()

    try:
        while True:
            item = goal_queue.get()
            if item is _STDIN_EOF:
                return
            if isinstance(item, CommandResultEvent):
                _write_event(item, out_stream)
            elif item is _RESUME:
                _run_resume(session, out_stream)
            elif isinstance(item, _Command):
                _run_command(session, item, commands or {}, context, out_stream)
            else:
                session.submit(item, routed_role=context.routed_role)
                _stream_events(session, out_stream)
                # A chat reply ends without `final`, so the stream alone cannot
                # tell a frontend the turn is over.
                out_stream.write(json.dumps({"type": "turn_end"}) + "\n")
                out_stream.flush()
            with gate.lock:
                # Refusals emitted while this item ran may have missed its
                # stream; flush them before the gate reopens.
                _stream_events(session, out_stream)
                if goal_queue.empty():
                    gate.busy = False
    finally:
        session.close()


def launch(
    workspace: str,
    goal: str,
    build_agent_fn: Callable,
    *,
    in_stream=None,
    out_stream=None,
    provider_override: str | None = None,
    models_override: dict[str, str] | None = None,
    routed_role: str | None = None,
    approval_mode: str = "ask",
    resume: bool = False,
) -> None:
    """Run the real Kvist core behind the stdio frontend boundary."""
    source = in_stream if in_stream is not None else sys.stdin
    destination = out_stream if out_stream is not None else sys.stdout

    if (
        not provider_override
        and not os.getenv("AGENT_LLM_PROVIDER")
        and get_saved_provider_or_none() is None
    ):
        raise RuntimeError(
            "Kvist stdio frontend requires a pre-configured provider. "
            "Run Kvist once in an interactive terminal first."
        )

    with redirect_stdout(sys.stderr):
        channel = FrontendChannel()
        ui = EventStreamUI(channel)
        executor = SafeExecutor(
            workspace,
            approvals=EventApprovalGate(channel, mode=approval_mode, workspace=workspace),
        )
        original_stdin = sys.stdin
        try:
            with open(os.devnull, encoding="utf-8") as null_input:
                sys.stdin = null_input
                agent, _config, _warnings = build_agent_fn(
                    executor,
                    max_steps=50,
                    persist_logs=False,
                    verbose=False,
                    provider_override=provider_override,
                    models_override=models_override,
                    ui=ui,
                )
        finally:
            sys.stdin = original_stdin
        serve_session(
            EventSession(channel, agent),
            out_stream=destination,
            in_reader=source,
            goal=None if resume else goal,
            resume=resume,
            routed_role=routed_role,
            approval_mode=approval_mode,
        )


def launch_multi(
    workspace: str,
    build_agent_fn: Callable,
    *,
    in_stream=None,
    out_stream=None,
    provider_override: str | None = None,
    models_override: dict[str, str] | None = None,
    routed_role: str | None = None,
    approval_mode: str = "ask",
    commands: dict[str, CommandHandler] | None = None,
    skip_planning: bool = False,
) -> None:
    """Run the real Kvist core behind the multi-turn stdio frontend boundary.

    Same launch-time contract as ``launch`` (provider check, stdout redirected
    to stderr, one agent build), but the session serves an unbounded sequence of
    goals read from stdin instead of a single goal. ``commands`` are the
    in-process slash-command handlers (see ``CommandContext``).
    """
    source = in_stream if in_stream is not None else sys.stdin
    destination = out_stream if out_stream is not None else sys.stdout

    if (
        not provider_override
        and not os.getenv("AGENT_LLM_PROVIDER")
        and get_saved_provider_or_none() is None
    ):
        raise RuntimeError(
            "Kvist stdio frontend requires a pre-configured provider. "
            "Run Kvist once in an interactive terminal first."
        )

    with redirect_stdout(sys.stderr):
        channel = FrontendChannel()
        ui = EventStreamUI(channel)
        executor = SafeExecutor(
            workspace,
            approvals=EventApprovalGate(channel, mode=approval_mode, workspace=workspace),
        )
        original_stdin = sys.stdin
        try:
            with open(os.devnull, encoding="utf-8") as null_input:
                sys.stdin = null_input
                agent, _config, _warnings = build_agent_fn(
                    executor,
                    max_steps=50,
                    persist_logs=False,
                    verbose=False,
                    skip_planning=skip_planning,
                    provider_override=provider_override,
                    models_override=models_override,
                    ui=ui,
                )
        finally:
            sys.stdin = original_stdin
        session = EventSession(channel, agent)

        def rebuild(provider_override=None, models_override=None, interactive=False):
            current = session.agent.config
            # Same stdin guard as the launch build: a credential prompt must
            # not consume the protocol lines the reader thread is waiting on.
            # ``interactive`` instead routes those prompts through the event
            # stream; the launch build never does, because no reader is
            # running yet and the request would wait forever.
            saved_stdin = sys.stdin
            try:
                with open(os.devnull, encoding="utf-8") as null_input:
                    sys.stdin = null_input
                    return build_agent_fn(
                        executor,
                        max_steps=current.max_steps,
                        persist_logs=current.persist_logs,
                        verbose=current.verbose,
                        skip_planning=current.skip_planning,
                        provider_override=provider_override,
                        models_override=models_override,
                        ui=ui,
                        **({"interactive_ui": ui} if interactive else {}),
                    )
            finally:
                sys.stdin = saved_stdin

        serve_session_multi(
            session,
            out_stream=destination,
            in_reader=source,
            routed_role=routed_role,
            approval_mode=approval_mode,
            commands=commands,
            ui=ui,
            rebuild=rebuild,
        )


def _parse_args(argv: list[str]) -> tuple[str, str]:
    parser = argparse.ArgumentParser(description="Kvist stdio-JSON event transport")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("goal", nargs="*")
    args = parser.parse_args(argv)
    goal = " ".join(args.goal).strip()
    if not goal:
        goal = sys.stdin.readline().strip()
    return args.workspace, goal


def main() -> None:
    # Imported lazily: main.py pulls in prompt_toolkit and provider SDKs that a
    # test importing ``pump`` should not have to load.
    from main import build_agent

    workspace, goal = _parse_args(sys.argv[1:])
    launch(workspace, goal, build_agent)


if __name__ == "__main__":
    main()
