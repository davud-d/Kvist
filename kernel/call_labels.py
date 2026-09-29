# -*- coding: utf-8 -*-
"""What a tool call does, in words the person reads, built from its arguments.

The model no longer writes a line per action: the core knows what each call
touches, so it says it itself ("читает src/x.py", 'ищет "role" в src/').
Only run_shell carries a model-written purpose, because an argv alone rarely
says why a command runs.
"""
from __future__ import annotations

from typing import Any

MAX_PURPOSE_WORDS = 10

# (russian, english) per action; {target} is the thing the call touches.
_TEMPLATES: dict[str, tuple[str, str]] = {
    "read_file": ("читает {target}", "reads {target}"),
    "read_multiple_files": ("читает {target}", "reads {target}"),
    "write_file": ("пишет {target}", "writes {target}"),
    "append_file": ("дописывает {target}", "appends to {target}"),
    "replace_in_file": ("правит {target}", "edits {target}"),
    "list_dir": ("смотрит {target}", "lists {target}"),
    "list_tree": ("смотрит дерево {target}", "lists the tree of {target}"),
    "find_files": ("ищет файлы {target}", "finds files {target}"),
    "search_code": ("ищет {target}", "searches for {target}"),
    "git_status": ("смотрит git status", "checks git status"),
    "git_diff": ("смотрит git diff {target}", "checks git diff {target}"),
    "run_shell": ("запускает {target}", "runs {target}"),
    "web_search": ("ищет в сети {target}", "searches the web for {target}"),
    "fetch_url": ("открывает {target}", "opens {target}"),
    "delegate_task": ("передаёт задачу роли {target}", "hands the task to {target}"),
    "refuse": ("отказывается", "refuses"),
    "final_answer": ("отвечает", "answers"),
    "report_to_user": ("готовит отчёт", "writes the report"),
    "offer_plan": ("предлагает сначала показать план", "offers to show a plan first"),
    "propose_plan": ("предлагает план", "proposes a plan"),
}

_IN = {"ru": " в ", "en": " in "}


def trim_purpose(purpose: Any) -> str:
    """The first ten words of a shell call's purpose; longer is cut, never refused."""
    if not isinstance(purpose, str):
        return ""
    return " ".join(purpose.split()[:MAX_PURPOSE_WORDS])


def call_label(action_type: str, args: dict[str, Any], language: str = "en", purpose: str = "") -> str:
    if action_type == "run_shell" and purpose:
        return purpose
    ru, en = _TEMPLATES.get(action_type, ("{target}", "{target}"))
    template = ru if language == "ru" else en
    return template.format(target=_target(action_type, args, language)).strip()


def _target(action_type: str, args: dict[str, Any], language: str) -> str:
    where = _IN.get(language, _IN["en"])
    path = _text(args.get("path"))
    if action_type == "search_code":
        return f'"{_text(args.get("query"))}"' + (f"{where}{path}" if path and path != "." else "")
    if action_type == "find_files":
        return _text(args.get("pattern")) + (f"{where}{path}" if path and path != "." else "")
    if action_type == "read_multiple_files":
        return ", ".join(_text(item) for item in args.get("paths") or [])
    if action_type == "run_shell":
        return " ".join(_text(part) for part in args.get("command") or [])
    if action_type == "web_search":
        return f'"{_text(args.get("query"))}"'
    if action_type == "fetch_url":
        return _text(args.get("url"))
    if action_type == "delegate_task":
        return _text(args.get("target_role"))
    return path or ("." if action_type in {"list_dir", "list_tree"} else "")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
