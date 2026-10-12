# -*- coding: utf-8 -*-
"""The managed installation of a skill's pinned Python dependencies.

Only the person starts it (``kvist --skill-op install-deps <id>``); neither the
model nor a skill can. It runs in its own Bubblewrap sandbox: the target
directory is the only writable path, the network is the core's proxy limited
to the package index, and only binary wheels of the exact pinned versions are
installed, so no package code runs during installation. The result is
installed into a staging directory and renamed into place, then marked ready
with the pins it holds; a skill is unavailable until that marker matches its
manifest.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable

from kernel.plugins.manifest import Manifest
from kernel.plugins.net_broker import NetBroker, NetPolicy, TaskServices
from kernel.plugins.registry import SkillRegistry
from kernel.plugins.sandbox import RUNNER_DIR, runner_python, runner_unavailable_reason
from kernel.resources import ManagedProcess
from kernel.sandbox import RUNTIME_MOUNT_ROOTS, resolve_bubblewrap_path

PACKAGE_INDEX = ["pypi.org", "files.pythonhosted.org"]
INSTALL_TIMEOUT = 900.0
_PROXY_PORT = 8899


class DependencyError(RuntimeError):
    pass


def install_dependencies(registry: SkillRegistry, manifest: Manifest,
                         progress: Callable[[str], None] = lambda line: None) -> Path:
    reason = runner_unavailable_reason()
    if reason:
        raise DependencyError(reason)
    if not manifest.python_deps:
        raise DependencyError(f"{manifest.id} declares no Python dependencies")
    final = registry.env_dir(manifest)
    staging = final.with_name(f"{final.name}.staging-{uuid.uuid4().hex[:8]}")
    staging.mkdir(parents=True, mode=0o700)
    # A unix socket path is limited to ~108 bytes, so the proxy socket lives
    # in the short private runtime directory, not next to the environment.
    net_dir = Path(tempfile.mkdtemp(prefix="kvist-deps-", dir=os.environ.get("XDG_RUNTIME_DIR") or None))
    broker = NetBroker(net_dir / "proxy.sock", NetPolicy(allow_services=False, domains=list(PACKAGE_INDEX)),
                       TaskServices())
    broker.start()
    try:
        process = ManagedProcess(_argv(staging, net_dir, manifest.python_deps), label=f"install {manifest.id}",
                                 env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, memory_mb=2048, pids_max=256)
        process.start(f"deps-{manifest.id}")
        deadline = time.monotonic() + INSTALL_TIMEOUT
        printed = 0
        while process.alive() and time.monotonic() < deadline:
            time.sleep(0.5)
            output = process.output_tail(200_000)
            if len(output) > printed:
                for line in output[printed:].splitlines():
                    progress(line)
                printed = len(output)
        if process.alive():
            process.stop(grace=2.0)
            raise DependencyError(f"installation did not finish within {INSTALL_TIMEOUT:.0f} s")
        process.stop(grace=0.1)
        if process.proc is None or process.proc.returncode != 0:
            raise DependencyError(f"pip failed:\n{process.output_tail(4000)}")
        _verify(staging / "site", manifest.python_deps)
        if final.exists():
            shutil.rmtree(final)
        staging.rename(final)
        final.with_suffix(".ready.json").write_text(json.dumps({
            "pins": list(manifest.python_deps), "installed_at": time.time(),
            "denied_destinations": [d for d in broker.recent(200) if not d["allowed"]],
        }), encoding="utf-8")
        return final
    finally:
        broker.close()
        shutil.rmtree(net_dir, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)


def _argv(staging: Path, net_dir: Path, pins: tuple[str, ...]) -> list[str]:
    bwrap = resolve_bubblewrap_path()
    python = runner_python()
    assert bwrap is not None and python is not None
    argv = [bwrap, "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL", "--clearenv",
            "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/tmp/home", "--setenv", "LANG", "C.UTF-8"]
    for root in RUNTIME_MOUNT_ROOTS:
        if Path(root).exists():
            argv += ["--ro-bind", root, root]
    for path in ("/etc/ld.so.cache", "/etc/ssl", "/etc/ca-certificates", "/etc/pki", "/etc/localtime"):
        if Path(path).exists():
            argv += ["--ro-bind", path, path]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/home",
             "--ro-bind", str(RUNNER_DIR), "/kvist/runner",
             "--bind", str(staging), "/kvist/env",
             "--ro-bind", str(net_dir), "/kvist/net",
             "--chdir", "/tmp", "--",
             python, "-I", "-B", "/kvist/runner/kvist_deps.py", "--target", "/kvist/env/site",
             "--proxy-socket", "/kvist/net/proxy.sock", "--proxy-port", str(_PROXY_PORT), "--", *pins]
    return argv


def _verify(site: Path, pins: tuple[str, ...]) -> None:
    installed = {}
    for info in site.glob("*.dist-info"):
        name, _, version = info.name[: -len(".dist-info")].rpartition("-")
        installed[_normalize(name)] = version
    missing = []
    for pin in pins:
        name, _, version = pin.partition("==")
        if installed.get(_normalize(name)) != version:
            missing.append(pin)
    if missing:
        raise DependencyError(f"after installation these pins are missing or differ: {', '.join(missing)}")


def _normalize(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")
