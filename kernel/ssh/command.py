# -*- coding: utf-8 -*-
"""The ssh command line the proxy runs, and what its failures mean. Pure functions."""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass

from kernel.ssh import wire
from kernel.ssh.paths import RemoteError

_HOST = re.compile(r"^[A-Za-z0-9._@\[\]:-]+$")
_REMOTE_FLAGS_WITH_VALUE = ("--remote", "--remote-workspace", "--remote-key", "--machine", "--open-session")


@dataclass(frozen=True, slots=True)
class Target:
    host: str  # as ssh takes it: ``user@name``
    port: int | None
    workspace: str  # a folder on the server
    key: str | None = None  # a private key file to sign in with; None means whatever ssh would use

    @property
    def label(self) -> str:
        return f"{self.host}:{self.port}" if self.port else self.host


def parse_target(spec: str, workspace: str, key: str | None = None) -> Target:
    """``user@host``, ``user@host:2222`` or ``user@[::1]:2222``; anything ssh could read as an option is refused."""
    spec = spec.strip()
    if not spec or spec.startswith("-") or not _HOST.match(spec):
        raise RemoteError(f"{spec!r} is not a host name Kvist can pass to ssh.")
    if not workspace.strip():
        raise RemoteError("Name the folder on the server with --remote-workspace.")
    port = None
    bracketed = re.match(r"^(?P<host>.+\])(?::(?P<port>\d+))?$", spec)
    if bracketed:
        # ssh takes an IPv6 address bare; the brackets only separate it from the port.
        spec, port = bracketed.group("host").replace("[", "").replace("]", ""), bracketed.group("port")
    elif spec.count(":") == 1:
        spec, port = spec.split(":")
        if not port.isdigit():
            raise RemoteError(f"{port!r} is not a port number.")
    return Target(spec, int(port) if port else None, workspace, key or None)


def remote_command(remote_args: list[str]) -> str:
    """Run the installed core: where the installer (or a copy Kvist made) put it, else whatever is in PATH (a non-interactive ssh has the system PATH only)."""
    args = shlex.join(remote_args)
    script = (
        'core="$HOME/.local/share/kvist/kvist-core"; '
        f'if [ -x "$core" ]; then exec "$core" {args}; fi; '
        f"if command -v kvist-core >/dev/null 2>&1; then exec kvist-core {args}; fi; "
        "echo 'kvist-core is neither in PATH nor in ~/.local/share/kvist' >&2; exit 127"
    )
    # ``sh -c`` first, because the login shell on the server may be fish or csh.
    return "sh -c " + shlex.quote(script)


def ssh_prefix(target: Target) -> list[str]:
    """ssh as Kvist always runs it: no terminal, no questions, only the chosen key, up to the host."""
    program = os.environ.get("KVIST_SSH") or "ssh"
    options = [
        "-T",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
    ]
    if target.port:
        options += ["-p", str(target.port)]
    if target.key:
        # Only this key: an agent full of others would run into the server's attempt limit.
        options += ["-i", target.key, "-o", "IdentitiesOnly=yes"]
    return [program, *options, "--", target.host]


def ssh_argv(target: Target, remote_args: list[str]) -> list[str]:
    return [*ssh_prefix(target), remote_command(remote_args)]


def forward_args(argv: list[str], workspace: str) -> list[str]:
    """The arguments a frontend gave this proxy, as the core on the server should get them."""
    forwarded: list[str] = []
    skip = False
    for item in argv:
        if skip:
            skip = False
        elif item in _REMOTE_FLAGS_WITH_VALUE or item == "--workspace":
            skip = True
        elif item.startswith(("--remote=", "--remote-workspace=", "--remote-key=", "--machine=", "--open-session=", "--workspace=")):
            continue
        else:
            forwarded.append(item)
    return [*forwarded, "--workspace", workspace]


def meaningful(stderr: str) -> str:
    """What ssh said about the failure itself, without the warnings it prints about the server's age or a first connection."""
    kept = [line for line in stderr.splitlines() if line.strip() and not line.startswith(("**", "Warning:", "WARNING:"))]
    return "\n".join(kept).strip()


@dataclass(frozen=True, slots=True)
class Failure:
    kind: str
    definitive: bool  # retrying cannot help until the person does something


def classify(returncode: int, stderr: str, *, shim_state: str | None) -> Failure:
    """What a finished ssh attempt says. A server that is rebooting refuses connections for a while, so that is not final."""
    if shim_state == "no_service" or returncode == wire.EXIT_NO_SERVICE:
        return Failure("no_service", True)
    if returncode == 127:
        return Failure("not_found", True)
    if "unrecognized arguments" in stderr and "--remote" in stderr:
        # An installed Kvist that predates the remote core: argparse refuses the flag and exits.
        return Failure("old_kvist", True)
    if "Host key verification failed" in stderr or "REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr:
        return Failure("host_key", True)
    if "Permission denied" in stderr:
        return Failure("auth", True)
    return Failure("unreachable", False)


def call_op(target: Target, operation: str) -> tuple[int, dict | None, str]:
    """One ``--remote-op`` on the server: its exit code, its JSON answer if it gave one, and ssh's own complaints."""
    command = ssh_argv(target, ["--remote-op", operation, "--workspace", target.workspace])
    done = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", timeout=120, check=False)
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    answer = wire.decode(lines[-1].encode("utf-8")) if lines else None
    return done.returncode, answer, done.stderr
