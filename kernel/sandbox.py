# -*- coding: utf-8 -*-
"""OS-level sandbox selection and Bubblewrap command construction.

``auto`` selects Bubblewrap on Linux when a system installation is present.
When it is unavailable, the terminal applies an approval-only restricted
fallback for commands capable of arbitrary code execution.  Explicit
``workspace`` mode remains a user-selected weaker boundary.
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
from enum import Enum
from pathlib import Path

from kernel.secret_paths import enumerate_secret_targets


class SandboxMode(Enum):
    NONE = "none"
    WORKSPACE_ONLY = "workspace_only"
    BUBBLEWRAP = "bubblewrap"
    HOST = "host"


def host_network_launcher(network_allowed: bool) -> str | None:
    """Never treat lack of an OS network boundary as permission to go online."""
    if sys.platform.startswith("linux"):
        launcher = resolve_bubblewrap_path()
        if launcher:
            return launcher
    if sys.platform == "darwin" and not network_allowed:
        launcher = Path("/usr/bin/sandbox-exec")
        if launcher.is_file() and _trusted_system_binary(launcher, launcher.resolve()):
            return str(launcher)
    if not network_allowed:
        raise ValueError(
            "Host execution without network is unavailable: this platform has no "
            "supported network isolation backend. No command was started. "
            "Network access requires a separate explicit request and user approval."
        )
    return None


def _wrap_host_command(
    command: list[str], cwd: Path, network_allowed: bool, launcher: str | None,
) -> list[str]:
    if not launcher:
        if not network_allowed:
            raise ValueError("Host execution requires an OS boundary to deny network access")
        return command
    if sys.platform == "darwin":
        return [launcher, "-p", "(version 1)(allow default)(deny network*)", *command]
    wrapped = [
        launcher, "--die-with-parent", "--new-session", "--unshare-pid",
        "--unshare-ipc", "--unshare-uts", "--bind", "/", "/", "--proc", "/proc",
    ]
    if not network_allowed:
        wrapped.append("--unshare-net")
    return [*wrapped, "--chdir", str(cwd), "--", *command]


_SYSTEM_EXEC_PATH = os.pathsep.join(
    path for path in ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")
    if Path(path).is_dir()
)


def normalize_sandbox_request(requested: str | None = None) -> str:
    value = requested if requested is not None else os.getenv("AGENT_SANDBOX_MODE", "auto")
    value = (value or "auto").strip().casefold()
    if value in {"workspace", "workspace_only"}:
        return "workspace"
    if value in {"auto", "bubblewrap"}:
        return value
    return "invalid"


def resolve_bubblewrap_path() -> str | None:
    """Resolve bwrap from fixed system directories, never a project PATH."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        found = shutil.which("bwrap", path=_SYSTEM_EXEC_PATH)
    except TypeError:
        # Compatibility with simple test doubles for shutil.which.  The real
        # stdlib implementation always takes ``path`` and production lookup
        # therefore remains restricted to system directories.
        found = shutil.which("bwrap")
    if not found:
        return None
    lookup = Path(found)
    if not lookup.is_absolute():
        return None
    try:
        resolved = lookup.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file() or not _trusted_system_binary(lookup, resolved):
        return None
    return str(resolved)


def _trusted_system_binary(lookup: Path, resolved: Path) -> bool:
    roots: list[Path] = []
    for raw in ("/usr", "/bin", "/sbin"):
        try:
            root = Path(raw).resolve(strict=True)
        except OSError:
            continue
        if root not in roots:
            roots.append(root)
    try:
        parent = lookup.parent.resolve(strict=True)
        target = resolved.resolve(strict=True)
    except OSError:
        return False
    root = next(
        (
            item
            for item in roots
            if _is_relative_to(parent, item) and _is_relative_to(target, item)
        ),
        None,
    )
    if root is None:
        return False
    paths = [target]
    for start in (parent, target.parent):
        current = start
        while True:
            if current not in paths:
                paths.append(current)
            if current == root:
                break
            if current.parent == current:
                return False
            current = current.parent
    return not any(_user_mutable(path) for path in paths)


def _is_relative_to(path: Path, boundary: Path) -> bool:
    try:
        path.relative_to(boundary)
    except ValueError:
        return False
    return True


