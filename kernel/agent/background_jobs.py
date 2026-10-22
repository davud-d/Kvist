# -*- coding: utf-8 -*-
"""Background shell commands: start now, read their output later.

A job is a ``run_shell`` command the model started with ``background: true``.
It runs under the same process-tree supervision as a foreground command; this
module only owns the thread that waits for it, the bounded output it produced,
and what the model has been told so far.

Output reaches the model on step boundaries: ``take_note`` is called once before
each request and returns only what is new since the previous note. Nothing is
pushed into a request already in flight. A job the answered turn left running
reaches the model when it ends, through a turn the transport starts for it.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from kernel.protocol.protocol import MAX_JOB_WAIT_SECONDS
from kernel.sandbox.redaction import defang_prompt_boundary_tags, redact_text

MAX_RUNNING_JOBS = 4
# Rolling per-job tail. The reader threads of the supervisor already bound the
# captured bytes; this bounds what the registry keeps for notes and the UI.
TAIL_CHARS = 16_000
# One job's contribution to a single note.
NOTE_CHARS = 4_000
# Time ``stop`` waits for the supervisor to tear the subtree down.
_STOP_GRACE_SECONDS = 5.0
# How long a wake-up on new output waits to see whether the job also ended.
_EXIT_SETTLE_SECONDS = 0.2
# A command line in a note is one short line, however long its argv.
_COMMAND_CHARS = 200

RUNNING = "running"
EXITED = "exited"
TIMED_OUT = "timed_out"
STOPPED = "stopped"

_STATUS_WORDS = {
    EXITED: "exited",
    TIMED_OUT: "timed out",
    STOPPED: "stopped",
}


class JobLimitError(RuntimeError):
    """Too many jobs are running at once."""


class UnknownJobError(KeyError):
    """No job has this id in the current run."""


@dataclass(slots=True)
class BackgroundJob:
    job_id: str
    command: list[str]
    purpose: str
    call_id: str
    started_at: float
    # Seconds the supervisor lets the job run before ending it as timed_out.
    max_seconds: float = 0.0
    status: str = RUNNING
    exit_code: int | None = None
    tail: str = ""
    # Characters received over the job's life and how many of them the model
    # has been shown; the difference is "new output".
    total: int = 0
    told: int = 0
    finish_told: bool = False
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)

    def unread(self) -> str:
        """Output the model has not seen, newest end kept when it exceeds the tail."""
        count = self.total - self.told
        if count <= 0:
            return ""
        return self.tail[-count:]

    def unread_omitted(self) -> int:
        """Characters of unread output that fell off the tail."""
        return max(0, self.total - self.told - len(self.tail))


class JobListener(Protocol):
    """Receives job events; called from the job's thread for output and finish."""

    def job_started(self, job: BackgroundJob) -> None: ...
    def job_output(self, job: BackgroundJob) -> None: ...
    def job_finished(self, job: BackgroundJob) -> None: ...


class UiJobListener:
    """Forwards job events to a UI through its optional ``print_job_*`` methods.

    ``on_finished`` runs after the UI heard that a job ended on its own (not
    stopped); the agent uses it to be woken by a job its answered turn left running.
    """

    def __init__(self, ui: Any, on_finished: Callable[[], None] | None = None) -> None:
        self._ui = ui
        self._on_finished = on_finished

    def job_started(self, job: BackgroundJob) -> None:
        method = getattr(self._ui, "print_job_started", None)
        if method is not None:
            method(job.job_id, list(job.command), job.purpose, job.call_id)

    def job_output(self, job: BackgroundJob) -> None:
        method = getattr(self._ui, "print_job_output", None)
        if method is not None:
            method(job.job_id, job.tail)

    def job_finished(self, job: BackgroundJob) -> None:
        method = getattr(self._ui, "print_job_finished", None)
        if method is not None:
            method(job.job_id, job.status, job.exit_code, job.tail)
        # A stopped job was stopped by the model or by "stop all", who already know.
        if self._on_finished is not None and job.status != STOPPED:
            self._on_finished()


