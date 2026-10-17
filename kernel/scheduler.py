# -*- coding: utf-8 -*-
"""Timed wake-ups: waits, one-time schedules and recurring schedules.

A wait lives in memory and ends with the conversation. A schedule is stored in
``<workspace>/.kvist/schedules.json`` and survives restarts. Either way, when
its time comes the scheduler queues a ``Fired`` record and calls the agent's
wake listener; the agent then runs a turn of its own (``LocalAgent.wake``).

The file is the single source of truth for schedules, so several cores on one
workspace share them. A core claims a due schedule by advancing its stored
``next_run`` under the file lock, which is what keeps two cores from running
the same occurrence. Only a core with a wake listener runs the timer thread:
without one (classic terminal, headless) schedules wait for a core that has it.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from kernel.durable_json import read_json_object, update_json_object

WAIT = "wait"
ONCE = "once"
RECURRING = "recurring"

MAX_SCHEDULES = 50
MAX_WAIT_SECONDS = 24 * 3600
MIN_INTERVAL_MINUTES = 5
# The timer re-reads the clock this often: a laptop suspend does not advance the
# monotonic timeout of a condition wait, and another core may have changed the file.
_TICK_SECONDS = 30.0

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_RECURRENCE_KEYS = ("time", "weekdays", "every_days", "every_minutes")


class ScheduleError(Exception):
    """A schedule operation the runtime refuses, with the reason for the model."""


@dataclass(slots=True)
class Entry:
    id: str
    kind: str
    task: str
    next_run: float
    # Recurrence fields (``_RECURRENCE_KEYS``, plus ``anchor`` for every_days); empty unless recurring.
    repeat: dict[str, Any] = field(default_factory=dict)
    paused: bool = False
    created_at: float = 0.0
    last_run: float | None = None
    runs: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__slots__}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Entry:
        return cls(
            id=str(data["id"]), kind=str(data["kind"]), task=str(data["task"]), next_run=float(data["next_run"]),
            repeat=dict(data.get("repeat") or {}), paused=bool(data.get("paused", False)),
            created_at=float(data.get("created_at", 0.0)), last_run=data.get("last_run"),
            runs=int(data.get("runs", 0)),
        )


@dataclass(frozen=True, slots=True)
class Fired:
    """One occurrence that came due and has not been shown to the model yet."""

    id: str
    kind: str
    task: str
    due_at: float
    # True when the occurrence came due while no core was running to take it.
    late: bool


def parse_clock(value: str) -> tuple[int, int]:
    try:
        hour, minute = value.split(":")
        parsed = (int(hour), int(minute))
    except ValueError as error:
        raise ScheduleError(f"'time' must be local HH:MM, got {value!r}") from error
    if not (0 <= parsed[0] < 24 and 0 <= parsed[1] < 60):
        raise ScheduleError(f"'time' must be local HH:MM, got {value!r}")
    return parsed


def parse_at(value: str, now: datetime) -> datetime:
    """A local date and time (or one with an offset, converted to local); it must lie ahead."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ScheduleError(f"'at' must be an ISO local time like 2026-10-18T14:00, got {value!r}") from error
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    if parsed <= now:
        raise ScheduleError(f"'at' {value} is not in the future; it is now {now:%Y-%m-%dT%H:%M}")
    return parsed