def _user_mutable(path: Path) -> bool:
    try:
        info = path.stat()
    except OSError:
        return True
    if hasattr(os, "geteuid") and info.st_uid == os.geteuid():
        return True
    try:
        if os.access(path, os.W_OK, effective_ids=True):
            return True
    except TypeError:
        if os.access(path, os.W_OK):
            return True
    if info.st_mode & stat.S_IWOTH:
        return True
    if not hasattr(os, "getegid"):
        return False
    try:
        groups = {os.getegid(), *os.getgroups()}
    except OSError:
        groups = {os.getegid()}
    return bool(info.st_mode & stat.S_IWGRP and info.st_gid in groups)


def bubblewrap_available() -> bool:
    return resolve_bubblewrap_path() is not None


def detect_sandbox_mode(requested: str | None = None) -> tuple[SandboxMode, str]:
    """Resolve the effective sandbox mode and a user-facing security note."""
    raw = requested if requested is not None else os.getenv("AGENT_SANDBOX_MODE", "auto")
    request = normalize_sandbox_request(requested)
    available = bubblewrap_available()

    if request == "bubblewrap":
        if available:
            return SandboxMode.BUBBLEWRAP, ""
        return (
            SandboxMode.WORKSPACE_ONLY,
            "AGENT_SANDBOX_MODE=bubblewrap was requested, but a trusted bwrap executable "
            "was not found; shell execution is blocked rather than silently degraded.",
        )

    if request == "workspace":
        return (
            SandboxMode.WORKSPACE_ONLY,
            "Explicit workspace-only mode is not an OS sandbox; approved child processes "
            "run with the current user's host permissions.",
        )

    if request == "auto":
        if available:
            return SandboxMode.BUBBLEWRAP, ""
        return (
            SandboxMode.WORKSPACE_ONLY,
            "Bubblewrap is unavailable; auto mode is using a restricted approval-only "
            "workspace fallback for arbitrary-code commands.",
        )

    # Invalid values must not silently opt into the weak mode.  Resolve them
    # like auto and surface the configuration error.
    if available:
        return (
            SandboxMode.BUBBLEWRAP,
            f"Unknown AGENT_SANDBOX_MODE={raw!r}; secure auto mode selected Bubblewrap.",
        )
    return (
        SandboxMode.WORKSPACE_ONLY,
        f"Unknown AGENT_SANDBOX_MODE={raw!r}; Bubblewrap is unavailable, so the restricted "
        "automatic fallback is active.",
    )


def sandbox_network_allowed(value: str | None = None) -> bool:
    """Return whether the strong sandbox may share the host network."""
    raw = value if value is not None else os.getenv("AGENT_SANDBOX_NET", "deny")
    return (raw or "deny").strip().casefold() == "allow"


RUNTIME_MOUNT_ROOTS = ("/usr", "/bin", "/sbin", "/lib", "/lib64")


def _append_runtime_mounts(wrapped: list[str]) -> None:
    """Mount only system runtime trees needed by ordinary dynamically linked CLIs."""
    for raw_path in RUNTIME_MOUNT_ROOTS:
        path = Path(raw_path)
        if path.exists():
            wrapped.extend(("--ro-bind", raw_path, raw_path))
    for raw_path in ("/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d", "/etc/localtime"):
        path = Path(raw_path)
        if path.exists():
            wrapped.extend(("--ro-bind", str(path.resolve()), raw_path))


def _append_network_mounts(wrapped: list[str]) -> None:
    for raw_path in ("/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/gai.conf"):
        path = Path(raw_path)
        if path.exists():
            wrapped.extend(("--ro-bind", str(path.resolve()), raw_path))
    for raw_path in ("/etc/ssl/certs", "/etc/ca-certificates", "/etc/pki/ca-trust"):
        path = Path(raw_path)
        if path.exists():
            wrapped.extend(("--ro-bind", raw_path, raw_path))


def _append_secret_masks(
    wrapped: list[str],
    workspace: Path,
    targets: tuple[list[Path], list[Path]] | None,
) -> None:
    """Hide protected workspace secrets so arbitrary child code cannot read them.

    Directories become empty tmpfs mounts; files are shadowed by ``/dev/null``,
    which yields an empty, unreadable-for-write object.  Masking the real
    target also neutralises symlinks and runtime-constructed paths that resolve
    to it — the OS view, not argv scanning, is the boundary.
    """
    secret_files, secret_dirs = targets if targets is not None else enumerate_secret_targets(workspace)
    for path in secret_dirs:
        wrapped.extend(("--tmpfs", str(path)))
    for path in secret_files:
        wrapped.extend(("--ro-bind", "/dev/null", str(path)))


