# -*- coding: utf-8 -*-
"""The ssh command line the proxy runs, and what its failures mean. Pure functions."""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass

from kernel.ssh import wire
from kernel.ssh.paths import RemoteError

_HOST = re.compile(r"^[A-Za-z0-9._@\[\]:-]+$")
_REMOTE_FLAGS_WITH_VALUE = ("--remote", "--remote-workspace")


@dataclass(frozen=True, slots=True)
class Target:
    host: str  # as ssh takes it: ``user@name``
    port: int | None
    workspace: str  # a folder on the server

    @property
    def label(self) -> str:
        return f"{self.host}:{self.port}" if self.port else self.host


def parse_target(spec: str, workspace: str) -> Target:
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
    return Target(spec, int(port) if port else None, workspace)


def remote_command(remote_args: list[str]) -> str:
    """Run the installed core: from PATH when it is there (a non-interactive ssh has the system PATH only), else where the installer puts it."""
    args = shlex.join(remote_args)
    script = (
        f"if command -v kvist-core >/dev/null 2>&1; then exec kvist-core {args}; fi; "
        f'exec "$HOME/.local/share/kvist/kvist-core" {args}'
    )
    # ``sh -c`` first, because the login shell on the server may be fish or csh.
    return "sh -c " + shlex.quote(script)


def ssh_argv(target: Target, remote_args: list[str]) -> list[str]:
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
    return [program, *options, "--", target.host, remote_command(remote_args)]


def forward_args(argv: list[str], workspace: str) -> list[str]:
    """The arguments a frontend gave this proxy, as the core on the server should get them."""
    forwarded: list[str] = []
    skip = False
    for item in argv:
        if skip:
            skip = False
        elif item in _REMOTE_FLAGS_WITH_VALUE or item == "--workspace":
            skip = True
        elif item.startswith(("--remote=", "--remote-workspace=", "--workspace=")):
            continue
        else:
            forwarded.append(item)
    return [*forwarded, "--workspace", workspace]


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
    if "Host key verification failed" in stderr or "REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr:
        return Failure("host_key", True)
    if "Permission denied" in stderr:
        return Failure("auth", True)
    return Failure("unreachable", False)