def build_repeat(args: dict[str, Any], today: datetime) -> dict[str, Any]:
    """The recurrence the arguments describe: every_minutes, or a time of day on every day, some weekdays or every N days."""
    if args.get("every_minutes") is not None:
        if any(args.get(key) is not None for key in ("time", "weekdays", "every_days")):
            raise ScheduleError("every_minutes cannot be combined with time, weekdays or every_days")
        minutes = int(args["every_minutes"])
        if minutes < MIN_INTERVAL_MINUTES:
            raise ScheduleError(f"every_minutes must be at least {MIN_INTERVAL_MINUTES}")
        return {"every_minutes": minutes}
    if args.get("time") is None:
        raise ScheduleError("a recurring schedule needs 'time' (local HH:MM) or 'every_minutes'")
    if args.get("weekdays") is not None and args.get("every_days") is not None:
        raise ScheduleError("use either weekdays or every_days, not both")
    parse_clock(str(args["time"]))
    repeat: dict[str, Any] = {"time": str(args["time"])}
    if args.get("weekdays") is not None:
        bad = [day for day in args["weekdays"] if day not in WEEKDAYS]
        if bad or not args["weekdays"]:
            raise ScheduleError(f"weekdays are {', '.join(WEEKDAYS)}")
        repeat["weekdays"] = sorted({WEEKDAYS.index(day) for day in args["weekdays"]})
    if args.get("every_days") is not None:
        repeat["every_days"] = int(args["every_days"])
        repeat["anchor"] = today.date().isoformat()
    return repeat


def next_after(repeat: dict[str, Any], after: datetime) -> datetime:
    """The first occurrence of a recurrence strictly after ``after``."""
    if "every_minutes" in repeat:
        return after + timedelta(minutes=repeat["every_minutes"])
    hour, minute = parse_clock(repeat["time"])
    weekdays = repeat.get("weekdays")
    step = repeat.get("every_days")
    anchor = datetime.fromisoformat(repeat["anchor"]).date() if step else None
    for offset in range(0, 366 * 2):
        day = (after + timedelta(days=offset)).date()
        candidate = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
        if candidate <= after:
            continue
        if weekdays is not None and day.weekday() not in weekdays:
            continue
        if anchor is not None and (day - anchor).days % step != 0:
            continue
        return candidate
    raise ScheduleError("this recurrence never comes due")


def describe_repeat(repeat: dict[str, Any]) -> str:
    if "every_minutes" in repeat:
        return f"every {repeat['every_minutes']} minutes"
    if "weekdays" in repeat:
        days = ", ".join(WEEKDAYS[index] for index in repeat["weekdays"])
        return f"{days} at {repeat['time']}"
    if "every_days" in repeat:
        return f"every {repeat['every_days']} days at {repeat['time']}"
    return f"every day at {repeat['time']}"