def validated_external_tool_path(
    path: Path,
    workspace: Path | None = None,
) -> Path | None:
    """Resolve one narrowly scoped external tool directory.

    Validation lives at the Bubblewrap boundary as well as configuration
    loading so direct callers cannot turn ``tool_paths`` into an arbitrary
    host-filesystem bind.
    """
    if not path.is_absolute():
        return None
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (OSError, RuntimeError):
        return None
    if not stat.S_ISDIR(info.st_mode):
        return None
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return None
    if len(resolved.parts) <= 2:
        return None

    for raw in ("/etc", "/var", "/run", "/proc", "/sys", "/dev"):
        blocked = Path(raw)
        if resolved == blocked or _is_relative_to(resolved, blocked):
            return None

    home: Path | None = None
    if hasattr(os, "geteuid"):
        try:
            import pwd

            home = Path(pwd.getpwuid(os.geteuid()).pw_dir).resolve(strict=True)
        except (KeyError, OSError):
            home = None
    if home is None:
        try:
            home = Path.home().resolve(strict=True)
        except OSError:
            home = None
    if home is not None:
        if resolved == home or _is_relative_to(home, resolved):
            return None
        if _is_relative_to(resolved, home.parent) and not _is_relative_to(resolved, home):
            return None

    if workspace is not None:
        try:
            root = workspace.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if (
            resolved == root
            or _is_relative_to(resolved, root)
            or _is_relative_to(root, resolved)
        ):
            return None
    return resolved


def _append_tool_path_mounts(
    wrapped: list[str],
    tool_paths: tuple[Path, ...],
    workspace: Path,
) -> None:
    """Read-only bind explicitly configured, narrowly scoped tool directories."""
    seen: set[str] = set()
    for path in tool_paths:
        resolved = validated_external_tool_path(path, workspace)
        if resolved is None:
            raise ValueError(f"Refusing unsafe external tool path: {path}")
        value = str(resolved)
        if value in seen:
            continue
        seen.add(value)
        wrapped.extend(("--ro-bind", value, value))


def wrap_command_for_sandbox(
    command: list[str],
    mode: SandboxMode,
    workspace: Path,
    cwd: Path,
    *,
    bwrap_path: str | None = None,
    network_allowed: bool | None = None,
    tool_paths: tuple[Path, ...] = (),
    links: tuple[tuple[str, str], ...] = (),
    secret_targets: tuple[list[Path], list[Path]] | None = None,
) -> list[str]:
    """Wrap a validated command in a minimal Bubblewrap filesystem.

    ``links`` recreates single symlinks (target, path) inside the sandbox
    without exposing anything else beside them. ``secret_targets`` is the
    enumeration the spawn-time context digest was built from; walking the
    workspace again milliseconds later cost seconds on every command.
    """
    if mode is SandboxMode.HOST:
        return _wrap_host_command(command, cwd, bool(network_allowed), bwrap_path)
    if mode is not SandboxMode.BUBBLEWRAP:
        return command

    try:
        workspace = workspace.resolve(strict=True)
        cwd = cwd.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError(f"Sandbox workspace or cwd cannot be resolved: {error}") from error
    if workspace == Path("/"):
        raise ValueError("Refusing to bind the complete host root as a workspace")
    if not workspace.is_dir() or not cwd.is_dir() or not _is_relative_to(cwd, workspace):
        raise ValueError("Sandbox cwd must be a directory inside the workspace")
    binary = bwrap_path or resolve_bubblewrap_path()
    if not binary:
        raise ValueError("Bubblewrap mode requires a trusted absolute bwrap executable")

    wrapped = [
        binary,
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup-try",
    ]
    if network_allowed is None:
        network_allowed = sandbox_network_allowed()
    if not network_allowed:
        wrapped.append("--unshare-net")

    _append_runtime_mounts(wrapped)
    if network_allowed:
        _append_network_mounts(wrapped)
    wrapped.extend(
        (
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--dir", "/tmp/kvist-home",
            "--setenv", "HOME", "/tmp/kvist-home",
            "--setenv", "TMPDIR", "/tmp",
            "--bind", str(workspace), str(workspace),
        )
    )
    _append_tool_path_mounts(wrapped, tool_paths, workspace)
    for target, path in links:
        wrapped.extend(("--symlink", target, path))
    internal_dir = workspace / ".kvist"
    wrapped.extend(("--tmpfs", str(internal_dir)))
    # Secret masks come after the workspace bind so they shadow the real files.
    _append_secret_masks(wrapped, workspace, secret_targets)
    wrapped.extend(("--chdir", str(cwd), "--", *command))
    return wrapped
