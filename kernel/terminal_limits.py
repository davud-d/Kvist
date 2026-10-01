# -*- coding: utf-8 -*-
"""Timeout ceilings, command size limits and per-profile resource limits."""
from __future__ import annotations

from dataclasses import dataclass

from kernel.env_utils import read_int_env
from kernel.protocol import MAX_SHELL_TIMEOUT_SECONDS


# Per-profile timeout ceiling: restricted keeps the old tight cap, dev
# allows real builds and test runs.
_TIMEOUT_CEILINGS: dict[str, float] = {
    "restricted": 30.0,
    "dev": MAX_SHELL_TIMEOUT_SECONDS,
}
_MAX_COMMAND_ITEMS = 512
_MAX_COMMAND_CHARS = 2000


@dataclass(slots=True)
class ResourceLimits:
    """Unix rlimits applied to child processes via preexec_fn."""

    cpu_seconds: int
    address_space_bytes: int
    nproc: int
    fsize_bytes: int
    nofile: int


_MB = 1024 * 1024

_PROFILE_RESOURCE_DEFAULTS: dict[str, ResourceLimits] = {
    # dev: wide enough for builds and test suites, still bounded.
    "dev": ResourceLimits(
        cpu_seconds=120,
        address_space_bytes=2048 * _MB,
        nproc=4096,
        fsize_bytes=64 * _MB,
        nofile=256,
    ),
    # restricted: the historical tight limits.
    "restricted": ResourceLimits(
        cpu_seconds=8,
        address_space_bytes=256 * _MB,
        nproc=256,
        fsize_bytes=1 * _MB,
        nofile=64,
    ),
}


def resolve_resource_limits(profile: str) -> ResourceLimits:
    """Profile defaults, overridable via AGENT_RLIMIT_* env vars."""
    base = _PROFILE_RESOURCE_DEFAULTS.get(profile, _PROFILE_RESOURCE_DEFAULTS["dev"])
    cpu = read_int_env("AGENT_RLIMIT_CPU", default=base.cpu_seconds, min_value=1, max_value=3600)
    as_mb = read_int_env(
        "AGENT_RLIMIT_AS_MB",
        default=base.address_space_bytes // _MB,
        min_value=64,
        max_value=16384,
    )
    nproc = read_int_env("AGENT_RLIMIT_NPROC", default=base.nproc, min_value=16, max_value=65536)
    return ResourceLimits(
        cpu_seconds=cpu,
        address_space_bytes=as_mb * _MB,
        nproc=nproc,
        fsize_bytes=base.fsize_bytes,
        nofile=base.nofile,
    )


def _unix_resource_limits(limits: ResourceLimits) -> None:
    """Resource limits for child processes on Unix.

    Called as preexec_fn — runs in the child process before exec.
    RLIMIT_NPROC counts processes per user, so it is a fork-bomb brake,
    not a per-command process cap.
    """
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
        resource.setrlimit(
            resource.RLIMIT_AS,
            (limits.address_space_bytes, limits.address_space_bytes),
        )
        resource.setrlimit(resource.RLIMIT_NPROC, (limits.nproc, limits.nproc))
        resource.setrlimit(resource.RLIMIT_FSIZE, (limits.fsize_bytes, limits.fsize_bytes))
        resource.setrlimit(resource.RLIMIT_NOFILE, (limits.nofile, limits.nofile))
    except ImportError:
        pass
    except Exception:
        import sys
        print(
            "[TerminalExecutor] Failed to set resource limits",
            file=sys.stderr,
        )