class Scheduler:
    """Waits and schedules of one workspace; see the module docstring."""

    def __init__(self, workspace: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.workspace = Path(workspace)
        self._path = self.workspace / ".kvist" / "schedules.json"
        self._clock = clock
        self._waits: dict[str, Entry] = {}
        self._due: list[Fired] = []
        self._ids = itertools.count(1)
        self._cond = threading.Condition()
        self._listener: Callable[[], None] | None = None
        self._thread: threading.Thread | None = None

    # ── timer ────────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        """Whether this core's timer runs, so a wait or schedule can wake the agent."""
        return self._listener is not None

    def set_listener(self, listener: Callable[[], None] | None) -> None:
        """Start the timer with a listener; None stops it (and drops waits, which end with the conversation)."""
        with self._cond:
            self._listener = listener
            if listener is None:
                self._waits.clear()
                self._cond.notify_all()
                return
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="kvist-scheduler", daemon=True)
                self._thread.start()
            self._cond.notify_all()

    def clear_waits(self) -> None:
        """A new or switched conversation did not ask for the old one's waits."""
        with self._cond:
            self._waits.clear()
            self._due = [item for item in self._due if item.kind != WAIT]

    def _run(self) -> None:
        while True:
            with self._cond:
                if self._listener is None:
                    return
                self._cond.wait(self._seconds_to_next())
                if self._listener is None:
                    return
            if self._take_due():
                listener = self._listener
                if listener is not None:
                    listener()

    def _seconds_to_next(self) -> float:
        upcoming = [entry.next_run for entry in self._all() if not entry.paused]
        if not upcoming:
            return _TICK_SECONDS
        return max(0.0, min(_TICK_SECONDS, min(upcoming) - self._clock()))

    def _take_due(self) -> bool:
        """Queue every occurrence that came due; True when something did."""
        now = self._clock()
        fired = [*self._claim_waits(now), *self._claim_schedules(now)]
        if not fired:
            return False
        with self._cond:
            self._due.extend(fired)
        return True

    def _claim_waits(self, now: float) -> list[Fired]:
        with self._cond:
            ready = [entry for entry in self._waits.values() if entry.next_run <= now]
            for entry in ready:
                del self._waits[entry.id]
        return [Fired(entry.id, WAIT, entry.task, entry.next_run, late=False) for entry in ready]

    def _claim_schedules(self, now: float) -> list[Fired]:
        """Advance the due stored schedules under the file lock; only the entries this core advanced are fired."""
        if not self._path.exists():
            return []
        fired: list[Fired] = []

        def claim(document: dict[str, Any]) -> dict[str, Any]:
            kept = []
            for raw in document.get("entries", []):
                entry = Entry.from_dict(raw)
                if entry.paused or entry.next_run > now:
                    kept.append(raw)
                    continue
                fired.append(Fired(entry.id, entry.kind, entry.task, entry.next_run,
                                   late=now - entry.next_run > 2 * _TICK_SECONDS))
                entry.runs += 1
                entry.last_run = now
                if entry.kind == RECURRING:
                    entry.next_run = next_after(entry.repeat, datetime.fromtimestamp(now)).timestamp()
                    kept.append(entry.to_dict())
            return {"version": 1, "entries": kept}

        update_json_object(self._path, claim, boundary=self.workspace, empty_as_missing=True)
        return fired

    # ── what the model is told ───────────────────────────────────────────

    def has_due(self) -> bool:
        with self._cond:
            return bool(self._due)

    def take_note(self) -> str:
        """The occurrences that came due since the last note, as facts for the next request; empty when none."""
        with self._cond:
            fired, self._due = self._due, []
        lines = [f"Scheduled wake-ups that came due ({self._local(self._clock())}):"]
        for item in fired:
            when = f"was due {self._local(item.due_at)}, Kvist was not running" if item.late else f"due {self._local(item.due_at)}"
            label = "wait over" if item.kind == WAIT else f"schedule {item.id}"
            lines.append(f"- {label} ({when}): {item.task}")
        return "\n".join(lines) if fired else ""

    @staticmethod
    def _local(moment: float) -> str:
        return datetime.fromtimestamp(moment).strftime("%Y-%m-%d %H:%M")

    # ── operations the tools call ────────────────────────────────────────

    def now(self) -> datetime:
        return datetime.fromtimestamp(self._clock())

    def start_wait(self, seconds: float, reason: str) -> dict[str, Any]:
        if not self.active:
            raise ScheduleError("nothing can wake the agent in this interface, so it cannot wait in the background; "
                                "use job_output with wait on a background command, or end the turn and say so")
        if not 1 <= seconds <= MAX_WAIT_SECONDS:
            raise ScheduleError(f"seconds must be between 1 and {MAX_WAIT_SECONDS}; use schedule for a later time")
        entry = Entry(f"w{next(self._ids)}", WAIT, reason, self._clock() + seconds, created_at=self._clock())
        with self._cond:
            self._waits[entry.id] = entry
            self._cond.notify_all()
        return {"wait_id": entry.id, "wakes_at": self._local(entry.next_run),
                "note": "You are woken when it ends. Say what you need to now, and end the turn."}

    def add(self, task: str, args: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        kind, next_run, repeat = self._timing(args, now)
        entry = Entry(f"s{int(self._clock() * 1000) % 10**8:x}", kind, task, next_run.timestamp(), repeat,
                      created_at=self._clock())

        def append(document: dict[str, Any]) -> dict[str, Any]:
            entries = list(document.get("entries", []))
            if len(entries) >= MAX_SCHEDULES:
                raise ScheduleError(f"at most {MAX_SCHEDULES} schedules; cancel one first")
            return {"version": 1, "entries": [*entries, entry.to_dict()]}

        update_json_object(self._path, append, boundary=self.workspace, empty_as_missing=True)
        self._poke()
        return {"scheduled": self._public(entry), **self._delivery_note()}

    def update(self, schedule_id: str, args: dict[str, Any]) -> dict[str, Any]:
        changed: list[Entry] = []

        def apply(document: dict[str, Any]) -> dict[str, Any]:
            entries = []
            for raw in document.get("entries", []):
                entry = Entry.from_dict(raw)
                if entry.id == schedule_id:
                    self._apply_changes(entry, args)
                    changed.append(entry)
                entries.append(entry.to_dict())
            return {"version": 1, "entries": entries}

        update_json_object(self._path, apply, boundary=self.workspace, empty_as_missing=True)
        if not changed:
            raise ScheduleError(f"no schedule {schedule_id}")
        self._poke()
        return {"updated": self._public(changed[0])}

    def _apply_changes(self, entry: Entry, args: dict[str, Any]) -> None:
        if isinstance(args.get("task"), str):
            entry.task = args["task"]
        if any(args.get(key) is not None for key in ("at", *_RECURRENCE_KEYS)):
            entry.kind, next_run, entry.repeat = self._timing(args, self.now())
            entry.next_run = next_run.timestamp()
            entry.paused = False
        if args.get("paused") is not None:
            entry.paused = bool(args["paused"])
            if not entry.paused and entry.kind == RECURRING and entry.next_run <= self._clock():
                entry.next_run = next_after(entry.repeat, self.now()).timestamp()

    def cancel(self, schedule_id: str) -> dict[str, Any]:
        found: list[bool] = []

        def drop(document: dict[str, Any]) -> dict[str, Any]:
            entries = list(document.get("entries", []))
            kept = [raw for raw in entries if raw.get("id") != schedule_id]
            found.append(len(kept) != len(entries))
            return {"version": 1, "entries": kept}

        with self._cond:
            if self._waits.pop(schedule_id, None) is not None:
                return {"cancelled": schedule_id}
        update_json_object(self._path, drop, boundary=self.workspace, empty_as_missing=True)
        if not found[0]:
            raise ScheduleError(f"no schedule or wait {schedule_id}")
        self._poke()
        return {"cancelled": schedule_id}

    def list(self) -> dict[str, Any]:
        return {"now": self._local(self._clock()), "schedules": [self._public(entry) for entry in self._all()],
                **self._delivery_note()}

    # ── helpers ──────────────────────────────────────────────────────────

    def _timing(self, args: dict[str, Any], now: datetime) -> tuple[str, datetime, dict[str, Any]]:
        if args.get("at") is not None:
            if any(args.get(key) is not None for key in _RECURRENCE_KEYS):
                raise ScheduleError("'at' is a one-time schedule; it cannot be combined with a recurrence")
            return ONCE, parse_at(str(args["at"]), now), {}
        repeat = build_repeat(args, now)
        return RECURRING, next_after(repeat, now), repeat

    def _stored(self) -> list[Entry]:
        document = read_json_object(self._path, boundary=self.workspace, empty_as_missing=True)
        return [Entry.from_dict(raw) for raw in document.get("entries", [])]

    def _all(self) -> list[Entry]:
        with self._cond:
            waits = list(self._waits.values())
        return [*self._stored(), *waits]

    def _poke(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def _delivery_note(self) -> dict[str, Any]:
        if self.active:
            return {}
        return {"note": "This interface cannot wake the agent by itself: the schedule is saved and runs when Kvist "
                        "is open in an interface that can (the terminal app or an IDE panel)."}

    def _public(self, entry: Entry) -> dict[str, Any]:
        view: dict[str, Any] = {"id": entry.id, "task": entry.task, "next_run": self._local(entry.next_run),
                                "runs": entry.runs}
        view["repeat"] = describe_repeat(entry.repeat) if entry.repeat else "once"
        if entry.kind == WAIT:
            view["repeat"] = "wait"
        if entry.paused:
            view["paused"] = True
        return view
