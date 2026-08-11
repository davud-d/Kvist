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


def serve_stdio(session: EventSession) -> None:
    """Serve an already-built session over stdio (goal = first stdin line)."""
    goal = sys.stdin.readline().strip()
    session.submit(goal)
    try:
        pump(session, sys.stdout, sys.stdin)
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
        session = EventSession(channel, agent)
        if resume:
            session.submit_resume()
        else:
            session.submit(goal, routed_role=routed_role)
        pump(
            session,
            destination,
            source,
            hello_event=HelloEvent(
                provider=_config.provider,
                models={
                    "fast": _config.fast_model,
                    "smart": _config.smart_model,
                    "planner": _config.planner_model,
                },
                role=routed_role or "auto",
                approval_mode=approval_mode,
                session=agent.current_session,
                has_checkpoint=agent._peek_checkpoint() is not None,
            ),
        )
        session.close()


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
