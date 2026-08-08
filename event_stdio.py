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
import sys
import threading

from event_session import EventApprovalGate, EventSession
from event_stream_ui import EventStreamUI
from executor import SafeExecutor
from frontend_protocol import (
    PROTOCOL_VERSION,
    FrontendChannel,
    HelloEvent,
    InputResponse,
)


def _read_responses(session: EventSession, in_reader) -> None:
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


def _write_event(out_stream, payload: dict) -> None:
    out_stream.write(json.dumps(payload) + "\n")
    out_stream.flush()


def pump(session: EventSession, out_stream, in_reader) -> None:
    """Stream session events to ``out_stream`` while feeding it responses.

    The first line is always a ``hello`` handshake carrying the protocol version.
    Returns when the event stream ends (worker done + queue drained). The reader
    thread is a daemon: if it is still blocked on ``in_reader`` at that point,
    process teardown reclaims it.
    """
    _write_event(out_stream, HelloEvent(PROTOCOL_VERSION).to_dict())
    reader = threading.Thread(
        target=_read_responses,
        args=(session, in_reader),
        daemon=True,
    )
    reader.start()
    for event in session.events():
        _write_event(out_stream, event.to_dict())


def serve_stdio(session: EventSession, goal: str | None = None) -> None:
    """Run ``session`` over the real stdin/stdout as newline-delimited JSON.

    stdout is redirected to stderr for the duration so that any stray ``print``
    from library code cannot corrupt the JSON stream: only the saved real stdout
    (``real_out``) carries events. The goal, when not given, is read as the first
    stdin line — the parent process sends it before any responses.
    """
    real_out = sys.stdout
    sys.stdout = sys.stderr
    if goal is None:
        goal = sys.stdin.readline().strip()
    session.submit(goal)
    pump(session, real_out, sys.stdin)


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
    # Redirect before building so nothing the agent build might print reaches the
    # JSON stream; only ``real_out`` carries events.
    real_out = sys.stdout
    sys.stdout = sys.stderr
    channel = FrontendChannel()
    executor = SafeExecutor(workspace, approvals=EventApprovalGate(channel))
    agent, _config, _warnings = build_agent(
        executor,
        max_steps=50,
        persist_logs=False,
        verbose=False,
        ui=EventStreamUI(channel),
    )
    session = EventSession(channel, agent)
    session.submit(goal)
    pump(session, real_out, sys.stdin)


if __name__ == "__main__":
    main()
