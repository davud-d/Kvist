# -*- coding: utf-8 -*-
"""Per-session event journal: what the agent did, kept so a frontend can redraw it.

The session document keeps only what the model needs as context (goal, role, a
short result). This journal keeps what a person saw: routes, plans, steps, the
commands that ran with their output, the files that were written, thoughts and
the full answers. It records the *frontend protocol's own events* (redacted by
``EventStreamUI`` exactly as a live frontend receives them), so a frontend
restores a session by replaying them through the reducer it already has.

Storage is an append-only JSONL file next to the session document, because the
document is rewritten whole on every step and a journal inside it would make
each step cost as much as the whole history. The journal is display-only: it
never reaches a model prompt, and losing it never loses the conversation.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.session.durable_json import (
    JsonStorageError,
    append_private_bytes,
    delete_private_file,
    read_private_bytes,
    update_private_bytes,
)
from kernel.protocol.event_stream_ui import EventStreamUI
from kernel.sandbox.redaction import redact_text

JOURNAL_SUFFIX = ".events.jsonl"

# One string inside an event (a written file, a command's arguments, a thought).
MAX_FIELD_CHARS = 20_000
# Answers are what the user came for, so they get a much higher ceiling.
MAX_ANSWER_CHARS = 100_000
MAX_LIST_ITEMS = 200
# A turn of the biggest realistic run (~400 steps, a handful of events each).
MAX_EVENTS_PER_TURN = 2_000
MAX_TURN_BYTES = 2 * 1024 * 1024
# Headroom that only answers, errors and the turn's own bookkeeping may use, so
# a runaway turn still ends with its outcome on record.
_RESERVE_EVENTS = 100
_RESERVE_BYTES = 512 * 1024
MAX_SESSION_BYTES = 8 * 1024 * 1024
KEEP_SESSION_BYTES = 6 * 1024 * 1024
MAX_SESSION_TURNS = 500
KEEP_SESSION_TURNS = 400

DEFAULT_HISTORY_LIMIT = 500
MAX_HISTORY_LIMIT = 5_000

_TRUNCATION_MARKER = "\n[... {omitted} characters omitted ...]\n"

# Live-only state: a frontend never needs it to redraw a finished conversation.
_TRANSIENT_EVENTS = frozenset(
    {"status", "live_start", "live_stop", "debug", "stream_chunk", "action_output", "job_output", "context", "narration"}
)
_ANSWER_EVENTS = frozenset({"final", "message", "stream_commit"})
_ESSENTIAL_EVENTS = _ANSWER_EVENTS | {"error", "journal_notice", "turn_end", "session_ended"}


def truncate_middle(text: str, limit: int) -> str:
    """Keep both ends of an oversized string: the failure is usually at the tail."""
    if len(text) <= limit:
        return text
    half = limit // 2
    omitted = len(text) - 2 * half
    return text[:half] + _TRUNCATION_MARKER.format(omitted=omitted) + text[-half:]


def _cap(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return truncate_middle(value, limit)
    if isinstance(value, list):
        items = [_cap(item, limit) for item in value[:MAX_LIST_ITEMS]]
        if len(value) > MAX_LIST_ITEMS:
            items.append(f"[... {len(value) - MAX_LIST_ITEMS} items omitted ...]")
        return items
    if isinstance(value, dict):
        return {key: _cap(item, limit) for key, item in value.items()}
    return value


def cap_event(event: dict[str, Any]) -> dict[str, Any]:
    limit = MAX_ANSWER_CHARS if event.get("type") in _ANSWER_EVENTS else MAX_FIELD_CHARS
    return _cap(event, limit)


# --- Reading ------------------------------------------------------------------


@dataclass(slots=True)
class _Entry:
    seq: int
    turn: int
    ts: float
    event: dict[str, Any]
    line: bytes


def _parse_entry(line: bytes) -> _Entry | None:
    try:
        raw = json.loads(line)
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    seq, turn, event = raw.get("seq"), raw.get("turn"), raw.get("event")
    if type(seq) is not int or type(turn) is not int or not isinstance(event, dict):
        return None
    ts = raw.get("ts")
    return _Entry(seq, turn, float(ts) if isinstance(ts, (int, float)) else 0.0, event, line)


def _parse(raw: bytes) -> tuple[list[_Entry], int]:
    """Valid entries in seq order plus the number of lines that were not.

    A crash can leave a torn last line, and a damaged line must cost only
    itself, never the rest of the journal.
    """
    entries: list[_Entry] = []
    skipped = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        entry = _parse_entry(line)
        if entry is None:
            skipped += 1
            continue
        entries.append(entry)
    entries.sort(key=lambda entry: entry.seq)
    return entries, skipped


def read_history(
    path: Path,
    workspace: Path,
    *,
    limit: int = DEFAULT_HISTORY_LIMIT,
    before: int | None = None,
    after: int | None = None,
) -> dict[str, Any]:
    """Read a cursor page from the durable journal.

    ``before`` pages backwards and keeps the newest matching events, preserving
    the original history-view contract. ``after`` pages forwards and keeps the
    oldest matching events, which lets reconnecting frontends recover every
    event after their last durable cursor without rereading the transcript.
    """
    if before is not None and after is not None:
        raise ValueError("History cursors 'before' and 'after' are mutually exclusive")
    raw = read_private_bytes(path, boundary=workspace) or b""
    entries, skipped = _parse(raw)
    total = len(entries)
    if before is not None:
        entries = [entry for entry in entries if entry.seq < before]
    if after is not None:
        entries = [entry for entry in entries if entry.seq > after]
        selected = entries[:limit]
        has_more = len(entries) > limit
    else:
        older = max(0, len(entries) - limit)
        selected = entries[older:]
        has_more = older > 0
    return {
        "events": [
            {"seq": e.seq, "turn": e.turn, "ts": e.ts, "event": e.event}
            for e in selected
        ],
        "has_more": has_more,
        "total_events": total,
        "skipped_lines": skipped,
        "journal_bytes": len(raw),
    }


def journal_size(path: Path) -> int:
    try:
        return path.lstat().st_size
    except FileNotFoundError:
        return 0
    except OSError as error:
        raise JsonStorageError(f"Cannot inspect journal {path.name!r}") from error


def delete_journal(path: Path, workspace: Path) -> bool:
    # Looked up first: taking the storage lock would create `.kvist/sessions`
    # in a workspace that never had one.
    if not os.path.lexists(path):
        return False
    return delete_private_file(path, boundary=workspace)


# --- Compaction ---------------------------------------------------------------


@dataclass(slots=True)
class _Stats:
    next_seq: int
    next_turn: int
    turns: int


def _stats_of(entries: list[_Entry]) -> _Stats:
    turns = {entry.turn for entry in entries if entry.turn > 0}
    return _Stats(
        next_seq=max((entry.seq for entry in entries), default=0) + 1,
        next_turn=max((entry.turn for entry in entries), default=0) + 1,
        turns=len(turns),
    )


def _compacted(raw: bytes) -> tuple[bytes, _Stats]:
    """Drop the oldest whole turns until the journal is back under its keep size.

    The newest turn always survives. Turn 0 is the journal's own notice saying
    how many older turns were dropped, so a frontend can say so instead of
    silently starting mid-conversation.
    """
    entries, _skipped = _parse(raw)
    stats = _stats_of(entries)
    omitted = sum(
        int(entry.event.get("omitted_turns", 0))
        for entry in entries
        if entry.turn == 0 and entry.event.get("type") == "journal_notice"
    )
    turns: dict[int, list[_Entry]] = {}
    for entry in entries:
        if entry.turn > 0:
            turns.setdefault(entry.turn, []).append(entry)
    order = list(turns)
    total = sum(len(entry.line) + 1 for entry in entries if entry.turn > 0)
    dropped = 0
    while len(order) - dropped > 1 and (
        len(order) - dropped > KEEP_SESSION_TURNS or total > KEEP_SESSION_BYTES
    ):
        total -= sum(len(entry.line) + 1 for entry in turns[order[dropped]])
        dropped += 1
    kept = [entry for turn in order[dropped:] for entry in turns[turn]]
    lines: list[bytes] = []
    if omitted + dropped and kept:
        notice = {"type": "journal_notice", "omitted_turns": omitted + dropped}
        lines.append(_encode(kept[0].seq - 1, 0, notice))
    lines.extend(entry.line + b"\n" for entry in kept)
    stats.turns = len(order) - dropped
    return b"".join(lines), stats


def _encode(seq: int, turn: int, event: dict[str, Any], ts: float | None = None) -> bytes:
    record = {"seq": seq, "turn": turn, "ts": round(time.time() if ts is None else ts, 3), "event": event}
    return (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


# --- Writing ------------------------------------------------------------------


def write_imported_turns(
    path: Path,
    workspace: Path,
    turns: list[tuple[float, list[dict[str, Any]]]],
) -> None:
    """Write finished turns taken from another agent into a new session's journal.

    Each turn is its time and its events. The journal's own size limits apply
    as they do to a live one: the oldest turns are dropped and a notice says
    how many.
    """
    lines: list[bytes] = []
    seq = 1
    for number, (ts, events) in enumerate(turns, start=1):
        for event in events:
            lines.append(_encode(seq, number, cap_event(event), ts))
            seq += 1
    raw, _stats = _compacted(b"".join(lines))
    append_private_bytes(path, raw, boundary=workspace, sync=True)


class SessionJournal:
    """Collects one turn's events and appends them to the active session's journal.

    It plays the part of a ``FrontendChannel`` for an ``EventStreamUI`` (only
    ``emit`` is used), so the journal sees the events a frontend would see.
    Live-only events are dropped; a streamed thought, whose chunks are
    cumulative snapshots, is written once, as its last snapshot.
    """

    def __init__(
        self,
        workspace: Path,
        current_path: Callable[[], Path],
        report: Callable[[str], None],
    ) -> None:
        self._workspace = workspace
        self._current_path = current_path
        self._report = report
        self._lock = threading.RLock()
        self._stats: dict[Path, _Stats] = {}
        self._active = False
        self._failed = False
        self._stopped = False
        self._pending_user: dict[str, Any] | None = None
        self._turn = 0
        self._events = 0
        self._bytes = 0
        self._dropped = 0
        self._reasoning: dict[str, str] = {}
        self._input_tokens = 0
        self._output_tokens = 0
        self._model = ""
        self._context_tokens = 0
        self._context_limit = 0

    # -- turn lifecycle --

    def begin_turn(self, text: str, *, resumed: bool = False, woken: bool = False) -> None:
        """Open a turn; nothing reaches the disk until the first event of it.

        A woken turn was started by the runtime when background work ended: it
        has no request text, and its ``job_notice``/``helper`` events say why.
        """
        with self._lock:
            if self._active:
                return
            self._active = True
            self._failed = False
            self._stopped = False
            self._reasoning = {}
            self._input_tokens = self._output_tokens = 0
            self._context_tokens = self._context_limit = 0
            self._pending_user = {"type": "user", "text": text}
            if resumed:
                self._pending_user["resumed"] = True
            if woken:
                self._pending_user["woken"] = True

    def describe_turn(self, text: str) -> None:
        """Fill in the goal of a resumed turn, which is only known once loaded."""
        with self._lock:
            if self._pending_user is not None:
                self._pending_user["text"] = text

    def mark_stopped(self) -> None:
        with self._lock:
            self._stopped = True

    def end_turn(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
            if self._pending_user is not None:
                self._pending_user = None
                return
            self._guarded(self._write_turn_summary)

    def _write_turn_summary(self) -> None:
        self._flush_reasoning()
        if self._input_tokens or self._output_tokens:
            self._append(
                {
                    "type": "usage",
                    "input_tokens": self._input_tokens,
                    "output_tokens": self._output_tokens,
                    "model": self._model,
                    "kind": "turn",
                    "step": 0,
                    "context_tokens": self._context_tokens,
                    "context_limit": self._context_limit,
                }
            )
        if self._dropped:
            self._append({"type": "journal_notice", "omitted_events": self._dropped})
        end: dict[str, Any] = {"type": "turn_end"}
        if self._stopped:
            end["stopped_by_user"] = True
        self._append(end, sync=True)

    # -- event intake (the FrontendChannel surface EventStreamUI uses) --

    def emit(self, event: Any) -> None:
        payload = event.to_dict()
        kind = payload["type"]
        with self._lock:
            if not self._active or kind in _TRANSIENT_EVENTS:
                return
            if kind == "usage":
                self._add_usage(payload)
            elif kind == "reasoning_start":
                self._reasoning[payload["stream_id"]] = ""
            elif kind == "reasoning_chunk":
                stream_id = payload["stream_id"]
                kept = self._reasoning.get(stream_id, "") if payload.get("append") else ""
                self._reasoning[stream_id] = kept + payload["text"]
            elif kind == "reasoning_commit":
                self._guarded(lambda: self._write_reasoning(payload))
            else:
                self._guarded(lambda: self._append(payload))

    def _add_usage(self, payload: dict[str, Any]) -> None:
        self._input_tokens += payload["input_tokens"]
        self._output_tokens += payload["output_tokens"]
        self._model = payload["model"] or self._model
        self._context_tokens = payload.get("context_tokens", 0) or self._context_tokens
        self._context_limit = payload.get("context_limit", 0) or self._context_limit

    def _write_reasoning(self, commit: dict[str, Any]) -> None:
        stream_id = commit["stream_id"]
        text = self._reasoning.pop(stream_id, "")
        if not text.strip() and commit["duration_seconds"] < 1:
            return
        self._append({"type": "reasoning_start", "stream_id": stream_id})
        self._append({"type": "reasoning_chunk", "stream_id": stream_id, "text": text})
        self._append(commit)

    def _flush_reasoning(self) -> None:
        for stream_id in list(self._reasoning):
            self._write_reasoning(
                {
                    "type": "reasoning_commit",
                    "stream_id": stream_id,
                    "duration_seconds": 0.0,
                }
            )

    # -- disk --

    def _guarded(self, action: Callable[[], None]) -> None:
        if self._failed:
            return
        try:
            action()
        except (JsonStorageError, OSError) as error:
            # Display-only: a broken journal must never stop the agent, but the
            # user is told once that this turn will not be restorable.
            self._failed = True
            self._report(f"Session journal disabled for this turn: {error}")

    def _stats_for(self, path: Path) -> _Stats:
        stats = self._stats.get(path)
        if stats is None:
            raw = read_private_bytes(path, boundary=self._workspace) or b""
            stats = _stats_of(_parse(raw)[0])
            self._stats[path] = stats
        return stats

    def _compact(self, path: Path) -> None:
        result: list[_Stats] = []

        def update(raw: bytes) -> bytes:
            compacted, stats = _compacted(raw)
            result.append(stats)
            return compacted

        update_private_bytes(path, update, boundary=self._workspace)
        self._stats[path] = result[0]

    def _open_turn_on_disk(self, path: Path) -> None:
        goal = self._pending_user
        if goal is None:
            return
        self._pending_user = None
        if self._stats_for(path).turns >= MAX_SESSION_TURNS:
            self._compact(path)
        stats = self._stats_for(path)
        self._turn = stats.next_turn
        stats.next_turn += 1
        stats.turns += 1
        self._events = self._bytes = self._dropped = 0
        goal["text"] = redact_text(goal["text"])
        self._write(path, cap_event(goal))

    def _append(self, payload: dict[str, Any], *, sync: bool = False) -> None:
        path = self._current_path()
        self._open_turn_on_disk(path)
        self._write(path, cap_event(payload), sync=sync)

    def _write(self, path: Path, payload: dict[str, Any], *, sync: bool = False) -> None:
        stats = self._stats_for(path)
        line = _encode(stats.next_seq, self._turn, payload)
        if not self._admits(payload["type"], len(line)):
            self._dropped += 1
            return
        size = append_private_bytes(path, line, boundary=self._workspace, sync=sync)
        stats.next_seq += 1
        self._events += 1
        self._bytes += len(line)
        if size > MAX_SESSION_BYTES:
            self._compact(path)

    def _admits(self, kind: str, size: int) -> bool:
        reserve = kind in _ESSENTIAL_EVENTS or kind == "user"
        event_limit = MAX_EVENTS_PER_TURN + (_RESERVE_EVENTS if reserve else 0)
        byte_limit = MAX_TURN_BYTES + (_RESERVE_BYTES if reserve else 0)
        return self._events < event_limit and self._bytes + size <= byte_limit


# --- The UI the agent talks to --------------------------------------------------


class JournalingUI:
    """Shows everything on the real UI and records it to the session journal.

    Attribute access falls through to the wrapped UI, so capability probes such
    as ``getattr(ui, "ask_choice", None)`` and settings like ``ui.verbose`` see
    the real UI. Only the calls that make up the visible feed are duplicated
    onto an ``EventStreamUI`` whose channel is the journal.
    """

    def __init__(self, ui: Any, journal: SessionJournal) -> None:
        object.__setattr__(self, "_ui", ui)
        object.__setattr__(self, "_events", EventStreamUI(journal, self._live_stream_id))

    def _live_stream_id(self) -> str:
        # The journal copy of a stream opens right after the live copy, so the
        # live UI's last id is the one its frontend already holds. A UI that
        # sends no stream ids (the classic terminal) leaves the journal its own.
        live_id = getattr(self._ui, "last_stream_id", "")
        return live_id or uuid.uuid4().hex

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ui, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._ui, name, value)

    def _forward(self, name: str, *args: Any) -> None:
        method = getattr(self._ui, name, None)
        if method is not None:
            method(*args)

    def print_route(self, role: str, source: str) -> None:
        self._events.print_route(role, source)
        self._forward("print_route", role, source)

    def print_session_title(self, session: str, title: str) -> None:
        # Fires from the background title-generation thread, strictly after
        # the turn that started it has already ended — never routed through
        # the journal (turn-scoped and inactive by then), only forwarded live.
        self._forward("print_session_title", session, title)

    def print_session_ended(self, payload: dict) -> None:
        # Recorded, so a replay of the session shows that it was closed.
        self._events.print_session_ended(payload)
        self._forward("print_session_ended", payload)

    # Skills, helpers and their results are part of the conversation a replay
    # redraws. Screen and task-state events are not: they carry live viewer
    # tokens and transient states, so they fall through to the live UI only.

    def print_skill_status(self, payload: dict) -> None:
        self._events.print_skill_status(payload)
        self._forward("print_skill_status", payload)

    def print_helper(self, payload: dict) -> None:
        self._events.print_helper(payload)
        if getattr(self._ui, "print_helper", None) is not None:
            self._ui.print_helper(payload)
        elif payload.get("event") in ("started", "done", "failed", "stopped"):
            self._ui.print_result(f"[{payload['agent_id']}] {payload['event']}: {payload.get('summary') or payload.get('task') or ''}")

    def print_helper_activity(self, payload: dict) -> None:
        # Live only: the helper's own service session journals this activity.
        self._forward("print_helper_activity", payload)

    def print_artifact(self, payload: dict) -> None:
        # The preview is a data URL: shown live, never stored in the journal.
        self._events.print_artifact({**payload, "preview": ""})
        if getattr(self._ui, "print_artifact", None) is not None:
            self._ui.print_artifact(payload)
        else:
            self._ui.print_result(f"{payload.get('caption') or payload['name']}: {payload['path']}")

    def print_skill_message(self, skill: str, label: str, markdown: str) -> None:
        self._events.print_skill_message(skill, label, markdown)
        if getattr(self._ui, "print_skill_message", None) is not None:
            self._ui.print_skill_message(skill, label, markdown)
        else:
            self._ui.print_user_markdown(f"**{label}:** {markdown}")

    def print_plan(self, markdown: str) -> None:
        self._events.print_plan(markdown)
        self._ui.print_plan(markdown)

    def print_step_header(self, step: int, command: Any, reasoning_seconds: float = 0.0) -> None:
        self._events.print_step_header(step, command, reasoning_seconds)
        self._ui.print_step_header(step, command, reasoning_seconds)

    def print_action_result(self, result: dict) -> None:
        self._events.print_action_result(result)
        self._forward("print_action_result", result)

    def print_job_started(self, job_id: str, command: list[str], purpose: str, call_id: str = "") -> None:
        self._events.print_job_started(job_id, command, purpose, call_id)
        self._forward("print_job_started", job_id, command, purpose, call_id)

    def print_job_finished(self, job_id: str, status: str, exit_code: int | None, output: str) -> None:
        self._events.print_job_finished(job_id, status, exit_code, output)
        self._forward("print_job_finished", job_id, status, exit_code, output)

    def print_job_notice(self, text: str, job_ids: list[str]) -> None:
        self._events.print_job_notice(text, job_ids)
        self._forward("print_job_notice", text, job_ids)

    def print_usage(self, usage: dict) -> None:
        self._events.print_usage(usage)
        self._forward("print_usage", usage)

    def print_narration(self, text: str) -> None:
        # Live only: the narrator's lines are for the person's ears in the moment.
        self._events.print_narration(text)
        self._forward("print_narration", text)

    def set_thinking(self, text: str) -> None:
        self._forward("set_thinking", text)
        self._events.set_thinking(text)

    def end_thinking(self) -> None:
        self._events.end_thinking()
        self._forward("end_thinking")

    def print_final(
        self, final_message: str, user_messages: list[str] | None = None, outcome: str = "done"
    ) -> None:
        self._events.print_final(final_message, user_messages, outcome)
        self._ui.print_final(final_message, user_messages, outcome=outcome)

    def print_user_markdown(self, message: str) -> None:
        self._events.print_user_markdown(message)
        self._ui.print_user_markdown(message)

    def print_result(self, message: str) -> None:
        self._events.print_result(message)
        self._ui.print_result(message)

    def print_error(self, message: str) -> None:
        self._events.print_error(message)
        self._ui.print_error(message)

    def print_sandbox_notice(self, note: str) -> None:
        self._events.print_sandbox_notice(note)
        self._ui.print_sandbox_notice(note)

    @contextlib.contextmanager
    def live_markdown(self) -> Iterator[Callable[[str], None]]:
        with self._ui.live_markdown() as show, self._events.live_markdown() as record:
            show_reasoning = getattr(show, "reasoning", None)

            def update(message: str) -> None:
                record(message)
                show(message)

            def reasoning(message: str) -> None:
                record.reasoning(message)
                if show_reasoning is not None:
                    show_reasoning(message)

            update.reasoning = reasoning
            yield update
