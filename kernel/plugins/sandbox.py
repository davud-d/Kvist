# -*- coding: utf-8 -*-
"""The OS sandbox a skill runner lives in.

Linux only, through the system Bubblewrap the shell sandbox already trusts.
The runner gets: the system runtime read-only, a snapshot of the approved
package read-only, its managed dependency environment read-only, the runner
code read-only, a private /tmp and HOME, and three core-owned directories
(exchange files, the proxy socket, the screen framebuffer). It does not get
the workspace, the person's HOME, the Kvist configuration, their display,
their environment variables, or a network interface besides its own loopback.

On any other platform an executable skill is reported unavailable: a separate
process alone is not isolation, and running skill code with the person's
rights is not an acceptable fallback. Instruction-only skills need none of this.
"""
from __future__ import annotations

import functools
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from kernel.sandbox import RUNTIME_MOUNT_ROOTS, _trusted_system_binary, resolve_bubblewrap_path

RUNNER_DIR = Path(__file__).resolve().parent / "runner"
SYSTEM_PYTHON = Path("/usr/bin/python3")
SANDBOX_ROOT = "/kvist"
_ETC_FILES = ("/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d", "/etc/localtime")
_DISPLAY_ETC = ("/etc/fonts",)
_TLS_ETC = ("/etc/ssl", "/etc/ca-certificates", "/etc/pki")


@functools.cache
def runner_python() -> str | None:
    """The system interpreter runners use, if it is a trusted system binary."""
    if not sys.platform.startswith("linux") or not SYSTEM_PYTHON.exists():
        return None
    try:
        resolved = SYSTEM_PYTHON.resolve(strict=True)
    except OSError:
        return None
    if not _trusted_system_binary(SYSTEM_PYTHON, resolved):
        return None
    return str(SYSTEM_PYTHON)


@functools.cache
def runner_python_version() -> str | None:
    python = runner_python()
    if python is None:
        return None
    try:
        output = subprocess.run(
            [python, "-I", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return output or None


def runner_unavailable_reason() -> str:
    """Why executable skills cannot run here; empty when they can."""
    if not sys.platform.startswith("linux"):
        return ("executable skills need a verified OS sandbox, which Kvist provides only on Linux "
                "(instruction-only skills work everywhere)")
    if resolve_bubblewrap_path() is None:
        return "executable skills need Bubblewrap (bwrap) installed in a system directory"
    if runner_python() is None or runner_python_version() is None:
        return "executable skills need the system Python at /usr/bin/python3"
    return ""


@dataclass(frozen=True, slots=True)
class RunnerSpec:
    skill_dir: Path  # verified snapshot of the approved package
    entrypoint: str
    exchange_dir: Path
    rpc_fd: int
    passwd_file: Path
    group_file: Path
    env_dir: Path | None = None
    net_dir: Path | None = None
    proxy_port: int = 0
    screen_dir: Path | None = None
    screen_size: str = "1280x800"


def build_runner_argv(spec: RunnerSpec) -> list[str]:
    bwrap = resolve_bubblewrap_path()
    python = runner_python()
    if bwrap is None or python is None:
        raise RuntimeError(runner_unavailable_reason() or "skill runner sandbox is unavailable")
    argv = [
        bwrap, "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL",
        "--clearenv",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/tmp/home",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--setenv", "PYTHONUNBUFFERED", "1",
    ]
    for root in RUNTIME_MOUNT_ROOTS:
        if Path(root).exists():
            argv += ["--ro-bind", root, root]
    for path in _ETC_FILES:
        if Path(path).exists():
            argv += ["--ro-bind", str(Path(path).resolve()), path]
    argv += [
        "--ro-bind", str(spec.passwd_file), "/etc/passwd",
        "--ro-bind", str(spec.group_file), "/etc/group",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/dev/shm",
        "--tmpfs", "/tmp",
        "--dir", "/tmp/home",
        "--ro-bind", str(RUNNER_DIR), f"{SANDBOX_ROOT}/runner",
        "--ro-bind", str(spec.skill_dir), f"{SANDBOX_ROOT}/skill",
        "--bind", str(spec.exchange_dir), f"{SANDBOX_ROOT}/exchange",
    ]
    runner_args = ["--fd", str(spec.rpc_fd), "--skill", f"{SANDBOX_ROOT}/skill", "--entry", spec.entrypoint]
    if spec.env_dir is not None:
        argv += ["--ro-bind", str(spec.env_dir), f"{SANDBOX_ROOT}/env"]
        runner_args += ["--site", f"{SANDBOX_ROOT}/env/site"]
    if spec.net_dir is not None and spec.proxy_port:
        argv += ["--ro-bind", str(spec.net_dir), f"{SANDBOX_ROOT}/net"]
        for path in _TLS_ETC:
            if Path(path).exists():
                argv += ["--ro-bind", path, path]
        runner_args += ["--proxy-socket", f"{SANDBOX_ROOT}/net/proxy.sock", "--proxy-port", str(spec.proxy_port)]
    if spec.screen_dir is not None:
        argv += ["--bind", str(spec.screen_dir), f"{SANDBOX_ROOT}/screen"]
        for path in _DISPLAY_ETC:
            if Path(path).exists():
                argv += ["--ro-bind", path, path]
        runner_args += ["--display", ":99", "--screen-dir", f"{SANDBOX_ROOT}/screen",
                        "--screen-size", spec.screen_size]
    argv += ["--chdir", f"{SANDBOX_ROOT}/skill", "--", python, "-I", "-B",
             f"{SANDBOX_ROOT}/runner/skill_runner.py", *runner_args]
    return argv
