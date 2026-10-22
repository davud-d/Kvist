# -*- coding: utf-8 -*-
"""Timeout ceilings, command size limits and per-profile resource limits."""
from __future__ import annotations

from dataclasses import dataclass

from kernel.config.env_utils import read_int_env
from kernel.protocol.protocol import MAX_SHELL_TIMEOUT_SECONDS


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
    # RLIMIT_DATA, not RLIMIT_AS: V8 reserves ~10 GB of address space per
    # WebAssembly memory, so an address-space cap breaks Node (tsx, fetch).
    data_bytes: int
    nproc: int
    fsize_bytes: int
    nofile: int


_MB = 1024 * 1024

_PROFILE_RESOURCE_DEFAULTS: dict[str, ResourceLimits] = {
    # dev: wide enough for builds and test suites, still bounded.
    "dev": ResourceLimits(
        cpu_seconds=120,
        data_bytes=2048 * _MB,
        nproc=4096,
        fsize_bytes=64 * _MB,
        nofile=256,
    ),
    # restricted: the historical tight limits.
    "restricted": ResourceLimits(
        cpu_seconds=8,
        data_bytes=256 * _MB,
        nproc=256,
        fsize_bytes=1 * _MB,
        nofile=64,
    ),
}


def resolve_resource_limits(profile: str) -> ResourceLimits:
    """Profile defaults, overridable via AGENT_RLIMIT_* env vars."""
    base = _PROFILE_RESOURCE_DEFAULTS.get(profile, _PROFILE_RESOURCE_DEFAULTS["dev"])
    cpu = read_int_env("AGENT_RLIMIT_CPU", default=base.cpu_seconds, min_value=1, max_value=3600)
    data_mb = read_int_env(
        "AGENT_RLIMIT_DATA_MB",
        default=base.data_bytes // _MB,
        min_value=64,
        max_value=16384,
    )
    nproc = read_int_env("AGENT_RLIMIT_NPROC", default=base.nproc, min_value=16, max_value=65536)
    return ResourceLimits(
        cpu_seconds=cpu,
        data_bytes=data_mb * _MB,
        nproc=nproc,
        fsize_bytes=base.fsize_bytes,
        nofile=base.nofile,
    )


def _unix_resource_limits(limits: ResourceLimits) -> None:
    """Resource limits for child processes on Unix.

    Called as preexec_fn — runs in the child process before exec.
    RLIMIT_NPROC counts processes per user, so it is a fork-bomb brake,
    not a per-command process cap.
    Each limit is applied on its own: a kernel that rejects one of them
    (macOS rejects some RLIMIT_DATA values) must not leave the others unset.
    """
    try:
        import resource
    except ImportError:
        return

    requested = (
        ("RLIMIT_CPU", limits.cpu_seconds),
        ("RLIMIT_DATA", limits.data_bytes),
        ("RLIMIT_NPROC", limits.nproc),
        ("RLIMIT_FSIZE", limits.fsize_bytes),
        ("RLIMIT_NOFILE", limits.nofile),
    )
    for name, value in requested:
        try:
            resource.setrlimit(getattr(resource, name), (value, value))
        except (ValueError, OSError) as error:
            import sys

            print(
                f"[TerminalExecutor] Failed to set {name}: {error}",
                file=sys.stderr,
            )