# What the supervisor reports for a finished run: (returncode, timed_out, cancelled).
SpawnOutcome = tuple[int, bool, bool]
Spawn = Callable[[Callable[[str], None], threading.Event], SpawnOutcome]


@dataclass(frozen=True, slots=True)
class ServiceExposure:
    """A port of a background job that the task's network broker may route to.

    ``socket_dir`` is a core-owned directory where the in-sandbox bridge puts
    ``service.sock``; ``on_ready``/``on_exit`` register and drop the service.
    """

    port: int
    socket_dir: Path
    on_ready: Callable[[str, bool], None]  # (job_id, isolated) -> None
    on_exit: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class BackgroundLaunch:
    """Where and as what ``TerminalExecutor.run`` starts a command in the background."""

    jobs: "BackgroundJobs"
    purpose: str = ""
    call_id: str = ""
    expose: ServiceExposure | None = None


class BackgroundJobs:
    """Registry of the jobs of one executor, across turns; every method is thread-safe."""

    def __init__(self, max_running: int = MAX_RUNNING_JOBS) -> None:
        self._max_running = max_running
        self._cond = threading.Condition()
        self._jobs: dict[str, BackgroundJob] = {}
        self._ids = itertools.count(1)
        self.listener: JobListener | None = None
        # Whether a job outlives the final answer of the turn that started it
        # (the agent is woken when it ends); only the start note reads it.
        self.outlives_answer = False

    def launch(
        self,
        command: list[str],
        purpose: str,
        call_id: str,
        spawn: Spawn,
        max_seconds: float = 0.0,
    ) -> BackgroundJob:
        """Start ``spawn`` on its own thread and return the job at once.

        ``spawn(on_output, cancel)`` runs the supervised process to its end and
        reports how it ended; the registry never inspects the process itself.
        """
        with self._cond:
            running = sum(1 for job in self._jobs.values() if job.status == RUNNING)
            if running >= self._max_running:
                raise JobLimitError(
                    f"{running} background jobs are already running (limit {self._max_running}); "
                    "read or stop one before starting another"
                )
            job = BackgroundJob(
                job_id=f"job_{next(self._ids)}",
                command=list(command),
                purpose=purpose,
                call_id=call_id,
                started_at=time.monotonic(),
                max_seconds=max_seconds,
            )
            self._jobs[job.job_id] = job
        listener = self.listener
        if listener is not None:
            listener.job_started(job)
        threading.Thread(
            target=self._run, args=(job, spawn), name=job.job_id, daemon=True
        ).start()
        return job

    def _run(self, job: BackgroundJob, spawn: Spawn) -> None:
        try:
            returncode, timed_out, cancelled = spawn(
                lambda text: self._feed(job, text), job.cancel
            )
        except (OSError, RuntimeError) as error:
            # Start-up failure (missing executable, no process-tree scope): the
            # model learns it through the job's output, like any other failure.
            self._feed(job, f"[could not run: {error}]\n")
            status, exit_code = EXITED, None
        else:
            if cancelled:
                status, exit_code = STOPPED, None
            elif timed_out:
                status, exit_code = TIMED_OUT, None
            else:
                status, exit_code = EXITED, returncode
        with self._cond:
            job.status = status
            job.exit_code = exit_code
            self._cond.notify_all()
        listener = self.listener
        if listener is not None:
            listener.job_finished(job)
        # After the event: whoever waits for the job to end (stop, end of turn)
        # then knows the finish has already been reported.
        job.done.set()

    def _feed(self, job: BackgroundJob, text: str) -> None:
        with self._cond:
            job.total += len(text)
            job.tail = (job.tail + text)[-TAIL_CHARS:]
            self._cond.notify_all()
        listener = self.listener
        if listener is not None:
            listener.job_output(job)

    def get(self, job_id: str) -> BackgroundJob:
        with self._cond:
            try:
                return self._jobs[job_id]
            except KeyError:
                raise UnknownJobError(job_id) from None

    def ids(self) -> list[str]:
        with self._cond:
            return list(self._jobs)

    def running(self) -> list[BackgroundJob]:
        with self._cond:
            return [job for job in self._jobs.values() if job.status == RUNNING]

    def wait_for_change(self, job: BackgroundJob, seconds: float) -> None:
        """Block until the job has new output or has finished, or ``seconds`` pass."""
        seconds = max(0.0, min(seconds, MAX_JOB_WAIT_SECONDS))
        with self._cond:
            seen = job.total
            self._cond.wait_for(
                lambda: job.total != seen or job.status != RUNNING, timeout=seconds
            )
        # A command that prints its last line and exits is reported as finished,
        # not as "running with one more line".
        if job.status == RUNNING:
            job.done.wait(_EXIT_SETTLE_SECONDS)

    def read(self, job: BackgroundJob) -> tuple[str, int]:
        """The job's unread output and how much of it was lost, marking it read."""
        with self._cond:
            text, omitted = job.unread(), job.unread_omitted()
            job.told = job.total
            if job.status != RUNNING:
                job.finish_told = True
            return text, omitted

    def stop(self, job: BackgroundJob) -> None:
        """Tear the job's process tree down and wait until it has ended."""
        job.cancel.set()
        job.done.wait(_STOP_GRACE_SECONDS)

    def stop_all(self) -> list[BackgroundJob]:
        """Stop every running job; returns the ones that were running."""
        stopped = self.running()
        for job in stopped:
            job.cancel.set()
        for job in stopped:
            job.done.wait(_STOP_GRACE_SECONDS)
        return stopped

    def has_unreported_finish(self) -> bool:
        """Whether a job ended on its own and the model has not been told yet; new output alone does not count."""
        with self._cond:
            return any(job.status not in (RUNNING, STOPPED) and not job.finish_told for job in self._jobs.values())

    def take_note(self) -> tuple[str, list[str]]:
        """What the model has not been told yet, as one block, plus the job ids.

        Empty when nothing changed, so a quiet job costs the model nothing.
        """
        parts: list[str] = []
        ids: list[str] = []
        with self._cond:
            for job in self._jobs.values():
                section = _job_section(job)
                job.told = job.total
                if job.status != RUNNING:
                    job.finish_told = True
                if section:
                    parts.append(section)
                    ids.append(job.job_id)
        if not parts:
            return "", []
        return "Background jobs:\n" + "\n".join(parts), ids


