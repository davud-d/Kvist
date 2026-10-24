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

import codecs
import os
import signal
import subprocess
import sys
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
_DESCENDANT_POLL_INTERVAL = 0.01
_CANCEL_POLL_INTERVAL = 0.1


@dataclass(slots=True)
class SupervisedResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool
    # The caller's ``cancel`` event ended the run before exit or timeout.
    cancelled: bool = False


class ProcessSupervisionError(RuntimeError):
    """Required process-tree containment could not be established."""


class _BoundedReader(threading.Thread):
    """Drain a pipe into a fixed-size buffer; keep reading and discard overflow."""

    def __init__(
        self,
        stream: Any,
        limit: int,
        on_chunk: Callable[[bytes], None] | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._on_chunk = on_chunk
        # A failing observer must not stop the drain: the child would block on
        # a full pipe. It is dropped after its first failure and the error kept.
        self.observer_error: Exception | None = None
        self._limit = max(0, limit)
        self._chunks: list[bytes] = []
        self._size = 0
        self._stop_requested = threading.Event()
        self._nonblocking = False
        # Windows (pipes since Python 3.12) reports an empty non-blocking pipe as an
        # error, not BlockingIOError, so the reader left before the child had written.
        if os.name != "nt":
            try:
                os.set_blocking(self._stream.fileno(), False)
                self._nonblocking = True
            except (AttributeError, OSError, ValueError):
                pass
        self.truncated = False

    def _notify(self, chunk: bytes) -> None:
        if self._on_chunk is None:
            return
        try:
            self._on_chunk(chunk)
        except Exception as error:
            self.observer_error = error
            self._on_chunk = None

    def run(self) -> None:
        try:
            while not self._stop_requested.is_set():
                try:
                    if self._nonblocking:
                        chunk = os.read(self._stream.fileno(), _CHUNK)
                    else:
                        # read1 returns what has arrived; read() would hold live
                        # output back until a full chunk or the end of the stream.
                        chunk = self._stream.read1(_CHUNK)
                except BlockingIOError:
                    self._stop_requested.wait(_KILL_POLL_INTERVAL)
                    continue
                if not chunk:
                    break
                self._notify(chunk)
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
            except (OSError, ValueError):
                pass

    def stop(self) -> None:
        self._stop_requested.set()
        if not self._nonblocking:
            try:
                self._stream.close()
            except (OSError, ValueError):
                pass

    def data(self) -> bytes:
        return b"".join(self._chunks)


def _linux_process_start_time(pid: int) -> int | None:
    """Return Linux /proc starttime for PID-reuse-safe descendant tracking."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = raw.rsplit(") ", 1)[1].split()
        return int(fields[19])
    except (FileNotFoundError, IndexError, OSError, ValueError):
        return None


def _linux_child_pids(pid: int) -> list[int]:
    try:
        raw = Path(f"/proc/{pid}/task/{pid}/children").read_text(
            encoding="ascii"
        )
    except OSError:
        return []
    children: list[int] = []
    for token in raw.split():
        try:
            child = int(token)
        except ValueError:
            continue
        if child > 0:
            children.append(child)
    return children


def _linux_descendant_snapshot(root_pid: int) -> dict[int, int]:
    found: dict[int, int] = {}
    pending = [root_pid]
    seen = {root_pid}
    while pending:
        parent = pending.pop()
        for child in _linux_child_pids(parent):
            if child in seen:
                continue
            seen.add(child)
            started = _linux_process_start_time(child)
            if started is None:
                continue
            found[child] = started
            pending.append(child)
    return found


class _LinuxDescendantTracker(threading.Thread):
    """Best-effort fallback for descendants that leave the process group."""

    def __init__(self, root_pid: int) -> None:
        super().__init__(daemon=True)
        self._root_pid = root_pid
        self._stop_requested = threading.Event()
        self._lock = threading.Lock()
        self._observed: dict[int, int] = {}

    def capture(self) -> None:
        snapshot = _linux_descendant_snapshot(self._root_pid)
        if snapshot:
            with self._lock:
                self._observed.update(snapshot)

    def run(self) -> None:
        while not self._stop_requested.is_set():
            self.capture()
            self._stop_requested.wait(_DESCENDANT_POLL_INTERVAL)

    def stop_and_snapshot(self) -> dict[int, int]:
        self.capture()
        self._stop_requested.set()
        self.join(_DESCENDANT_POLL_INTERVAL * 5)
        with self._lock:
            return dict(self._observed)


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

    def kill(self) -> str | None:
        try:
            with open(self.path / "cgroup.kill", "w") as handle:
                handle.write("1")
        except OSError:
            return "required cgroup scope could not be killed"
        deadline = time.monotonic() + _KILL_DEADLINE
        members = ""
        while time.monotonic() < deadline:
            try:
                members = (self.path / "cgroup.procs").read_text().strip()
            except OSError:
                return "required cgroup membership could not be verified"
            if not members:
                return None
            time.sleep(_KILL_POLL_INTERVAL)
        return (
            "required cgroup scope remained populated after its cleanup "
            "deadline"
        )

    def close(self) -> str | None:
        try:
            self.path.rmdir()
        except FileNotFoundError:
            return None
        except OSError:
            return "required cgroup scope could not be removed"
        return None


def _terminate_process_group(proc: subprocess.Popen[bytes]) -> None:
    # ``start_new_session=True`` makes the spawned PID the process-group ID.
    # Record/use that stable value directly: after a normal leader exit,
    # ``proc.wait()`` has reaped it and ``getpgid(proc.pid)`` fails even while
    # descendants still keep the original group alive.
    group_id = proc.pid if isinstance(proc.pid, int) and proc.pid > 0 else None
    try:
        if group_id is None:
            raise ProcessLookupError
        os.killpg(group_id, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except (PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _kill_recorded_descendants(processes: dict[int, int]) -> None:
    """Kill recorded descendants only if their Linux starttime still matches."""
    own_group = os.getpgrp()
    groups: set[int] = set()
    live_pids: list[int] = []
    for pid, expected_start in processes.items():
        if _linux_process_start_time(pid) != expected_start:
            continue
        live_pids.append(pid)
        try:
            group = os.getpgid(pid)
        except (ProcessLookupError, PermissionError, OSError):
            continue
        if group > 0 and group != own_group:
            groups.add(group)
    for group in groups:
        try:
            os.killpg(group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    for pid in live_pids:
        if _linux_process_start_time(pid) != processes[pid]:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _text_observer(
    on_output: Callable[[str], None] | None,
) -> Callable[[bytes], None] | None:
    if on_output is None:
        return None
    # Each stream keeps its own decoder so a multi-byte character split across
    # two reads is not mangled.
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def observe(chunk: bytes) -> None:
        text = decoder.decode(chunk)
        if text:
            on_output(text)

    return observe


def _wait_unless_cancelled(
    proc: subprocess.Popen[bytes], timeout: float, cancel: threading.Event
) -> bool:
    """Wait for exit; True when ``cancel`` fired first, TimeoutExpired at the deadline."""
    deadline = time.monotonic() + timeout
    while True:
        if cancel.is_set():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        try:
            proc.wait(timeout=min(_CANCEL_POLL_INTERVAL, remaining))
        except subprocess.TimeoutExpired:
            continue
        return False


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
    on_output: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> SupervisedResult:
    """Spawn ``argv`` under tree supervision with bounded output capture.

    ``on_output`` sees the decoded output as it arrives, from the reader
    threads (so it must be thread-safe and quick). Capture is unaffected.
    Setting ``cancel`` tears the subtree down like a timeout and reports
    ``cancelled``; the run then ends within ``_CANCEL_POLL_INTERVAL``.
    """
    scope: _CgroupScope | None = None
    if use_cgroup and not is_windows:
        scope = _CgroupScope.create()
        if scope is None:
            raise ProcessSupervisionError(
                "required cgroup process-tree scope became unavailable"
            )

    def preexec() -> None:
        if scope is not None:
            scope.join_current_process()
        if rlimit_setter is not None:
            rlimit_setter()

    kwargs: dict[str, Any] = {
        "cwd": cwd,
        # Terminal commands are non-interactive actions. Inheriting Kvist's
        # controlling stdin lets a child consume UI keystrokes, block on a
        # prompt, or race the HITL input path.
        "stdin": subprocess.DEVNULL,
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
    except subprocess.SubprocessError as error:
        if scope is not None:
            scope.close()
        raise ProcessSupervisionError(
            "child process supervision setup failed"
        ) from error
    except BaseException:
        if scope is not None:
            scope.close()
        raise

    stdout_reader = _BoundedReader(
        proc.stdout, max_capture_bytes, _text_observer(on_output)
    )
    stderr_reader = _BoundedReader(
        proc.stderr, max_capture_bytes, _text_observer(on_output)
    )
    descendant_tracker = (
        _LinuxDescendantTracker(proc.pid)
        if (
            not is_windows
            and scope is None
            and sys.platform.startswith("linux")
            and isinstance(proc.pid, int)
            and proc.pid > 0
        )
        else None
    )
    if descendant_tracker is not None:
        descendant_tracker.start()
    stdout_reader.start()
    stderr_reader.start()

    timed_out = False
    cancelled = False
    interrupted: BaseException | None = None
    try:
        if cancel is None:
            proc.wait(timeout=timeout)
        else:
            cancelled = _wait_unless_cancelled(proc, timeout, cancel)
    except subprocess.TimeoutExpired:
        timed_out = True
    except BaseException as error:
        # A stop requested from the UI (Ctrl+C/Escape) unwinds through here.
        # The child owns its own session, so it survives our exit unless the
        # teardown below still runs — hence recording the interrupt and
        # re-raising it only after the subtree is gone.
        interrupted = error

    # Always tear down the whole subtree before joining readers: this kills
    # detached descendants (survival on normal completion) and closes any pipe
    # they inherited, so the readers reach EOF instead of blocking forever.
    recorded_descendants = (
        descendant_tracker.stop_and_snapshot()
        if descendant_tracker is not None
        else {}
    )
    termination_error = _terminate_tree(
        proc,
        scope,
        is_windows,
        recorded_descendants=recorded_descendants,
    )

    # Reap the main process so it does not linger as a zombie after a timeout
    # kill.  Orphaned descendants reparent to init, which reaps them.
    try:
        proc.wait(timeout=_REAP_DEADLINE)
    except subprocess.TimeoutExpired:
        pass

    readers = (stdout_reader, stderr_reader)
    reader_deadline = time.monotonic() + _READER_JOIN_GRACE
    for reader in readers:
        reader.join(max(0.0, reader_deadline - time.monotonic()))
    for reader in readers:
        if reader.is_alive():
            reader.stop()
    for reader in readers:
        if reader.is_alive():
            reader.join(_KILL_POLL_INTERVAL * 5)

    scope_close_error = scope.close() if scope is not None else None
    if interrupted is not None:
        raise interrupted
    supervision_error = termination_error or scope_close_error
    if supervision_error:
        raise ProcessSupervisionError(supervision_error)

    returncode = proc.returncode if proc.returncode is not None else -1
    return SupervisedResult(
        returncode=-1 if timed_out else returncode,
        stdout=stdout_reader.data(),
        stderr=stderr_reader.data(),
        timed_out=timed_out,
        stdout_truncated=stdout_reader.truncated,
        stderr_truncated=stderr_reader.truncated,
        cancelled=cancelled,
    )


def _terminate_tree(
    proc: subprocess.Popen[bytes],
    scope: "_CgroupScope | None",
    is_windows: bool,
    *,
    recorded_descendants: dict[int, int],
) -> str | None:
    if is_windows:
        try:
            proc.kill()
        except OSError:
            pass
        return None
    if scope is not None:
        error = scope.kill()
        if error:
            _terminate_process_group(proc)
        return error
    _terminate_process_group(proc)
    if recorded_descendants:
        _kill_recorded_descendants(recorded_descendants)
    return None


def cgroup_supervision_available() -> bool:
    scope = _CgroupScope.create()
    if scope is None:
        return False
    return scope.close() is None
