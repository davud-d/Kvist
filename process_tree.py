# -*- coding: utf-8 -*-
"""Supervised process execution: tree termination and bounded output capture.

Two host problems are solved here:

* A child that calls ``setsid()``/``start_new_session`` escapes the parent's
  process group, so ``killpg`` on the leader misses it.  On Linux we place the
  whole subtree in a dedicated cgroup v2 scope and use ``cgroup.kill`` — the
  kernel SIGKILLs every member, detached or not.  Inside Bubblewrap the PID
  namespace already guarantees this (killing the namespace init reaps all),
  and the cgroup adds kill-on-completion for strays.

* ``Popen.communicate`` buffers the entire output before any truncation, so a
  runaway child can exhaust parent memory, and a second unbounded
  ``communicate`` after a timeout blocks until a detached child closes the
  inherited pipe.  Here two daemon readers drain each pipe into a fixed-size
  buffer, discarding the overflow, and the tree is always torn down before the
  readers are joined so a held-open pipe cannot wedge the parent.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_CGROUP_ROOT = Path("/sys/fs/cgroup")
_KILL_POLL_INTERVAL = 0.02
_KILL_DEADLINE = 2.0
_READER_JOIN_GRACE = 2.0
_REAP_DEADLINE = 2.0
_CHUNK = 65536


@dataclass(slots=True)
class SupervisedResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool


class _BoundedReader(threading.Thread):
    """Drain a pipe into a fixed-size buffer; keep reading and discard overflow."""

    def __init__(self, stream: Any, limit: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._limit = max(0, limit)
        self._chunks: list[bytes] = []
        self._size = 0
        self.truncated = False

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(_CHUNK)
                if not chunk:
                    break
                if self._size < self._limit:
                    room = self._limit - self._size
                    self._chunks.append(chunk[:room])
                    self._size += min(len(chunk), room)
                    if len(chunk) > room:
                        self.truncated = True
                else:
                    self.truncated = True
        except (OSError, ValueError):
            pass
        finally:
            try:
                self._stream.close()
            except OSError:
                pass

    def data(self) -> bytes:
        return b"".join(self._chunks)


class _CgroupScope:
    """A leaf cgroup v2 scope with kill-on-demand for the whole subtree."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def create(cls) -> "_CgroupScope | None":
        if not (_CGROUP_ROOT / "cgroup.controllers").exists():
            return None
        try:
            own = Path("/proc/self/cgroup").read_text().strip()
        except OSError:
            return None
        rel = own.split("::")[-1].lstrip("/")
        current = _CGROUP_ROOT / rel
        name = f"kvist-{os.getpid()}-{uuid.uuid4().hex[:12]}"
        for base in (current.parent, current):
            candidate = base / name
            try:
                candidate.mkdir()
            except (OSError, ValueError):
                continue
            if (candidate / "cgroup.kill").exists() and (candidate / "cgroup.procs").exists():
                return cls(candidate)
            try:
                candidate.rmdir()
            except OSError:
                pass
        return None

    def join_current_process(self) -> None:
        """Runs inside the forked child (preexec): move self into the scope."""
        fd = os.open(str(self.path / "cgroup.procs"), os.O_WRONLY)
        try:
            os.write(fd, b"0")
        finally:
            os.close(fd)

    def kill(self) -> None:
        try:
            with open(self.path / "cgroup.kill", "w") as handle:
                handle.write("1")
        except OSError:
            pass
        deadline = time.monotonic() + _KILL_DEADLINE
        while time.monotonic() < deadline:
            try:
                members = (self.path / "cgroup.procs").read_text().strip()
            except OSError:
                break
            if not members:
                break
            time.sleep(_KILL_POLL_INTERVAL)

    def close(self) -> None:
        try:
            self.path.rmdir()
        except OSError:
            pass


def _terminate_process_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def run_supervised(
    argv: list[str],
    *,
    cwd: Any,
    env: dict[str, str],
    timeout: float,
    max_capture_bytes: int,
    rlimit_setter: Callable[[], None] | None,
    is_windows: bool,
    use_cgroup: bool,
) -> SupervisedResult:
    """Spawn ``argv`` under tree supervision with bounded output capture."""
    scope: _CgroupScope | None = None
    if use_cgroup and not is_windows:
        scope = _CgroupScope.create()

    def preexec() -> None:
        if scope is not None:
            scope.join_current_process()
        if rlimit_setter is not None:
            rlimit_setter()

    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
        "env": env,
    }
    if is_windows:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
        kwargs["preexec_fn"] = preexec

    try:
        proc = subprocess.Popen(argv, **kwargs)
    except BaseException:
        if scope is not None:
            scope.close()
        raise

    stdout_reader = _BoundedReader(proc.stdout, max_capture_bytes)
    stderr_reader = _BoundedReader(proc.stderr, max_capture_bytes)
    stdout_reader.start()
    stderr_reader.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True

    # Always tear down the whole subtree before joining readers: this kills
    # detached descendants (survival on normal completion) and closes any pipe
    # they inherited, so the readers reach EOF instead of blocking forever.
    _terminate_tree(proc, scope, is_windows)

    # Reap the main process so it does not linger as a zombie after a timeout
    # kill.  Orphaned descendants reparent to init, which reaps them.
    try:
        proc.wait(timeout=_REAP_DEADLINE)
    except subprocess.TimeoutExpired:
        pass

    stdout_reader.join(_READER_JOIN_GRACE)
    stderr_reader.join(_READER_JOIN_GRACE)

    if scope is not None:
        scope.close()

    returncode = proc.returncode if proc.returncode is not None else -1
    return SupervisedResult(
        returncode=-1 if timed_out else returncode,
        stdout=stdout_reader.data(),
        stderr=stderr_reader.data(),
        timed_out=timed_out,
        stdout_truncated=stdout_reader.truncated,
        stderr_truncated=stderr_reader.truncated,
    )


def _terminate_tree(
    proc: subprocess.Popen[bytes],
    scope: "_CgroupScope | None",
    is_windows: bool,
) -> None:
    if is_windows:
        try:
            proc.kill()
        except OSError:
            pass
        return
    if scope is not None:
        scope.kill()
        return
    _terminate_process_group(proc)


def cgroup_supervision_available() -> bool:
    scope = _CgroupScope.create()
    if scope is None:
        return False
    scope.close()
    return True
