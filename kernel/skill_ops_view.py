# -*- coding: utf-8 -*-
"""``kvist --skill-op`` for a person at a terminal: tables and short lines instead of the JSON object.

Programs (frontends, scripts) read the JSON object, so ``skill_ops.main_skill_op``
uses this module only when stdout is a terminal and ``--json`` is not given.
"""
from __future__ import annotations

from typing import Any, Callable

from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

_WORDS = {
    "en": {
        "kind": "Kind", "status": "Status", "rights": "Permissions",
        "tools": "Tools", "ready": "ready", "instructions": "instructions", "tools_kind": "tools",
        "approved": "approved here", "asks": "asks on first use", "summary": "{ready} of {total} ready",
        "no_skills": "No skills are installed.", "installed": "installed", "already": "already installed, same content",
        "active": "active", "approved_done": "approved for this workspace", "revoked": "permissions revoked",
        "revoked_none": "there was nothing to revoke", "uninstalled": "uninstalled", "rolled_back": "rolled back to",
        "removed_version": "removed version", "data_removed": "data removed", "data_none": "no data to remove",
        "deps_done": "dependencies installed", "versions": "Versions", "check_ok": "passed", "check_bad": "failed",
        "error": "Error", "permissions": "Permissions", "similar": "Similar installed skills",
        "author_tests": "Author's tests",
    },
    "ru": {
        "kind": "Вид", "status": "Состояние", "rights": "Права",
        "tools": "Инструменты", "ready": "готов", "instructions": "инструкции", "tools_kind": "инструменты",
        "approved": "подтверждён здесь", "asks": "спросит при первом использовании",
        "summary": "Готово {ready} из {total}", "no_skills": "Скиллов не установлено.",
        "installed": "установлен", "already": "уже установлен, содержимое то же", "active": "активна",
        "approved_done": "подтверждён для этого проекта", "revoked": "права отозваны",
        "revoked_none": "отзывать было нечего", "uninstalled": "удалён", "rolled_back": "откат на версию",
        "removed_version": "удалена версия", "data_removed": "данные удалены", "data_none": "данных нет",
        "deps_done": "зависимости установлены", "versions": "Версии", "check_ok": "пройдена",
        "check_bad": "не пройдена", "error": "Ошибка", "permissions": "Права", "similar": "Похожие скиллы",
        "author_tests": "Тесты автора",
    },
}
_TOOLS_SHOWN = 4


def render(op: str, payload: dict[str, Any], console: Console, language: str = "en") -> None:
    words = _WORDS.get(language, _WORDS["en"])
    if payload.get("error"):
        console.print(Text.assemble(("✗ ", "bold red"), f"{words['error']}: ", (str(payload["error"]), "red")))
        return
    view = _VIEWS.get(op)
    if view is None:
        console.print_json(data=payload)
        return
    view(payload, console, words)


def _kind(row: dict[str, Any], words: dict[str, str]) -> str:
    return words["instructions"] if row.get("kind") == "instructions" else words["tools_kind"]


def _status(row: dict[str, Any], words: dict[str, str]) -> Text:
    if row.get("available"):
        return Text(f"● {words['ready']}", style="green")
    return Text(f"○ {row.get('reason') or '?'}", style="yellow", overflow="fold")


def _rights(row: dict[str, Any], words: dict[str, str]) -> Text:
    if row.get("kind") == "instructions" or not row.get("capabilities"):
        return Text("—", style="dim")
    if row.get("approved_here"):
        return Text(f"✓ {words['approved']}", style="green")
    return Text(words["asks"], style="dim")


def _tool_names(row: dict[str, Any]) -> str:
    names = list(row.get("tools") or [])
    if not names:
        return "—"
    shown = ", ".join(names[:_TOOLS_SHOWN])
    return shown + (f" +{len(names) - _TOOLS_SHOWN}" if len(names) > _TOOLS_SHOWN else "")


def _list(payload: dict[str, Any], console: Console, words: dict[str, str]) -> None:
    rows = payload.get("skills") or []
    if not rows:
        console.print(words["no_skills"])
        return
    for row in rows:
        mark = Text("● ", style="green") if row.get("available") else Text("○ ", style="yellow")
        head = Text.assemble(mark, (str(row.get("id")), "bold"), f"  {row.get('version') or ''}  ",
                             (_kind(row, words), "dim"), ("  ·  ", "dim"), _rights(row, words))
        console.print(head)
        for line in _card_lines(row, words):
            console.print(Padding(line, (0, 0, 0, 2)))
        console.print()
    ready = sum(1 for row in rows if row.get("available"))
    console.print(Text(words["summary"].format(ready=ready, total=len(rows)), style="dim"))


