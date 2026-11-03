# -*- coding: utf-8 -*-
"""What ``main()`` does with the ``--remote*`` flags: the three roles this program can play for a remote core."""
from __future__ import annotations

import argparse
import functools
import os
import sys

from kernel.ssh.command import parse_target
from kernel.ssh.paths import RemoteError

# Frontends that start this program as their core and talk to it over stdio.
_FRONTENDS_WITH_A_CORE = ("tui", "claude", "glass", "voice", "opencode", "stdio-multi")


def _server_workspace(args: argparse.Namespace) -> str:
    return os.path.abspath(os.path.expanduser(args.workspace or "."))


def _serve_daemon(args: argparse.Namespace) -> None:
    from kernel.main import STDIO_COMMANDS, build_agent
    from kernel.protocol.event_stdio import launch_multi
    from kernel.ssh.daemon import run_daemon

    workspace = _server_workspace(args)
    if not os.path.isdir(workspace):
        raise RemoteError(f"{workspace} is not a folder on this machine.")
    models = {"fast": args.fast_model, "smart": args.smart_model} if args.fast_model and args.smart_model else None
    serve = functools.partial(
        launch_multi,
        build_agent_fn=build_agent,
        provider_override=args.provider,
        models_override=models,
        routed_role=None if args.role == "auto" else args.role,
        approval_mode=args.approval_mode,
        commands=STDIO_COMMANDS,
    )
    run_daemon(workspace, serve=serve)


def _is_operation(args: argparse.Namespace) -> bool:
    return bool(args.session_op or args.memory_op or args.remote_op)


def dispatch(args: argparse.Namespace, argv: list[str]) -> int | None:
    """An exit code when this invocation is a remote role, None when the ordinary program should go on."""
    try:
        return _dispatch(args, argv)
    except (RemoteError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace, argv: list[str]) -> int | None:
    if args.remote_attach:
        from kernel.ssh.attach import run_attach

        return run_attach(_server_workspace(args))
    if args.remote_serve:
        _serve_daemon(args)
        return 0
    if not args.remote:
        if args.remote_op:
            from kernel.ssh.ops import run_op

            return run_op(args.remote_op, _server_workspace(args))
        return None

    target = parse_target(args.remote, args.remote_workspace or "")
    if _is_operation(args):
        from kernel.ssh.proxy import run_forwarded

        return run_forwarded(target, argv)
    if args.frontend == "stdio-multi":
        from kernel.ssh.proxy import Proxy

        return Proxy(target).run()
    return None


def refuse_unsupported_frontend(frontend: str) -> None:
    """``--remote`` with a frontend that runs the agent in its own process cannot work."""
    if frontend not in _FRONTENDS_WITH_A_CORE:
        raise RemoteError(
            "--remote needs a frontend that starts a core: choose one with --frontend "
            "(tui, claude, glass, voice, opencode)."
        )
