# -*- coding: utf-8 -*-
"""The ``/export`` command: pick another agent's sessions and import them.

Shared by the classic terminal and the stdio frontends; ``ui`` supplies the
dialogs (``choose_option``) and the caller turns the outcome into its own reply.

    /export                       pick the agent, then the session
    /export list                  what was found, nothing imported
    /export <agent>               pick a session of that agent
    /export <agent> all           every session of that agent from this folder
    /export all                   every agent, every session from this folder
    add ``everywhere`` to include sessions that ran in other folders
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from kernel.durable_json import JsonStorageError
from kernel.session_import import SOURCES, SourceUnreadable, find_sessions, here, source_by_key
from kernel.session_import.model import SessionSummary, Source
from kernel.session_import.writer import import_session

_ALL = "__all__"
_EVERYWHERE = "__everywhere__"
_LIST_KEYWORD = "list"
_ALL_KEYWORD = "all"
_EVERYWHERE_KEYWORD = "everywhere"
# KeyError/TypeError: another agent changed its file layout; the person is told which session failed.
_READ_ERRORS = (OSError, sqlite3.Error, ValueError, KeyError, TypeError, JsonStorageError)


def _bi(en: str, ru: str) -> str:
    return f"{en} / {ru}"


@dataclass(slots=True)
class ExportOutcome:
    ok: bool
    message: str
    effects: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _Found:
    source: Source
    sessions: list[SessionSummary]
    unreadable: str = ""


def _discover(home: Path, workspace: Path) -> list[_Found]:
    found: list[_Found] = []
    for source in SOURCES:
        try:
            found.append(_Found(source, find_sessions(source, home, workspace)))
        except SourceUnreadable as error:
            found.append(_Found(source, [], str(error)))
    return found


def _describe_counts(found: list[_Found], workspace: Path) -> str:
    rows = []
    for item in found:
        if item.unreadable:
            rows.append(f"- {item.unreadable}")
        elif item.sessions:
            rows.append(
                f"- {item.source.label}: {len(here(item.sessions, workspace))} "
                + _bi("here", "здесь")
                + f", {len(item.sessions)} " + _bi("in total", "всего")
            )
    return "\n".join(rows)


def _session_option(summary: SessionSummary) -> dict[str, str]:
    stamp = datetime.fromtimestamp(summary.updated_at).strftime("%Y-%m-%d %H:%M") if summary.updated_at else "—"
    return {"value": summary.id, "label": f"{stamp}  {summary.title}", "description": summary.cwd}


def _choose_source(ui: Any, found: list[_Found], workspace: Path) -> Source | str | None:
    options = [
        {
            "value": item.source.key,
            "label": item.source.label,
            "description": f"{len(here(item.sessions, workspace))} "
            + _bi("here", "здесь")
            + f", {len(item.sessions)} " + _bi("in total", "всего"),
        }
        for item in found
        if item.sessions
    ]
    options.append({"value": _ALL, "label": _bi("All agents", "Все агенты"), "description": str(workspace)})
    chosen = ui.choose_option(
        title=_bi("Import sessions from", "Импорт сессий из"),
        options=options,
        help_text=_bi("Type to filter, Enter — choose, Esc — cancel", "Печатайте для поиска, Enter — выбрать, Esc — отмена"),
    )
    if chosen is None or chosen == _ALL:
        return chosen
    return source_by_key(chosen)


def _choose_sessions(ui: Any, item: _Found, workspace: Path, everywhere: bool) -> list[SessionSummary] | None:
    local = here(item.sessions, workspace)
    pool = item.sessions if everywhere or not local else local
    while True:
        options = [_session_option(summary) for summary in pool]
        if pool is local:
            options.insert(0, {"value": _ALL, "label": _bi(f"All {len(local)} from this folder", f"Все {len(local)} из этой папки"), "description": ""})
            if len(item.sessions) > len(local):
                options.append({"value": _EVERYWHERE, "label": _bi("Other folders…", "Другие папки…"), "description": str(len(item.sessions) - len(local))})
        chosen = ui.choose_option(
            title=_bi(f"{item.source.label}: choose a session", f"{item.source.label}: выберите сессию"),
            options=options,
            help_text=_bi("Type to filter, Enter — import, Esc — cancel", "Печатайте для поиска, Enter — импорт, Esc — отмена"),
        )
        if chosen is None:
            return None
        if chosen == _EVERYWHERE:
            pool = item.sessions
            continue
        if chosen == _ALL:
            return local
        return [summary for summary in pool if summary.id == chosen]


def _import_all(agent: Any, picked: list[tuple[Source, SessionSummary]]) -> ExportOutcome:
    imported: list[dict[str, Any]] = []
    existing = empty = 0
    failed: list[str] = []
    for source, summary in picked:
        try:
            session = source.load(summary)
            if not any(turn.user for turn in session.turns):
                empty += 1
                continue
            result = import_session(agent, source, session)
        except _READ_ERRORS as error:
            failed.append(f"{source.label} {summary.id}: {error}")
            continue
        if result is None:
            existing += 1
            continue
        imported.append(
            {"session": result.name, "source": source.key, "title": session.summary.title, "turns": result.turns, "turns_in_context": result.turns_in_context}
        )
    effects = {"imported": imported, "already_imported": existing, "empty": empty, "failed": failed}
    return ExportOutcome(bool(imported) or not failed, _report(imported, existing, empty, failed), effects)


def _report(imported: list[dict[str, Any]], existing: int, empty: int, failed: list[str]) -> str:
    lines = []
    if imported:
        lines.append(_bi(f"Imported {len(imported)} session(s):", f"Импортировано сессий: {len(imported)}:"))
        lines.extend(f"- {item['session']}  ({item['turns']} " + _bi("requests", "запросов") + f")  {item['title']}" for item in imported)
        lines.append(_bi("Open one with /session.", "Откройте нужную через /session."))
    if existing:
        lines.append(_bi(f"Already imported earlier: {existing}.", f"Уже импортировано раньше: {existing}."))
    if empty:
        lines.append(_bi(f"Without requests, skipped: {empty}.", f"Без запросов, пропущено: {empty}."))
    lines.extend(_bi("Failed: ", "Не удалось: ") + item for item in failed)
    return "\n".join(lines) or _bi("Nothing to import.", "Импортировать нечего.")


def _select(
    found: list[_Found], words: list[str], workspace: Path, everywhere: bool
) -> list[tuple[Source, SessionSummary]] | str:
    """Sessions named by the arguments, or an error text."""
    wanted = found if words[0] == _ALL_KEYWORD else [item for item in found if item.source.key == words[0]]
    if not wanted:
        known = ", ".join(source.key for source in SOURCES)
        return _bi(f"Unknown agent {words[0]!r}. Known: {known}.", f"Неизвестный агент {words[0]!r}. Известны: {known}.")
    if len(words) > 1 and words[1] != _ALL_KEYWORD:
        return _bi("Usage: /export [list | <agent> [all] | all] [everywhere]", "Использование: /export [list | <агент> [all] | all] [everywhere]")
    return [
        (item.source, summary)
        for item in wanted
        for summary in (item.sessions if everywhere else here(item.sessions, workspace))
    ]


def run_export(agent: Any, ui: Any, args: list[str]) -> ExportOutcome | None:
    """Run ``/export``; None when the person cancelled a dialog."""
    workspace = Path(agent.executor.workspace)
    found = _discover(Path.home(), workspace)
    tokens = [argument.casefold() for argument in args]
    everywhere = _EVERYWHERE_KEYWORD in tokens
    words = [token for token in tokens if token != _EVERYWHERE_KEYWORD]
    if words[:1] == [_LIST_KEYWORD]:
        return ExportOutcome(True, _describe_counts(found, workspace) or _bi("No sessions of other agents were found.", "Сессий других агентов не найдено."))
    if not any(item.sessions for item in found):
        return ExportOutcome(False, _bi("No sessions of other agents were found.", "Сессий других агентов не найдено.") + "\n" + _describe_counts(found, workspace))
    if words:
        picked = _select(found, words, workspace, everywhere)
        if isinstance(picked, str):
            return ExportOutcome(False, picked)
        return _import_all(agent, picked)
    choice = _choose_source(ui, found, workspace)
    if choice is None:
        return None
    if choice == _ALL:
        return _import_all(agent, [(i.source, s) for i in found for s in (i.sessions if everywhere else here(i.sessions, workspace))])
    item = next(item for item in found if item.source is choice)
    sessions = _choose_sessions(ui, item, workspace, everywhere)
    if sessions is None:
        return None
    return _import_all(agent, [(item.source, summary) for summary in sessions])
