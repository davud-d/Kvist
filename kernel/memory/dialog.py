# -*- coding: utf-8 -*-
"""``/memory`` with no arguments: a dialog to look into everything Kvist remembers.

It is built from the ``choose_option`` / ``prompt_text`` dialogs every frontend
already renders, so the classic terminal and every ``stdio`` frontend get it
without a screen of their own. The person is the actor of every change made
here, exactly as with the text commands of ``commands.py``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.config.user_config import save_memory_paused
from kernel.memory.commands import (
    _PAUSE_TEXT,
    _date,
    _forgotten_text,
    _kind_text,
    _process,
    _set_processing,
    _source_text,
    _status_text,
    explain_text,
)
from kernel.memory.errors import MemoryStoreError
from kernel.memory.extractor import ModelAccess
from kernel.memory.schema import KINDS, RECORD_KINDS
from kernel.memory.service import MemoryService
from kernel.memory.store import PERSON

_LIST_LIMIT = 2000
_CARD_ITEMS = 3
_BACKGROUND_AUTHOR = "extractor"

# name -> (predicate, title ru, title en, description ru, description en)
_FILTERS: dict[str, tuple[Callable[[dict[str, Any]], bool], str, str, str, str]] = {
    "all": (lambda r: True, "Все записи", "All records", "всё, что Kvist помнит", "everything Kvist remembers"),
    "workspace": (lambda r: r["scope"] == "workspace", "Только этот проект", "This project only",
                  "записи, которые видны только здесь", "records visible in this project only"),
    "user": (lambda r: r["scope"] == "user", "Для всех проектов", "Every project",
             "ваши предпочтения везде", "your preferences everywhere"),
    "background": (lambda r: r["kind"] == "session_recap" or r["author"] == _BACKGROUND_AUTHOR,
                   "Фоновая память", "Background memory",
                   "итоги сессий и то, что Kvist выделил сам", "session recaps and what Kvist picked out itself"),
    "check": (lambda r: r["status"] in ("stale", "conflicted"), "Требуют проверки", "Need a check",
              "устаревшие и противоречащие друг другу", "stale or contradicting each other"),
}

_KIND_NAMES = {
    "project_fact": ("факт о проекте", "project fact"),
    "decision": ("решение", "decision"),
    "preference": ("предпочтение", "preference"),
}
_TRUST_NAMES = {
    "person": ("вы сами", "you yourself"),
    "observed": ("замечено в работе", "observed while working"),
    "inferred": ("выведено из разговора", "inferred from a conversation"),
}
_AUTHOR_NAMES = {
    "person": ("вы", "you"),
    "agent": ("агент во время задачи", "the agent during a task"),
    _BACKGROUND_AUTHOR: ("фоновое чтение разговоров", "background reading of conversations"),
}
_EDIT_REASON = "edited by the person in /memory"
_CONFIRM_REASON = "confirmed current by the person in /memory"


def _pick(ru: bool, russian: str, english: str) -> str:
    return russian if ru else english


def _join(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part)


def _one_line(text: str, width: int = 80) -> str:
    line = " ".join(text.split())
    return line if len(line) <= width else line[: width - 1] + "…"


def run_dialog(workspace: Path, ui: Any, confirm: Callable[[str, str], bool], ru: bool, *,
               model: ModelAccess | None, session: str) -> str:
    """Run the dialog until the person leaves it; returns text to print afterwards, or ``""``."""
    dialog = MemoryDialog(MemoryService(workspace, with_user_scope=True), workspace, ui, confirm, ru, model, session)
    return dialog.run()


@dataclass(slots=True)
class MemoryDialog:
    memory: MemoryService
    workspace: Path
    ui: Any
    confirm: Callable[[str, str], bool]
    ru: bool
    model: ModelAccess | None
    session: str

    def run(self) -> str:
        notice = ""
        while True:
            status = self.memory.status()
            choice = self.ui.choose_option(
                _pick(self.ru, "Память Kvist", "Kvist memory"),
                self._main_options(status),
                help_text=_join(notice, _status_text(status, self.ru)),
            )
            if choice is None:
                return ""
            try:
                notice, final = self._main_action(choice, status)
            except MemoryStoreError as error:
                notice, final = str(error), ""
            if final:
                return final

    # --- main menu ------------------------------------------------------

    def _main_options(self, status: dict[str, Any]) -> list[dict[str, str]]:
        records = self._all_records()
        options = []
        for name, (matches, title_ru, title_en, desc_ru, desc_en) in _FILTERS.items():
            count = sum(1 for record in records if matches(record))
            options.append({"value": name, "label": f"{_pick(self.ru, title_ru, title_en)} ({count})",
                            "description": _pick(self.ru, desc_ru, desc_en)})
        options.append({"value": "search", "label": _pick(self.ru, "Найти по словам…", "Search by words…"),
                        "description": ""})
        options.append({"value": "add", "label": _pick(self.ru, "Запомнить самому…", "Remember something…"),
                        "description": _pick(self.ru, "запись, которую вы подтверждаете сами", "a record you state yourself")})
        paused = status["paused"]
        options.append({
            "value": "resume" if paused else "pause",
            "label": _pick(self.ru, "Включить запись в память", "Turn writing back on") if paused
            else _pick(self.ru, "Поставить запись на паузу", "Pause writing"),
            "description": _pick(self.ru, "агенты и скиллы не пишут, пока на паузе", "agents and skills write nothing while paused"),
        })
        allowed = status["processing_allowed"]
        options.append({
            "value": "processing",
            "label": _pick(self.ru, "Выключить разбор разговоров", "Turn conversation processing off") if allowed
            else _pick(self.ru, "Разрешить разбор разговоров", "Allow conversation processing"),
            "description": _pick(self.ru, "фоновое чтение сессий моделью", "background reading of sessions by the model"),
        })
        if self.model is not None and self.session:
            options.append({"value": "process", "label": _pick(self.ru, "Разобрать эту сессию сейчас", "Read this session now"),
                            "description": self.session})
        return options

    def _main_action(self, choice: str, status: dict[str, Any]) -> tuple[str, str]:
        if choice in _FILTERS:
            matches = _FILTERS[choice][0]
            title = _pick(self.ru, _FILTERS[choice][1], _FILTERS[choice][2])
            return self._list(title, lambda: [r for r in self._all_records() if matches(r)])
        if choice == "search":
            return self._search()
        if choice == "add":
            return self._add(), ""
        if choice in ("pause", "resume"):
            save_memory_paused(choice == "pause")
            return _PAUSE_TEXT[(choice, self.ru)], ""
        if choice == "processing":
            return _set_processing(not status["processing_allowed"], self.model, self.confirm, self.ru)[1], ""
        if choice == "process":
            return _process(self.workspace, self.session, self.model, self.confirm, self.ru)[1], ""
        return "", ""

    def _all_records(self) -> list[dict[str, Any]]:
        return self.memory.search("", kinds=RECORD_KINDS, include_inactive=True, limit=_LIST_LIMIT)

    # --- lists ----------------------------------------------------------

    def _search(self) -> tuple[str, str]:
        words = self.ui.prompt_text(_pick(self.ru, "Что искать в памяти?", "What to look for in memory?"))
        if words is None or not words.strip():
            return "", ""
        title = _pick(self.ru, f"Поиск: {words.strip()}", f"Search: {words.strip()}")
        return self._list(title, lambda: self.memory.search(words, kinds=RECORD_KINDS, include_inactive=True,
                                                            limit=_LIST_LIMIT))

    def _list(self, title: str, fetch: Callable[[], list[dict[str, Any]]]) -> tuple[str, str]:
        notice = ""
        while True:
            records = fetch()
            if not records:
                return notice or _pick(self.ru, "Записей нет.", "No records."), ""
            picked = self.ui.choose_option(
                f"{title} ({len(records)})",
                [self._row(record) for record in records],
                help_text=_join(notice, _pick(self.ru, "Enter — открыть запись, Esc — назад", "Enter — open a record, Esc — back")),
            )
            if picked is None:
                return "", ""
            notice, final = self._card(picked)
            if final:
                return notice, final

    def _row(self, record: dict[str, Any]) -> dict[str, str]:
        parts = [_date(record["updated_at"]), self._where(record, short=True), self._who(record, short=True)]
        if record["status"] != "active":
            parts.append(record["status"])
        label = _one_line(record["content"]) if record["kind"] != "session_recap" else _one_line(
            f"{_kind_text(record, self.ru)}: {record['content']}")
        return {"value": record["id"], "label": label, "description": " · ".join(parts)}

    # --- one record -----------------------------------------------------

    def _card(self, record_id: str) -> tuple[str, str]:
        explanation = self.memory.explain(record_id)
        record = explanation["record"]
        action = self.ui.choose_option(
            f"{self._kind(record)} · {record_id}",
            self._card_options(record),
            help_text=self._card_text(explanation),
        )
        if action is None or action == "back":
            return "", ""
        if action == "full":
            return "", explain_text(explanation, self.ru)
        try:
            return self._card_action(action, record), ""
        except MemoryStoreError as error:
            return str(error), ""

    def _card_options(self, record: dict[str, Any]) -> list[dict[str, str]]:
        options = [{"value": "full", "label": _pick(self.ru, "Показать полностью", "Show everything"),
                    "description": _pick(self.ru, "все источники, связи и история", "every source, link and history entry")}]
        if record["kind"] != "session_recap":
            options.append({"value": "edit", "label": _pick(self.ru, "Изменить текст…", "Edit the text…"),
                            "description": _pick(self.ru, "запись станет подтверждённой вами", "the record becomes yours")})
            stale = record["status"] == "stale"
            options.append({
                "value": "active" if stale else "stale",
                "label": _pick(self.ru, "Снова актуально", "Current again") if stale else _pick(self.ru, "Отметить устаревшей…", "Mark as stale…"),
                "description": "",
            })
        options.append({"value": "forget", "label": _pick(self.ru, "Забыть навсегда…", "Forget for good…"),
                        "description": _pick(self.ru, "удаляет запись и её историю", "deletes the record and its history")})
        options.append({"value": "back", "label": _pick(self.ru, "Назад", "Back"), "description": ""})
        return options

    def _card_action(self, action: str, record: dict[str, Any]) -> str:
        if action == "edit":
            return self._edit(record)
        if action in ("stale", "active"):
            return self._set_status(record, action)
        if action == "forget":
            return self._forget(record)
        return ""

    def _edit(self, record: dict[str, Any]) -> str:
        text = self.ui.prompt_text(_pick(self.ru, "Новый текст записи:", "New text of the record:"),
                                   current_value=record["content"])
        if text is None or not text.strip() or text.strip() == record["content"]:
            return ""
        self.memory.update(record["id"], actor=PERSON, expected_version=record["version"], reason=_EDIT_REASON,
                           content=text.strip())
        return _pick(self.ru, f"Запись {record['id']} изменена.", f"{record['id']} updated.")

    def _set_status(self, record: dict[str, Any], status: str) -> str:
        reason = _CONFIRM_REASON
        if status == "stale":
            asked = self.ui.prompt_text(_pick(self.ru, "Почему запись устарела?", "Why is the record stale?"))
            if asked is None or not asked.strip():
                return ""
            reason = asked.strip()
        self.memory.update(record["id"], actor=PERSON, expected_version=record["version"], reason=reason, status=status)
        return _pick(self.ru, f"Запись {record['id']}: {status}.", f"{record['id']} is now {status}.")

    def _forget(self, record: dict[str, Any]) -> str:
        title = _pick(self.ru, "Забыть эту запись навсегда?", "Forget this record for good?")
        if not self.confirm(title, record["content"]):
            return _pick(self.ru, "Ничего не изменилось.", "Nothing changed.")
        return _forgotten_text(self.memory.forget(record["id"], actor=PERSON), self.ru)

    def _add(self) -> str:
        scope = self.ui.choose_option(
            _pick(self.ru, "Где помнить?", "Where to remember it?"),
            [{"value": "workspace", "label": _pick(self.ru, "Только в этом проекте", "In this project only"), "description": ""},
             {"value": "user", "label": _pick(self.ru, "Во всех проектах", "In every project"),
              "description": _pick(self.ru, "только предпочтения", "preferences only")}])
        if scope is None:
            return ""
        kind = "preference" if scope == "user" else self._ask_kind()
        if kind is None:
            return ""
        text = self.ui.prompt_text(_pick(self.ru, "Что запомнить?", "What to remember?"))
        if text is None or not text.strip():
            return ""
        result = self.memory.remember(text.strip(), kind=kind, trust=PERSON, actor=PERSON, scope=scope)
        verb = _pick(self.ru, "Сохранено", "Remembered") if result["result"] == "accepted" else _pick(
            self.ru, "Уже было сохранено", "Already remembered")
        return f"{verb}: {result['record']['id']}"

    def _ask_kind(self) -> str | None:
        return self.ui.choose_option(
            _pick(self.ru, "Что это?", "What is it?"),
            [{"value": kind, "label": _pick(self.ru, *_KIND_NAMES[kind]), "description": ""} for kind in KINDS])

    # --- how a record is described -------------------------------------

    def _where(self, record: dict[str, Any], *, short: bool = False) -> str:
        if record["scope"] == "user":
            return _pick(self.ru, "везде", "everywhere") if short else _pick(
                self.ru, "везде — личная память, видна во всех проектах", "everywhere — personal memory, seen in every project")
        return _pick(self.ru, "этот проект", "this project") if short else _pick(
            self.ru, "только этот проект", "this project only")

    def _who(self, record: dict[str, Any], *, short: bool = False) -> str:
        author = record["author"]
        known = _AUTHOR_NAMES.get(author)
        name = _pick(self.ru, *known) if known else _pick(self.ru, f"скилл {author}", f"skill {author}")
        if short:
            return name
        return f"{name} · {_pick(self.ru, 'доверие', 'trust')}: {_pick(self.ru, *_TRUST_NAMES[record['trust']])}"

    def _card_text(self, explanation: dict[str, Any]) -> str:
        record = explanation["record"]
        kind = self._kind(record)
        status = record["status"] + (f" ({record['status_reason']})" if record["status_reason"] else "")
        lines = [record["content"], "",
                 f"{_pick(self.ru, 'Где', 'Where')}: {self._where(record)}",
                 f"{_pick(self.ru, 'Что это', 'Kind')}: {kind} · {_pick(self.ru, 'статус', 'status')}: {status}",
                 f"{_pick(self.ru, 'Кто записал', 'Written by')}: {self._who(record)}",
                 self._times(record)]
        if record["topic"]:
            lines.append(f"{_pick(self.ru, 'Тема', 'Topic')}: {record['topic']}")
        if record["files"]:
            lines.append(f"{_pick(self.ru, 'Файлы', 'Files')}: {', '.join(record['files'])}")
        lines += self._section(_pick(self.ru, "Откуда", "Sources"),
                               [_source_text(source, self.ru) for source in explanation["sources"]])
        lines += self._section(_pick(self.ru, "Связи", "Links"), [self._link_line(link) for link in explanation["relations"]])
        lines += self._section(_pick(self.ru, "История", "History"), [
            f"{_date(entry['at'])} {entry['actor']}: {entry['action']}" for entry in explanation["history"]], newest=True)
        return "\n".join(lines)

    def _kind(self, record: dict[str, Any]) -> str:
        if record["kind"] == "session_recap":
            return _kind_text(record, self.ru)
        return _pick(self.ru, *_KIND_NAMES[record["kind"]])

    def _times(self, record: dict[str, Any]) -> str:
        text = (f"{_pick(self.ru, 'Создана', 'Created')} {_date(record['created_at'])} · "
                f"{_pick(self.ru, 'изменена', 'changed')} {_date(record['updated_at'])} · v{record['version']}")
        if record["verified_at"]:
            text += f" · {_pick(self.ru, 'проверена', 'verified')} {_date(record['verified_at'])}"
        return text

    @staticmethod
    def _link_line(link: dict[str, Any]) -> str:
        return f"{link['source_id']} {link['relation']} {link['target_id']}"

    def _section(self, title: str, items: list[str], *, newest: bool = False) -> list[str]:
        if not items:
            return []
        shown = items[-_CARD_ITEMS:] if newest else items[:_CARD_ITEMS]
        more = len(items) - len(shown)
        lines = [f"{title}:"] + [f"  - {item}" for item in shown]
        if more:
            lines.append("  " + _pick(self.ru, f"…и ещё {more} («Показать полностью»)", f"…and {more} more (\"Show everything\")"))
        return lines