def _card_lines(row: dict[str, Any], words: dict[str, str]) -> list[Text]:
    lines = []
    if row.get("summary"):
        lines.append(Text(str(row["summary"]), style="dim"))
    if row.get("tools"):
        lines.append(Text.assemble((f"{words['tools']}: ", "dim"), _tool_names(row)))
    if not row.get("available"):
        lines.append(Text(f"→ {row.get('reason') or '?'}", style="yellow"))
    return lines


def _install(payload: dict[str, Any], console: Console, words: dict[str, str]) -> None:
    state = words["installed"] if payload.get("created") else words["already"]
    console.print(Text.assemble(("✓ ", "bold green"), (f"{payload.get('installed')} {payload.get('version')}", "bold"),
                                f" — {state}, {words['active']} {payload.get('active_version')}"))
    if payload.get("available") is False and payload.get("reason"):
        console.print(Text(f"  ○ {payload['reason']}", style="yellow"))
    if payload.get("note"):
        console.print(Text(f"  {payload['note']}", style="dim"))


def _show(payload: dict[str, Any], console: Console, words: dict[str, str]) -> None:
    row = payload.get("skill") or {}
    body = Text()
    if row.get("summary"):
        body.append(f"{row['summary']}\n\n")
    body.append(f"{words['kind']}: ", style="bold").append(f"{_kind(row, words)}\n")
    body.append(f"{words['status']}: ", style="bold").append(_status(row, words)).append("\n")
    body.append(f"{words['rights']}: ", style="bold").append(_rights(row, words)).append("\n")
    if row.get("capabilities"):
        body.append(f"{words['permissions']}: ", style="bold").append(", ".join(row["capabilities"]) + "\n")
    if row.get("tools"):
        body.append(f"{words['tools']}: ", style="bold").append(", ".join(row["tools"]) + "\n")
    if row.get("versions"):
        body.append(f"{words['versions']}: ", style="bold").append(", ".join(map(str, row["versions"])) + "\n")
    if row.get("approval_text"):
        body.append("\n").append(str(row["approval_text"]), style="dim")
    console.print(Panel(body, title=f"{row.get('name') or row.get('id')} {row.get('version')}", title_align="left",
                        border_style="cyan", expand=False))


def _done(message: Callable[[dict[str, Any], dict[str, str]], str]) -> Callable[..., None]:
    def view(payload: dict[str, Any], console: Console, words: dict[str, str]) -> None:
        console.print(Text.assemble(("✓ ", "bold green"), message(payload, words)))

    return view


def _approve(payload: dict[str, Any], console: Console, words: dict[str, str]) -> None:
    console.print(Text.assemble(("✓ ", "bold green"), (f"{payload.get('approved')} {payload.get('version')}", "bold"),
                                f" — {words['approved_done']}"))
    if payload.get("capabilities"):
        console.print(Text("  " + ", ".join(payload["capabilities"]), style="dim"))


def _check(payload: dict[str, Any], console: Console, words: dict[str, str]) -> None:
    report = payload.get("report") or {}
    headline = Text.assemble(("✓ " if payload.get("ok") else "✗ ", "bold green" if payload.get("ok") else "bold red"),
                             (f"{report.get('skill')} {report.get('version')}", "bold"),
                             f" — {words['check_ok'] if payload.get('ok') else words['check_bad']}")
    console.print(headline)
    for finding in report.get("findings") or []:
        if finding.get("ok") and not finding.get("detail"):
            continue
        mark, style = ("✓", "green") if finding.get("ok") else \
            (("!", "yellow") if finding.get("severity") == "warning" else ("✗", "red"))
        console.print(Text.assemble((f"  {mark} ", style), (str(finding.get("check")), "bold"),
                                    f"  {finding.get('detail') or ''}".rstrip()))
    tests = report.get("author_tests") or {}
    if tests:
        console.print(Text(f"  {words['author_tests']}: {tests.get('status')}", style="dim"))
    if report.get("similar_installed"):
        console.print(Text(f"  {words['similar']}: {', '.join(report['similar_installed'])}", style="yellow"))


_VIEWS: dict[str, Callable[..., None]] = {
    "list": _list,
    "show": _show,
    "install": _install,
    "approve": _approve,
    "check": _check,
    "revoke": _done(lambda p, w: w["revoked"] if p.get("revoked") else w["revoked_none"]),
    "uninstall": _done(lambda p, w: f"{p.get('uninstalled')} — {w['uninstalled']}"),
    "rollback": _done(lambda p, w: f"{w['rolled_back']} {p.get('active_version')}"),
    "remove-version": _done(lambda p, w: f"{w['removed_version']} {p.get('removed_version')}"),
    "reset-data": _done(lambda p, w: w["data_removed"] if p.get("removed") else w["data_none"]),
    "install-deps": _done(lambda p, w: f"{w['deps_done']}: {', '.join(p.get('pins') or [])}"),
}
