# -*- coding: utf-8 -*-
"""Training-data record of a run: every model call as sent and received, and what its calls returned.

Off unless ``KVIST_TRAJECTORY_DIR=<dir>`` is set. One JSON-lines file per run,
``<dir>/<session folder>/<run_id>.jsonl``; a resumed run appends to its own file.
The session folder is ``<workspace hash>-<session name>``: session names repeat
across workspaces. ``/rate`` moves a session's folder into ``good/`` or ``bad/``
(``rate_session``). Record types:

``run_start``  the run's goal, provider, model, role and effort.
``message``    one distinct chat message (or tool list), stored once under ``id``.
``call``       one model call that got an answer: ``kind`` ("step", "report", ...),
               ``protocol`` ("native" or "text"), the ids of the messages and tools
               exactly as sent, and the response: content, reasoning, tool calls.
``results``    the calls of one response with their arguments and the result text
               the model was given back for each.
``run_end``    how the run ended and its final message.

Why messages are stored by id: a native-tools step sends the whole conversation
again and a text step sends the same system prompt again, so writing each prompt
in full would make a run's file grow with the square of its steps. Every id is a
hash of the message, so the exact prompt of any call is rebuilt by looking up its ids.

Why everything passes through ``redact_value``: the files are made to leave the
machine (a rented GPU, a dataset), and a prompt can hold a key the model read.

A failing disk costs the run its record, never the run: the first failure is said
once in debug output and the rest of the run records nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kernel.sandbox.redaction import redact_text, redact_value
from kernel.session.durable_json import JsonStorageError, append_private_bytes

TRAJECTORY_DIR_ENV = "KVIST_TRAJECTORY_DIR"


def trajectory_dir() -> Path | None:
    raw = os.environ.get(TRAJECTORY_DIR_ENV, "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def message_id(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


@dataclass(slots=True)
class Trajectory:
    run_id: str
    path: Path
    root: Path
    known: set[str] = field(default_factory=set)
    failed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def write(self, records: list[dict[str, Any]]) -> str | None:
        """Append the records; the error text when the disk refused them."""
        if self.failed:
            return None
        data = "".join(json.dumps(record, ensure_ascii=False, default=str) + "\n" for record in records)
        try:
            append_private_bytes(self.path, data.encode("utf-8"), boundary=self.root)
        except (JsonStorageError, OSError) as error:
            self.failed = True
            return redact_text(str(error))
        return None

    def intern(self, value: Any, records: list[dict[str, Any]]) -> str:
        """The id of ``value``, adding its ``message`` record the first time it is seen."""
        safe = redact_value(value)
        key = message_id(safe)
        if key not in self.known:
            self.known.add(key)
            records.append({"type": "message", "id": key, "message": safe})
        return key


LABELS = ("good", "bad")


def session_folder(agent: Any) -> str:
    workspace = str(Path(agent.executor.workspace).resolve())
    digest = hashlib.sha256(workspace.encode("utf-8")).hexdigest()[:8]
    return f"{digest}-{getattr(agent, 'current_session', 'default')}"


def _recorder(agent: Any) -> Trajectory | None:
    root = trajectory_dir()
    run_id = getattr(agent, "_run_id", None)
    if root is None or not run_id:
        return None
    current = getattr(agent, "_trajectory", None)
    if current is not None and current.run_id == run_id:
        return current
    folder = root / session_folder(agent)
    path = folder / f"{run_id}.jsonl"
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as error:
        agent._ui.debug_print(f"[trajectory] not recorded: {redact_text(str(error))}")
        agent._trajectory = Trajectory(run_id, path, root, failed=True)
        return None
    agent._trajectory = Trajectory(run_id, path, root)
    return agent._trajectory


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _append(agent: Any, build: Any) -> None:
    """Build the records under the run's lock and write them; ``build`` gets the trajectory."""
    trajectory = _recorder(agent)
    if trajectory is None:
        return
    with trajectory.lock:
        if trajectory.failed:
            return
        error = trajectory.write(build(trajectory))
    if error:
        agent._ui.debug_print(f"[trajectory] recording stopped for this run: {error}")


def note_run_start(agent: Any, user_goal: str, start_step: int) -> None:
    config = agent.config
    state = agent._state
    record = {
        "type": "run_start",
        "ts": _now(),
        "run_id": agent._run_id,
        "session": getattr(agent, "current_session", "default"),
        "start_step": start_step,
        "provider": config.provider,
        "model": str(getattr(state, "selected_action_model", "") or ""),
        "role": state.current_agent_role,
        "reasoning_effort": config.reasoning_effort,
        "response_language": state.response_language,
        "goal": redact_text(user_goal),
    }
    _append(agent, lambda _trajectory: [record])


def note_call(
    agent: Any,
    *,
    kind: str,
    protocol: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    response: dict[str, Any],
    usage: dict[str, Any],
    seconds: float,
) -> None:
    """One model call that came back with an answer."""

    def build(trajectory: Trajectory) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        ids = [trajectory.intern(message, records) for message in messages]
        tools_id = trajectory.intern(tools, records) if tools else None
        records.append(
            {
                "type": "call",
                "ts": _now(),
                "step": getattr(agent, "_current_step", 0),
                "kind": kind,
                "protocol": protocol,
                "model": model,
                "messages": ids,
                "tools": tools_id,
                "response": redact_value(response),
                "usage": usage,
                "seconds": round(seconds, 3),
            }
        )
        return records

    _append(agent, build)


def note_results(agent: Any, step: int, calls: list[dict[str, Any]]) -> None:
    """The calls of one response, each with the result text the model was given back."""
    record = {"type": "results", "ts": _now(), "step": step, "calls": redact_value(calls)}
    _append(agent, lambda _trajectory: [record])


