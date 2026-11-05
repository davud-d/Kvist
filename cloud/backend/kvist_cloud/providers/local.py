# -*- coding: utf-8 -*-
"""Local provider: a machine is a runner process with its own folder on this computer.

The runner is started with a clean environment: nothing of the server's (master key, session
secret, database URL) reaches it. Its credential is in ``runner.json``, mode 0600.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
from pathlib import Path

from ..config import REPO_ROOT
from .base import MachineSpec, ProviderError, ProviderState

log = logging.getLogger("kvist_cloud.providers.local")
STOP_TIMEOUT_SECONDS = 15.0
# Variables a runner needs from the host; everything else is left behind.
PASSED_ENV = ("PATH", "LANG", "LC_ALL", "TZ", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")


class LocalProvider:
    name = "local"

    def __init__(
        self,
        root: Path,
        runner_command: tuple[str, ...],
        kvist_command: tuple[str, ...],
        heartbeat_seconds: float,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.root = root
        self.runner_command = runner_command
        self.kvist_command = kvist_command
        self.heartbeat_seconds = heartbeat_seconds
        self.extra_env = dict(extra_env or {})
        self._processes: dict[str, subprocess.Popen] = {}

    def _dir(self, ref: str) -> Path:
        path = (self.root / ref).resolve()
        if path.parent != self.root.resolve():
            raise ProviderError(f"bad machine reference: {ref!r}")
        return path

    async def create(self, spec: MachineSpec) -> str:
        ref = f"m{spec.machine_id}"
        folder = self._dir(ref)
        for sub in ("work", "home", "exports"):
            (folder / sub).mkdir(parents=True, exist_ok=True)
        config = {
            "machine_id": spec.machine_id,
            "gateway_url": spec.gateway_url,
            "token": spec.token,
            "root": str(folder),
            "kvist_command": list(self.kvist_command),
            # The core runs from this repository's checkout here; on real hosting it is installed.
            "kvist_env": {"PYTHONPATH": str(REPO_ROOT)},
            "heartbeat_seconds": self.heartbeat_seconds,
            "machine": {"vcpu": spec.vcpu, "memory_gb": spec.memory_gb, "disk_gb": spec.disk_gb},
        }
        path = folder / "runner.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(config, handle)
        return ref

    def _pid(self, ref: str) -> int | None:
        process = self._processes.get(ref)
        if process is not None:
            return process.pid if process.poll() is None else None
        pid_file = self._dir(ref) / "runner.pid"
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError):
            return None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            return None
        return pid

    async def wake(self, ref: str) -> None:
        folder = self._dir(ref)
        if not (folder / "runner.json").exists():
            raise ProviderError(f"machine {ref} does not exist")
        if self._pid(ref) is not None:
            return
        env = {name: os.environ[name] for name in PASSED_ENV if name in os.environ}
        env.update(self.extra_env)
        env["HOME"] = str(folder / "home")
        env["PYTHONUNBUFFERED"] = "1"
        log_file = open(folder / "runner.log", "ab")  # noqa: SIM115 - handed to the child
        try:
            process = subprocess.Popen(
                [*self.runner_command, "--config", str(folder / "runner.json")],
                cwd=folder,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as error:
            raise ProviderError(f"could not start the runner: {error.strerror}") from error
        finally:
            log_file.close()
        self._processes[ref] = process
        (folder / "runner.pid").write_text(str(process.pid), encoding="utf-8")
        log.info("machine %s woke up (runner pid %s)", ref, process.pid)

    async def sleep(self, ref: str) -> None:
        pid = self._pid(ref)
        process = self._processes.pop(ref, None)
        if pid is None:
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        if process is not None:
            try:
                await asyncio.to_thread(process.wait, STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                os.killpg(pid, signal.SIGKILL)
                await asyncio.to_thread(process.wait)
        else:
            await self._wait_gone(pid)
        (self._dir(ref) / "runner.pid").unlink(missing_ok=True)
        log.info("machine %s is asleep", ref)

    async def _wait_gone(self, pid: int) -> None:
        for _ in range(int(STOP_TIMEOUT_SECONDS * 10)):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(0.1)
        os.killpg(pid, signal.SIGKILL)

    async def delete(self, ref: str) -> None:
        await self.sleep(ref)
        folder = self._dir(ref)
        if folder.exists():
            await asyncio.to_thread(shutil.rmtree, folder)

    async def status(self, ref: str) -> str:
        if not (self._dir(ref) / "runner.json").exists():
            return ProviderState.MISSING
        return ProviderState.RUNNING if self._pid(ref) is not None else ProviderState.STOPPED

    async def close(self) -> None:
        """Server shutdown: put every machine this process woke to sleep."""
        for ref in list(self._processes):
            await self.sleep(ref)
