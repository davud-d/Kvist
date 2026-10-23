# -*- coding: utf-8 -*-
"""Task-owned resources: processes, endpoints and temporary directories with an owner and a lifetime.

Every long-lived thing a task starts (a skill runner and the virtual screen in
its sandbox, a dev server, a screen viewer, a helper agent) is registered here
with its owner. The core — not the model, not the skill — stops what a task
owns before the final answer, and records whether that worked.

Process identity is the pair (pid, kernel start time), never the pid alone: a
pid recorded before a crash may belong to an unrelated program by the time the
core looks again. Where cgroup v2 is available each process tree gets its own
scope, so detached descendants cannot outlive a stop.

The registry is mirrored to ``<workspace>/.kvist/resources.json`` so a core that
crashed can be followed by one that finds and stops what it left behind.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from kernel.session.durable_json import JsonStorageError, read_json_object, update_json_object
from kernel.sandbox.process_tree import _CgroupScope, _linux_process_start_time

REGISTRY_FILE = "resources.json"
_STOP_POLL = 0.05
DEFAULT_GRACE_SECONDS = 3.0
# The marker every process the core spawns carries in its environment, so a
# recovering core can tell its own leftovers from an unrelated program that
# happens to have the same pid and start time on another boot.
MARKER_ENV = "KVIST_RESOURCE_ID"


@dataclass(frozen=True, slots=True)
class Owner:
    """Who a resource belongs to. Empty fields mean "not narrower than this"."""

    session: str = ""
    run_id: str = ""
    kind: str = "run"  # run | skill | helper | viewer
    skill: str = ""  # "<id>@<version>"
    call_id: str = ""
    helper_id: str = ""

    def within(self, other: "Owner") -> bool:
        """Whether this owner falls inside ``other`` (whose empty fields match anything)."""
        for name in ("session", "run_id", "skill", "call_id", "helper_id"):
            wanted = getattr(other, name)
            if wanted and getattr(self, name) != wanted:
                return False
        return True


@dataclass(slots=True)
class StopOutcome:
    resource_id: str
    label: str
    stopped: bool
    forced: bool = False
    error: str = ""
    kind: str = ""


@dataclass(slots=True)
class CleanupReport:
    reason: str
    outcomes: list[StopOutcome] = field(default_factory=list)

    @property
    def failed(self) -> list[StopOutcome]:
        return [item for item in self.outcomes if not item.stopped]

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self, language: str) -> str:
        """One factual line for the final report; empty when nothing the person would recognise was owned."""
        visible = [item for item in self.outcomes if item.kind != "tempdir"]
        if not visible:
            return ""
        labels = ", ".join(dict.fromkeys(item.label for item in visible if item.stopped))
        if self.ok:
            if language == "ru":
                return f"Kvist закрыла процессы задачи: {labels}."
            return f"Kvist closed the task's processes: {labels}."
        failed = "; ".join(f"{item.label} ({item.error})" for item in self.failed)
        if language == "ru":
            return f"Kvist не смог подтвердить завершение: {failed}."
        return f"Kvist could not confirm that these stopped: {failed}."


class ManagedProcess:
    """One process tree the core started, contained by a cgroup scope where possible."""

    def __init__(
        self,
        argv: list[str],
        *,
        label: str,
        env: dict[str, str],
        cwd: str | Path | None = None,
        pass_fds: tuple[int, ...] = (),
        memory_mb: int | None = None,
        pids_max: int | None = None,
        output_limit: int = 256 * 1024,
        require_cgroup: bool = False,
        protocol: bool = False,
    ) -> None:
        """``protocol`` keeps stdin and stdout as the process's protocol pipes
        (an MCP server); stderr alone is then drained into the output tail."""
        self.argv = list(argv)
        self.label = label
        self._env = dict(env)
        self._cwd = cwd
        self._pass_fds = pass_fds
        self._memory_mb = memory_mb
        self._pids_max = pids_max
        self._require_cgroup = require_cgroup
        self.scope: _CgroupScope | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self.start_time: int | None = None
        self.limits_note = ""
        self._output: deque[str] = deque()
        self._output_size = 0
        self._output_limit = output_limit
        self._output_lock = threading.Lock()
        self._frozen = False
        self._protocol = protocol

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None

    def start(self, resource_id: str) -> None:
        if sys.platform.startswith("linux"):
            self.scope = _CgroupScope.create()
            if self.scope is None and self._require_cgroup:
                raise RuntimeError("cgroup v2 process containment is unavailable")
            self._apply_limits()
        env = {**self._env, MARKER_ENV: resource_id}
        scope = self.scope

        def preexec() -> None:
            if scope is not None:
                scope.join_current_process()

        try:
            self.proc = subprocess.Popen(
                self.argv,
                cwd=self._cwd,
                env=env,
                stdin=subprocess.PIPE if self._protocol else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE if self._protocol else subprocess.STDOUT,
                pass_fds=self._pass_fds,
                start_new_session=True,
                preexec_fn=preexec if scope is not None else None,
            )
        except BaseException:
            if self.scope is not None:
                self.scope.close()
            raise
        self.start_time = _linux_process_start_time(self.proc.pid)
        threading.Thread(target=self._drain, name=f"kvist-out-{self.label}", daemon=True).start()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def output_tail(self, limit: int = 8000) -> str:
        with self._output_lock:
            text = "".join(self._output)
        return text[-limit:]

    def freeze(self) -> bool:
        """Stop every process of the tree in place (cgroup freezer); False when unsupported."""
        if self.scope is None or not self.alive():
            return False
        try:
            (self.scope.path / "cgroup.freeze").write_text("1")
        except OSError:
            return False
        self._frozen = True
        return True

    def thaw(self) -> None:
        if self.scope is None or not self._frozen:
            return
        try:
            (self.scope.path / "cgroup.freeze").write_text("0")
        except OSError:
            return
        self._frozen = False

    def stop(self, grace: float = DEFAULT_GRACE_SECONDS) -> StopOutcome:
        """SIGTERM the tree, wait ``grace``, then kill everything left; verify it is gone."""
        outcome = StopOutcome("", self.label, stopped=True)
        if self.proc is None:
            return outcome
        self.thaw()
        if self.alive():
            self._signal_group(signal.SIGTERM)
            deadline = time.monotonic() + grace
            while self.alive() and time.monotonic() < deadline:
                time.sleep(_STOP_POLL)
        forced = self.alive() or self._scope_populated()
        if forced:
            outcome.forced = True
            error = self.scope.kill() if self.scope is not None else None
            if error or self.scope is None:
                self._signal_group(signal.SIGKILL)
            if error:
                outcome.error = error
        try:
            self.proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            outcome.stopped = False
            outcome.error = outcome.error or "the process did not exit after SIGKILL"
        if self._scope_populated():
            outcome.stopped = False
            outcome.error = outcome.error or "processes remained in its cgroup scope"
        if self.scope is not None and outcome.stopped:
            close_error = self.scope.close()
            if close_error:
                outcome.error = close_error
        return outcome

    def _apply_limits(self) -> None:
        if self.scope is None:
            if self._memory_mb or self._pids_max:
                self.limits_note = "no cgroup scope: memory and process-count limits are not enforced"
            return
        notes: list[str] = []
        for filename, value in (("memory.max", self._memory_mb and self._memory_mb * 1024 * 1024),
                                ("pids.max", self._pids_max)):
            if not value:
                continue
            try:
                (self.scope.path / filename).write_text(str(value))
            except OSError:
                notes.append(f"{filename} could not be set")
        self.limits_note = "; ".join(notes)

    def _scope_populated(self) -> bool:
        if self.scope is None:
            return False
        try:
            return bool((self.scope.path / "cgroup.procs").read_text().strip())
        except OSError:
            return False

    def _signal_group(self, sig: int) -> None:
        assert self.proc is not None
        try:
            os.killpg(self.proc.pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            try:
                self.proc.send_signal(sig)
            except OSError:
                return

    def _drain(self) -> None:
        if self.proc is None:
            return
        stream = self.proc.stderr if self._protocol else self.proc.stdout
        if stream is None:
            return
        for raw in iter(lambda: stream.read1(4096), b""):
            text = raw.decode("utf-8", errors="replace")
            with self._output_lock:
                self._output.append(text)
                self._output_size += len(text)
                while self._output_size > self._output_limit and self._output:
                    self._output_size -= len(self._output.popleft())


@dataclass(slots=True)
class Resource:
    id: str
    kind: str  # process | endpoint | tempdir | helper | logical
    label: str
    owner: Owner
    created_at: float
    keep_on_pause: bool = True
    process: ManagedProcess | None = None
    path: Path | None = None
    closer: Callable[[], str | None] | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "owner": asdict(self.owner),
            "created_at": self.created_at,
            "core_pid": os.getpid(),
            "core_start": _linux_process_start_time(os.getpid()),
        }
        if self.process is not None:
            record.update({
                "pid": self.process.pid,
                "start_time": self.process.start_time,
                "cgroup": str(self.process.scope.path) if self.process.scope else "",
            })
        if self.path is not None:
            record["path"] = str(self.path)
        return record


class ResourceManager:
    """The resources of one core process, for one workspace."""

    def __init__(self, workspace: Path, runtime_root: Path | None = None) -> None:
        self.workspace = Path(workspace)
        self._registry_path = self.workspace / ".kvist" / REGISTRY_FILE
        self._boundary = self.workspace
        self._resources: dict[str, Resource] = {}
        self._lock = threading.RLock()
        self._runtime_root = runtime_root
        self.listener: Callable[[str, Resource], None] | None = None

    # --- creating --------------------------------------------------------

    def runtime_dir(self) -> Path:
        """A private (0700) directory for this core's sockets and scratch files."""
        with self._lock:
            if self._runtime_root is None:
                # Atomic allocation avoids following a pre-created per-user directory.
                self._runtime_root = Path(tempfile.mkdtemp(prefix="kvist-core-"))
            return self._runtime_root

    def temp_dir(self, owner: Owner, label: str) -> Resource:
        path = self.runtime_dir() / f"{label}-{uuid.uuid4().hex[:10]}"
        path.mkdir(mode=0o700)
        return self._add(Resource(_new_id("dir"), "tempdir", label, owner, time.time(), path=path))

    def spawn(self, process: ManagedProcess, owner: Owner, *, keep_on_pause: bool = True,
              details: dict[str, Any] | None = None) -> Resource:
        resource = Resource(_new_id("proc"), "process", process.label, owner, time.time(),
                            keep_on_pause=keep_on_pause, process=process, details=details or {})
        process.start(resource.id)
        return self._add(resource)

    def register(self, kind: str, label: str, owner: Owner, closer: Callable[[], str | None] | None = None,
                 details: dict[str, Any] | None = None) -> Resource:
        """A resource the core does not spawn itself (an endpoint, a helper, a skill's browser session)."""
        return self._add(Resource(_new_id(kind), kind, label, owner, time.time(), closer=closer,
                                  details=details or {}))

    def _add(self, resource: Resource) -> Resource:
        with self._lock:
            self._resources[resource.id] = resource
        self._persist()
        self._notify("started", resource)
        return resource

    # --- reading ---------------------------------------------------------

    def get(self, resource_id: str) -> Resource | None:
        with self._lock:
            return self._resources.get(resource_id)

    def owned_by(self, owner: Owner) -> list[Resource]:
        with self._lock:
            return [item for item in self._resources.values() if item.owner.within(owner)]

    def describe(self, owner: Owner) -> list[dict[str, Any]]:
        rows = []
        for item in self.owned_by(owner):
            alive = item.process.alive() if item.process is not None else True
            rows.append({"id": item.id, "kind": item.kind, "label": item.label, "alive": alive,
                         "owner": asdict(item.owner), "details": item.details})
        return rows

    # --- stopping --------------------------------------------------------

    def stop(self, resource_id: str, grace: float = DEFAULT_GRACE_SECONDS) -> StopOutcome:
        with self._lock:
            resource = self._resources.get(resource_id)
        if resource is None:
            return StopOutcome(resource_id, resource_id, stopped=True)
        outcome = self._stop_one(resource, grace)
        if outcome.stopped:
            with self._lock:
                self._resources.pop(resource_id, None)
            self._persist()
            self._notify("stopped", resource)
        return outcome

    def cleanup(self, owner: Owner, reason: str, grace: float = DEFAULT_GRACE_SECONDS) -> CleanupReport:
        """Stop everything ``owner`` covers, newest first (a viewer before its screen)."""
        report = CleanupReport(reason)
        items = sorted(self.owned_by(owner), key=lambda item: item.created_at, reverse=True)
        for resource in items:
            report.outcomes.append(self.stop(resource.id, grace))
        return report

    def shutdown(self, reason: str = "app_closed") -> CleanupReport:
        report = self.cleanup(Owner(), reason, grace=1.0)
        if self._runtime_root is not None:
            shutil.rmtree(self._runtime_root, ignore_errors=True)
        return report

    def _stop_one(self, resource: Resource, grace: float) -> StopOutcome:
        if resource.process is not None:
            outcome = resource.process.stop(grace)
        elif resource.closer is not None:
            error = resource.closer()
            outcome = StopOutcome("", resource.label, stopped=not error, error=error or "")
        elif resource.path is not None:
            shutil.rmtree(resource.path, ignore_errors=True)
            outcome = StopOutcome("", resource.label, stopped=not resource.path.exists(),
                                  error="" if not resource.path.exists() else "directory remained")
        else:
            outcome = StopOutcome("", resource.label, stopped=True)
        outcome.resource_id = resource.id
        outcome.kind = resource.kind
        return outcome

    # --- persistence and recovery ----------------------------------------

    def _persist(self) -> None:
        with self._lock:
            records = [item.record() for item in self._resources.values() if item.kind in ("process", "tempdir")]
        my_pid = os.getpid()

        def update(document: dict[str, Any]) -> dict[str, Any]:
            others = [entry for entry in document.get("resources", []) if entry.get("core_pid") != my_pid]
            document["resources"] = others + records
            return document

        try:
            self._registry_path.parent.mkdir(parents=True, exist_ok=True)
            update_json_object(self._registry_path, update, boundary=self._boundary, empty_as_missing=True)
        except (JsonStorageError, OSError):
            # The in-memory registry still drives cleanup in this process; only
            # crash recovery loses this entry, which recover() then reports.
            return

    def recover(self) -> CleanupReport:
        """Stop what a crashed core of this workspace left running, without touching anything else."""
        report = CleanupReport("recovered_after_crash")
        try:
            document = read_json_object(self._registry_path, boundary=self._boundary, empty_as_missing=True)
        except (JsonStorageError, OSError):
            return report
        kept: list[dict[str, Any]] = []
        for entry in document.get("resources", []):
            if _core_may_be_running(entry):
                kept.append(entry)
                continue
            outcome = _stop_leftover(entry)
            if outcome is not None:
                report.outcomes.append(outcome)

        def update(current: dict[str, Any]) -> dict[str, Any]:
            current["resources"] = [entry for entry in current.get("resources", []) if entry in kept
                                    or entry.get("core_pid") == os.getpid()]
            return current

        try:
            update_json_object(self._registry_path, update, boundary=self._boundary, empty_as_missing=True)
        except (JsonStorageError, OSError) as error:
            report.outcomes.append(StopOutcome("", "resource registry", stopped=False, error=str(error)))
        return report

    def _notify(self, event: str, resource: Resource) -> None:
        if self.listener is not None:
            self.listener(event, resource)