def note_run_end(agent: Any, *, outcome: str, final_message: str, steps: int) -> None:
    state = agent._state
    record = {
        "type": "run_end",
        "ts": _now(),
        "outcome": outcome,
        "final_message": redact_text(final_message),
        "steps": steps,
        "files_changed": sorted(str(path) for path in state.files_changed),
        "finished_incomplete": bool(state.finished_incomplete),
    }
    _append(agent, lambda _trajectory: [record])


def _session_places(root: Path, folder: str) -> list[tuple[str, Path]]:
    """(label, folder) for each place a session's runs can be: not rated yet, good, bad."""
    return [("", root / folder), *((label, root / label / folder) for label in LABELS)]


def session_label(agent: Any) -> tuple[str | None, int]:
    """The session's label ("" when not rated yet, None when nothing is recorded) and its number of runs.

    A session rated earlier and continued since has runs in two places; the newer, unrated ones decide nothing,
    so the label is the one its rated runs carry.
    """
    root = trajectory_dir()
    if root is None:
        return None, 0
    found: dict[str, int] = {}
    for label, place in _session_places(root, session_folder(agent)):
        count = len(list(place.glob("*.jsonl"))) if place.is_dir() else 0
        if count:
            found[label] = count
    if not found:
        return None, 0
    rated = [label for label in LABELS if label in found]
    return (rated[0] if rated else ""), sum(found.values())


def rate_session(agent: Any, label: str) -> Path:
    """Move every run of the agent's session into ``<root>/<label>/<session folder>/``; returns that folder.

    A run file already there (a run resumed after an earlier rating) gets the new records appended: records
    never depend on their neighbours, and a ``message`` stored twice reads back the same.
    """
    root = trajectory_dir()
    if root is None or label not in LABELS:
        raise ValueError(f"cannot rate a session as {label!r} without {TRAJECTORY_DIR_ENV}")
    folder = session_folder(agent)
    target = root / label / folder
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    for _label, place in _session_places(root, folder):
        if place == target or not place.is_dir():
            continue
        for source in sorted(place.glob("*.jsonl")):
            _move_run(source, target / source.name, root)
        if not any(place.iterdir()):
            place.rmdir()
    # The next record of this run opens its file afresh, in the new place.
    agent._trajectory = None
    return target


def _move_run(source: Path, destination: Path, root: Path) -> None:
    if destination.exists():
        append_private_bytes(destination, source.read_bytes(), boundary=root)
        source.unlink()
    else:
        source.replace(destination)
    # The empty lock file append_private_bytes leaves beside a file; it would keep the old folder alive.
    source.with_name(f".{source.name}.lock").unlink(missing_ok=True)


def _pick(ru: bool, russian: str, english: str) -> str:
    return russian if ru else english


def rate_dialog(agent: Any, ui: Any, ru: bool) -> tuple[bool, str]:
    """``/rate``: ask whether the session is a good example and file it; ``(True, "")`` when the person left."""
    if trajectory_dir() is None:
        return False, _pick(
            ru,
            f"Запись траекторий выключена. Задай {TRAJECTORY_DIR_ENV}=<папка> до запуска Kvist.",
            f"Trajectory recording is off. Set {TRAJECTORY_DIR_ENV}=<folder> before starting Kvist.",
        )
    current, runs = session_label(agent)
    if current is None:
        return False, _pick(ru, "В этой сессии ещё ничего не записано.", "Nothing is recorded in this session yet.")
    state = {
        "": _pick(ru, "ещё не оценена", "not rated yet"),
        "good": _pick(ru, "сейчас: хорошая", "now: good"),
        "bad": _pick(ru, "сейчас: плохая", "now: bad"),
    }[current]
    choice = ui.choose_option(
        _pick(ru, "Оценка сессии для обучения", "Rate this session as training data"),
        [
            {"value": "good", "label": _pick(ru, "Хороший пример", "Good example"),
             "description": _pick(ru, "работа, на которой стоит учиться (good/)", "work worth learning from (good/)")},
            {"value": "bad", "label": _pick(ru, "Плохой пример", "Bad example"),
             "description": _pick(ru, "так делать не надо (bad/)", "how not to do it (bad/)")},
        ],
        current_value=current or "good",
        help_text=_pick(
            ru,
            f"Прогонов в сессии: {runs}, {state}. Выбор только переносит файлы в {trajectory_dir()}, никуда их не отправляет.",
            f"Runs in this session: {runs}, {state}. The choice only moves files within {trajectory_dir()}; nothing is sent.",
        ),
    )
    if choice not in LABELS:
        return True, ""
    try:
        target = rate_session(agent, choice)
    except (JsonStorageError, OSError) as error:
        return False, _pick(ru, "Не удалось перенести сессию: ", "Could not move the session: ") + redact_text(str(error))
    kind = {"good": _pick(ru, "хороший", "a good"), "bad": _pick(ru, "плохой", "a bad")}[choice]
    return True, _pick(ru, f"Сессия сохранена как {kind} пример: {target}", f"Session filed as {kind} example: {target}")


def read_trajectory(path: Path) -> list[dict[str, Any]]:
    """The run's calls with their prompts rebuilt in full, in order, plus its other records.

    A ``call`` comes back with ``messages`` and ``tools`` replaced by the values
    themselves; ``message`` records are folded away.
    """
    table: dict[str, Any] = {}
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["type"] == "message":
            table[record["id"]] = record["message"]
            continue
        if record["type"] == "call":
            record["messages"] = [table[key] for key in record["messages"]]
            record["tools"] = table[record["tools"]] if record["tools"] else None
        out.append(record)
    return out