def describe(job: BackgroundJob) -> str:
    """One word-level state: ``running``, ``exited with code 0``, ``timed out``…"""
    if job.status == RUNNING:
        return "running"
    word = _STATUS_WORDS[job.status]
    if job.status == EXITED and job.exit_code is not None:
        return f"{word} with code {job.exit_code}"
    return word


def command_line(command: list[str]) -> str:
    text = " ".join(" ".join(command).split())
    return text if len(text) <= _COMMAND_CHARS else text[: _COMMAND_CHARS - 1] + "…"


def wrap_output(job_id: str, text: str, omitted: int = 0) -> str:
    """Job output as untrusted data: redacted, and unable to close its own block."""
    body = defang_prompt_boundary_tags(redact_text(text))[-NOTE_CHARS:]
    lost = f"[... {omitted} earlier characters not kept ...]\n" if omitted else ""
    return (
        f'<UNTRUSTED_TOOL_OUTPUT source="background_job:{job_id}">\n'
        f"{lost}{body}\n</UNTRUSTED_TOOL_OUTPUT>"
    )


def _job_section(job: BackgroundJob) -> str:
    news = job.total - job.told
    finished_now = job.status != RUNNING and not job.finish_told
    if news <= 0 and not finished_now:
        return ""
    lines = [f"[{job.job_id} · {describe(job)}] {command_line(job.command)}"]
    if news > 0:
        lines.append(wrap_output(job.job_id, job.unread(), job.unread_omitted()))
    return "\n".join(lines)


def snapshot(job: BackgroundJob, now: float | None = None) -> dict[str, Any]:
    """Plain facts about a job for an action result."""
    clock = time.monotonic() if now is None else now
    return {
        "job_id": job.job_id,
        "status": job.status,
        "exit_code": job.exit_code,
        "seconds": round(clock - job.started_at, 1),
    }
