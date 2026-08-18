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
import sys
import threading
from contextlib import redirect_stdout
from collections.abc import Callable

from event_session import EventApprovalGate, EventSession
from event_stream_ui import EventStreamUI
from executor import SafeExecutor
from frontend_protocol import FrontendChannel, HelloEvent, InputResponse
from user_config import get_saved_provider_or_none


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
            response = InputResponse.from_dict(data)
            session.respond(
                response.request_id,
                value=response.value,
                approved=response.approved,
            )
    finally:
        session.close()


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
    for event in session.events():
        out_stream.write(json.dumps(event.to_dict()) + "\n")
        out_stream.flush()


def build_session_hello(
    agent,
    *,
    routed_role: str | None = None,
    approval_mode: str | None = None,
) -> HelloEvent:
    """One authoritative launch-time hello snapshot for every stdio entrypoint.

    Sampled from the agent BEFORE the worker starts, so ``session``/
    ``has_checkpoint`` describe the state at connection time and cannot race a
    worker that clears the checkpoint (e.g. a fast ``--resume`` run).
    """
    config = agent.config
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
            approvals=EventApprovalGate(channel, mode=approval_mode),
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
