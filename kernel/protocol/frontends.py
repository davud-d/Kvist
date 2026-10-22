# -*- coding: utf-8 -*-
"""Universal frontend dispatch: build one EventSession, hand it to a launcher.

Every non-terminal frontend shares the same wiring (channel -> EventStreamUI ->
executor with EventApprovalGate -> agent -> EventSession); it differs only in
what it does with the session. A launcher is any ``launch(session)`` callable.
The built-in ``stdio`` launcher lives here; an external frontend is imported
lazily by name from a ``kvist_frontends.<name>`` module exposing ``launch`` — so
adding a frontend is one file and needs no core edit. Lazy import means a heavy
toolkit (Qt, aiohttp, …) loads only when its frontend is selected.
"""
from __future__ import annotations

import importlib
from collections.abc import Callable

from kernel.protocol.event_session import EventApprovalGate, EventSession
from kernel.protocol.event_stream_ui import EventStreamUI
from kernel.sandbox.executor import SafeExecutor
from kernel.protocol.frontend_protocol import FrontendChannel

Launcher = Callable[[EventSession], None]


def make_session(
    workspace: str,
    *,
    max_steps: int = 150,
    persist_logs: bool = False,
    verbose: bool = False,
    language_hint: str | None = None,
) -> EventSession:
    """Wire the shared event pipeline and return a ready (un-submitted) session."""
    from kernel.main import build_agent  # lazy: avoids an import cycle with main

    channel = FrontendChannel()
    executor = SafeExecutor(workspace, approvals=EventApprovalGate(channel))
    agent, _config, _warnings = build_agent(
        executor,
        max_steps=max_steps,
        persist_logs=persist_logs,
        verbose=verbose,
        language_hint=language_hint,
        ui=EventStreamUI(channel),
    )
    return EventSession(channel, agent)


def _launch_stdio(session: EventSession) -> None:
    from kernel.protocol.event_stdio import serve_stdio

    serve_stdio(session)


_BUILTIN: dict[str, Launcher] = {"stdio": _launch_stdio}


def load_launcher(name: str) -> Launcher:
    """Resolve a frontend name to its ``launch(session)`` callable."""
    if name in _BUILTIN:
        return _BUILTIN[name]
    try:
        module = importlib.import_module(f"kvist_frontends.{name}")
    except ModuleNotFoundError as error:
        raise RuntimeError(f"Unknown frontend '{name}': {error}") from error
    launch = getattr(module, "launch", None)
    if launch is None:
        raise RuntimeError(f"Frontend '{name}' exposes no launch(session) callable.")
    return launch


def run_frontend(
    name: str,
    workspace: str,
    *,
    max_steps: int = 150,
    persist_logs: bool = False,
    verbose: bool = False,
    language_hint: str | None = None,
) -> None:
    """Resolve the frontend, build a session, and hand control to the launcher."""
    from kernel.main import resolve_workspace_path  # lazy: avoids an import cycle

    launcher = load_launcher(name)
    workspace_path = resolve_workspace_path(workspace)
    session = make_session(
        workspace_path,
        max_steps=max_steps,
        persist_logs=persist_logs,
        verbose=verbose,
        language_hint=language_hint,
    )
    launcher(session)