def _new_id(kind: str) -> str:
    return f"{kind}_{uuid.uuid4().hex[:10]}"


def _core_may_be_running(entry: dict[str, Any]) -> bool:
    # Start times come from /proc, so on macOS and Windows a core records none and a running core cannot be
    # told from a crashed one; its records are kept rather than remove what a live core still uses.
    start = entry.get("core_start")
    return start is None or _process_matches(entry.get("core_pid"), start)


def _process_matches(pid: Any, start: Any) -> bool:
    return isinstance(pid, int) and pid > 0 and start is not None and _linux_process_start_time(pid) == start


def _stop_leftover(entry: dict[str, Any]) -> StopOutcome | None:
    label = str(entry.get("label") or entry.get("id") or "resource")
    if entry.get("kind") == "tempdir":
        path = Path(str(entry.get("path") or ""))
        if path.name and "kvist" in str(path) and path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            return StopOutcome(str(entry.get("id")), label, stopped=not path.exists())
        return None
    pid, start = entry.get("pid"), entry.get("start_time")
    cgroup = Path(str(entry.get("cgroup") or ""))
    if cgroup.name and (cgroup / "cgroup.kill").exists():
        # A cgroup this core created holds only what it spawned there; killing
        # it cannot reach another program even if a pid was reused.
        error = _CgroupScope(cgroup).kill()
        _CgroupScope(cgroup).close()
        return StopOutcome(str(entry.get("id")), label, stopped=not error, forced=True, error=error or "")
    if not _process_matches(pid, start) or not _carries_marker(pid, str(entry.get("id"))):
        return StopOutcome(str(entry.get("id")), label, stopped=True)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError as error:
        return StopOutcome(str(entry.get("id")), label, stopped=False, error=str(error))
    return StopOutcome(str(entry.get("id")), label, stopped=True, forced=True)


def _carries_marker(pid: int, resource_id: str) -> bool:
    try:
        environ = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return False
    return f"{MARKER_ENV}={resource_id}".encode() in environ.split(b"\0")
