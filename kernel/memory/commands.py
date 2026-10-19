# -*- coding: utf-8 -*-
"""Memory as the person manages it: ``--memory-op`` (JSON for frontends) and ``/memory`` (text).

The person is the one actor every rule lets through: they confirm records,
rewrite and forget what they confirmed, and pause what agents and skills
write. Forgetting still asks first, because it cannot be undone.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kernel.memory.errors import MemoryValidationError
from kernel.memory.extractor import ModelAccess, process_session
from kernel.memory.schema import STATUSES
from kernel.memory.service import MemoryService
from kernel.memory.store import PERSON, MemoryStore
from kernel.user_config import (
    get_memory_paused,
    get_memory_processing_allowed,
    save_memory_paused,
    save_memory_processing_allowed,
)

OPS = ("status", "list", "search", "get", "explain", "remember", "update", "forget", "link", "pause", "resume")
SLASH_USAGE = (
    "/memory [status] · inspect [words] · why <id> · forget <id> · remember <text> · remember-global <text> "
    "· process [session] · processing on|off · pause · resume"
)


def run_op(op: str, workspace: Path, payload: dict[str, Any]) -> dict[str, Any]:
    memory = MemoryService(workspace, with_user_scope=True)
    if op == "status":
        return {"ok": True, **memory.status()}
    if op in ("pause", "resume"):
        save_memory_paused(op == "pause")
        return {"ok": True, "paused": op == "pause"}
    if op == "list":
        return {"ok": True, "records": memory.search("", include_inactive=True, limit=int(payload.get("limit") or 200))}
    if op == "search":
        return {"ok": True, "records": memory.search(str(payload.get("query") or ""),
                                                     include_inactive=bool(payload.get("include_inactive")))}
    if op == "get":
        return {"ok": True, "record": memory.get(str(payload.get("id") or ""))}
    if op == "explain":
        return {"ok": True, **memory.explain(str(payload.get("id") or ""))}
    if op == "remember":
        return {"ok": True, **memory.remember(
            str(payload.get("content") or ""), kind=str(payload.get("kind") or "preference"), trust=PERSON,
            actor=PERSON, scope=str(payload.get("scope") or "workspace"), topic=str(payload.get("topic") or ""),
            files=payload.get("files"))}
    if op == "update":
        return {"ok": True, "record": memory.update(
            str(payload.get("id") or ""), actor=PERSON, expected_version=int(payload.get("expected_version") or 0),
            reason=str(payload.get("reason") or ""), content=payload.get("content"), topic=payload.get("topic"),
            files=payload.get("files"), kind=payload.get("kind"), status=payload.get("status"))}
    if op == "forget":
        return {"ok": True, **memory.forget(str(payload.get("id") or ""), actor=PERSON)}
    if op == "link":
        return {"ok": True, **memory.link(str(payload.get("source_id") or ""), str(payload.get("target_id") or ""),
                                          str(payload.get("relation") or ""), actor=PERSON,
                                          reason=str(payload.get("reason") or ""))}
    raise MemoryValidationError(f"unknown memory op {op!r}; one of {', '.join(OPS)}")


def run_slash(workspace: Path, args: list[str], confirm: Callable[[str, str], bool], ru: bool, *,
              model: ModelAccess | None = None, session: str = "") -> tuple[bool, str]:
    """``/memory …`` as text; ``confirm(title, details)`` asks the person before anything is forgotten or sent.

    ``model`` and ``session`` come from a running agent; without them ``process`` is unavailable.
    """
    command, rest = (args[0].lower(), " ".join(args[1:]).strip()) if args else ("status", "")
    if command == "process":
        return _process(workspace, rest or session, model, confirm, ru)
    if command == "processing" and rest in ("on", "off"):
        return _set_processing(rest == "on", model, confirm, ru)
    memory = MemoryService(workspace, with_user_scope=True)
    if command == "status":
        return True, _status_text(memory.status(), ru)
    if command in ("pause", "resume"):
        save_memory_paused(command == "pause")
        return True, _PAUSE_TEXT[(command, ru)]
    if command == "inspect":
        return True, _records_text(memory.search(rest, include_inactive=True, limit=30), ru)
    if command == "why" and rest:
        return True, explain_text(memory.explain(rest), ru)
    if command in ("remember", "remember-global") and rest:
        result = memory.remember(rest, kind="preference", trust=PERSON, actor=PERSON,
                                 scope="user" if command == "remember-global" else "workspace")
        verb = ("Сохранено" if ru else "Remembered") if result["result"] == "accepted" else (
            "Уже было сохранено" if ru else "Already remembered")
        return True, f"{verb}: {result['record']['id']} — {result['record']['content']}"
    if command == "forget" and rest:
        record = memory.get(rest)
        title = "Забыть эту запись навсегда?" if ru else "Forget this record for good?"
        if not confirm(title, record["content"]):
            return True, "Ничего не изменилось." if ru else "Nothing changed."
        result = memory.forget(rest, actor=PERSON)
        return True, _forgotten_text(result, ru)
    return False, ("Использование: " if ru else "Usage: ") + SLASH_USAGE


def _process(workspace: Path, session: str, model: ModelAccess | None, confirm: Callable[[str, str], bool],
             ru: bool) -> tuple[bool, str]:
    if model is None or not session:
        return False, ("Разбор доступен только в открытом Kvist." if ru else "Processing needs a running Kvist.")
    if get_memory_paused():
        return False, ("Память на паузе: /memory resume, и потом снова /memory process." if ru else
                       "Memory is paused: /memory resume, then /memory process again.")
    if not get_memory_processing_allowed():
        allowed, text = _set_processing(True, model, confirm, ru)
        if not allowed or not get_memory_processing_allowed():
            return allowed, text
    summary = process_session(MemoryStore.for_workspace(workspace), session, model)
    return not summary["problem"], _process_text(summary, ru)


def _set_processing(allowed: bool, model: ModelAccess | None, confirm: Callable[[str, str], bool],
                    ru: bool) -> tuple[bool, str]:
    if not allowed:
        save_memory_processing_allowed(False)
        return True, ("Разбор разговоров выключен: ничего из них больше не отправляется." if ru else
                      "Conversation processing is off: nothing from them is sent any more.")
    label = model.label if model else ("провайдер этого Kvist" if ru else "this Kvist's provider")
    title = ("Разрешить разбор разговоров для памяти?" if ru else "Allow conversations to be processed for memory?")
    details = (
        f"Kvist отправит {label} ваши сообщения, ответы и краткие итоги шагов из выбранной сессии, чтобы выбрать то, "
        "что стоит помнить. Строки, похожие на ключи или личные данные, вырезаются до отправки; мысли модели и "
        "вывод команд не отправляются. Отключить: /memory processing off." if ru else
        f"Kvist will send {label} your messages, its answers and one-line step summaries from the chosen session, "
        "to pick out what is worth remembering. Lines that look like keys or personal data are cut before sending; "
        "the model's thoughts and command output are not sent. Turn off with /memory processing off."
    )
    if not confirm(title, details):
        return True, "Ничего не изменилось." if ru else "Nothing changed."
    save_memory_processing_allowed(True)
    return True, ("Разбор разговоров разрешён." if ru else "Conversation processing is allowed.")


def _process_text(summary: dict[str, Any], ru: bool) -> str:
    if not summary["events"] and not summary["problem"]:
        return (f"В сессии {summary['session']} нет новых законченных ходов." if ru else
                f"No new finished turns in {summary['session']}.")
    lines = [(f"{summary['session']}: прочитано событий {summary['events'] - summary['left']}, "
              f"новых записей {len(summary['accepted'])}, совпало с уже известным {len(summary['merged'])}, "
              f"отклонено {len(summary['rejected'])}.") if ru else
             (f"{summary['session']}: {summary['events'] - summary['left']} events read, "
              f"{len(summary['accepted'])} new records, {len(summary['merged'])} matched known ones, "
              f"{len(summary['rejected'])} rejected.")]
    if summary["accepted"]:
        lines.append(("Новые: " if ru else "New: ") + ", ".join(summary["accepted"])
                     + (" (/memory why <id>)"))
    for item in summary["rejected"][:5]:
        lines.append(f"- {'отклонено' if ru else 'rejected'}: {item['content'][:100]} — {item['reason']}")
    if summary["problem"]:
        lines.append(("Остановлено: " if ru else "Stopped: ") + summary["problem"])
    if summary["left"]:
        lines.append((f"Осталось событий: {summary['left']} — запустите /memory process ещё раз." if ru else
                      f"{summary['left']} events left — run /memory process again."))
    return "\n".join(lines)


_PAUSE_TEXT = {
    ("pause", True): "Память на паузе: агенты и скиллы ничего не записывают, пока вы не скажете /memory resume. "
                     "Уже сохранённое по-прежнему подсказывается задачам.",
    ("pause", False): "Memory is paused: agents and skills write nothing until /memory resume. "
                      "What is already kept is still offered to tasks.",
    ("resume", True): "Память снова записывается.",
    ("resume", False): "Memory is written again.",
}


def _status_text(status: dict[str, Any], ru: bool) -> str:
    lines = [("Память: на паузе" if status["paused"] else "Память: включена") if ru else
             ("Memory: paused" if status["paused"] else "Memory: on")]
    allowed = status["processing_allowed"]
    lines.append(("Разбор разговоров: " + ("разрешён" if allowed else "не разрешён (/memory process спросит)"))
                 if ru else
                 ("Conversation processing: " + ("allowed" if allowed else "not allowed (/memory process asks)")))
    names = {"workspace": ("этот проект", "this project"), "user": ("все проекты", "every project")}
    for scope, counts in status["stores"].items():
        parts = [f"{counts['by_status'].get(state, 0)} {state}" for state in STATUSES if counts["by_status"].get(state)]
        lines.append(f"- {names[scope][0 if ru else 1]}: {counts['total']}" + (f" ({', '.join(parts)})" if parts else ""))
    return "\n".join(lines)


def _records_text(records: list[dict[str, Any]], ru: bool) -> str:
    if not records:
        return "Записей нет." if ru else "No records."
    return "\n".join(
        f"{record['id']}  {record['kind']} · {record['trust']} · {record['status']} · v{record['version']}"
        f" · {_date(record['updated_at'])}\n    {record['content']}"
        for record in records
    )


def explain_text(explanation: dict[str, Any], ru: bool) -> str:
    record = explanation["record"]
    lines = [f"{record['id']}: {record['content']}",
             f"{record['kind']} · {record['trust']} · {record['status']}"
             + (f" ({record['status_reason']})" if record["status_reason"] else "") + f" · v{record['version']}"]
    lines.append("Источники:" if ru else "Sources:")
    for source in explanation["sources"]:
        lines.append("  - " + _source_text(source, ru))
    if explanation["relations"]:
        lines.append("Связи:" if ru else "Links:")
        for relation in explanation["relations"]:
            other = relation["other"] or {}
            lines.append(f"  - {relation['source_id']} {relation['relation']} {relation['target_id']}"
                         + (f" ({relation['reason']})" if relation["reason"] else "")
                         + (f" — {other.get('content', '')[:120]}" if other else ""))
    lines.append("История:" if ru else "History:")
    for entry in explanation["history"]:
        lines.append(f"  - {_date(entry['at'])} {entry['actor']}: {entry['action']} {entry['detail']}".rstrip())
    return "\n".join(lines)


def _source_text(source: dict[str, Any], ru: bool) -> str:
    where = source["origin"]
    if source["session"]:
        where += f", {'сессия' if ru else 'session'} {source['session']}"
    if source["event_seq"] is not None:
        where += f", {'событие' if ru else 'event'} {source['event_seq']}"
    if source["available"] is False:
        where += " — " + ("событие больше недоступно" if ru else "that event is no longer available")
    return f"{_date(source['added_at'])} {where}"


def _forgotten_text(result: dict[str, Any], ru: bool) -> str:
    text = (f"Запись {result['forgotten']} забыта" if ru else f"Forgot {result['forgotten']}")
    if result["now_stale"]:
        text += (". Требуют перепроверки: " if ru else ". To check again: ") + ", ".join(result["now_stale"])
    if not result["log_cleared"]:
        text += (". Журнал базы ещё держит другой процесс Kvist; текст исчезнет из него при следующей записи."
                 if ru else ". Another Kvist process still holds the database log; the text leaves it on the next write.")
    return text + "."


def _date(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
