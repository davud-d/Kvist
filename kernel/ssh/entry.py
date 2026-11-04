# -*- coding: utf-8 -*-
"""What ``main()`` does with the ``--remote*`` flags: the three roles this program can play for a remote core."""
from __future__ import annotations

import argparse
import functools
import os
import sys

from kernel.ssh import machines
from kernel.ssh.command import parse_target
from kernel.ssh.machines import Machine
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
    run_daemon(workspace, serve=serve, wait_for_credentials=True)


# What looks at or changes a machine's own state, so with --remote it runs on that machine: its sessions,
# memory, provider settings, skills, MCP servers, connections.
_FORWARDED_OPERATIONS = ("session_op", "memory_op", "remote_op", "config_op", "config_info", "skill_op", "mcp_op", "connection_op")
# Anything that is not a frontend starting; the main machine is for starting a frontend only.
_NOT_A_LAUNCH = (
    *_FORWARDED_OPERATIONS, "remote_attach", "remote_serve", "doctor", "commit_message", "complete", "complete_server",
    "selection_task", "tray_op", "acp", "setup", "installer", "machines",
)


def _is_operation(args: argparse.Namespace) -> bool:
    return any(getattr(args, name, None) for name in _FORWARDED_OPERATIONS)


def target_of(args: argparse.Namespace):
    """The server this invocation names, checked."""
    return parse_target(args.remote, args.remote_workspace or "", args.remote_key)


def apply_choice(args: argparse.Namespace, machine: Machine | None, folder: str | None = None, session: str | None = None) -> None:
    """Point this launch at ``machine`` (None: this computer) in ``folder``, opening ``session`` if named."""
    args.open_session = session
    if machine is None:
        args.local = True
        args.machine = args.remote = args.remote_workspace = args.remote_key = None
        return
    args.local = False
    args.machine, args.remote, args.remote_key = machine.name, machine.spec, machine.key
    args.remote_workspace = folder or machine.work_folder


def _has_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _starts_a_frontend(args: argparse.Namespace) -> bool:
    from kernel.config.user_config import get_saved_frontend

    if any(getattr(args, name, None) for name in _NOT_A_LAUNCH):
        return False
    if not _has_terminal():
        return False  # a frontend needs one; without it Kvist falls back to the local terminal anyway
    return (args.frontend or get_saved_frontend()) in ("tui", "claude", "glass", "voice", "opencode")


def resolve_machine(args: argparse.Namespace) -> None:
    """Turn ``--machine NAME``, or the main machine, into the remote flags the rest of the program reads.

    ``--local`` wins over everything. The main machine is only for starting a frontend, never for a headless operation.
    """
    if args.local:
        apply_choice(args, None)
        return
    if args.machine:
        machine = machines.get_machine(args.machine)
        if machine is None:
            raise RemoteError(f"No machine is saved as {args.machine!r}. See them with kvist --machines.")
    elif not args.remote and _starts_a_frontend(args):
        machine = machines.main_machine()
    else:
        return
    if machine is not None:
        apply_choice(args, machine, args.remote_workspace, args.open_session)


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

    target = target_of(args)
    if _is_operation(args):
        from kernel.ssh.proxy import run_forwarded

        return run_forwarded(target, argv)
    if args.frontend == "stdio-multi":
        from kernel.ssh.proxy import Proxy

        return Proxy(target, machine=args.machine, open_session=args.open_session).run()
    return None


def refuse_unsupported_frontend(frontend: str) -> None:
    """``--remote`` with a frontend that runs the agent in its own process cannot work."""
    if frontend not in _FRONTENDS_WITH_A_CORE:
        raise RemoteError(
            "--remote needs a frontend that starts a core: choose one with --frontend "
            "(tui, claude, glass, voice, opencode)."
        )
