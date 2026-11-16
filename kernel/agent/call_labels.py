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
    "job_output": ("смотрит вывод {target}", "reads the output of {target}"),
    "job_stop": ("останавливает {target}", "stops {target}"),
    "web_search": ("ищет в сети {target}", "searches the web for {target}"),
    "fetch_url": ("открывает {target}", "opens {target}"),
    "delegate_task": ("передаёт задачу роли {target}", "hands the task to {target}"),
    "refuse": ("отказывается", "refuses"),
    "final_answer": ("отвечает", "answers"),
    "report_to_user": ("готовит отчёт", "writes the report"),
    "offer_plan": ("предлагает сначала показать план", "offers to show a plan first"),
    "offer_best_of": ("предлагает запустить best-of", "offers to run best-of"),
    "propose_plan": ("предлагает план", "proposes a plan"),
    "edit_plan": ("изменяет план", "updates the plan"),
    "cancel_plan": ("отменяет задачу", "cancels the task"),
    "skills": ("скиллы: {target}", "skills: {target}"),
    "memory": ("память: {target}", "memory: {target}"),
    "sessions": ("прошлые разговоры: {target}", "past conversations: {target}"),
    "helper": ("помощница: {target}", "helper: {target}"),
    "ask_user": ("спрашивает вас", "asks you"),
    "wait": ("ждёт {target}", "waits {target}"),
    "schedule": ("расписание: {target}", "schedule: {target}"),
    "feedback": ("пишет черновик отзыва", "drafts feedback"),
    "generate_image": ("рисует картинку {target}", "generates an image {target}"),
    "verdict": ("выносит вердикт", "gives the verdict"),
}

# What the reviewer of a best-of run does, by op. The person sees "variants", never "executors" or the reviewer's
# role; {id} is the variant's id (e2). ``wait`` without an id waits for all of them.
_EXECUTOR_TEMPLATES: dict[str, tuple[str, str]] = {
    "status": ("смотрит итоги вариантов", "checks how the variants are doing"),
    "wait": ("ждёт варианты", "waits for the variants"),
    "wait_one": ("ждёт вариант {id}", "waits for variant {id}"),
    "inspect": ("смотрит, что делал вариант {id}", "looks at what variant {id} did"),
    "message": ("пишет варианту {id}", "sends a note to variant {id}"),
    "start": ("даёт варианту {id} новую работу", "gives variant {id} more work"),
    "diff": ("читает diff варианта {id}", "reads the diff of variant {id}"),
    "check": ("запускает проверку варианта {id}", "runs the check on variant {id}"),
    "set_check": ("выбирает команду проверки", "picks the check command"),
    "eliminate": ("убирает вариант {id} из сравнения", "drops variant {id} from the comparison"),
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
    if action_type == "executor":
        return _executor_label(args, language)
    ru, en = _TEMPLATES.get(action_type, ("{target}", "{target}"))
    template = ru if language == "ru" else en
    return template.format(target=_target(action_type, args, language)).strip()


def _executor_label(args: dict[str, Any], language: str) -> str:
    op = _text(args.get("op"))
    executor_id = _text(args.get("executor_id"))
    key = "wait_one" if op == "wait" and executor_id else op
    ru, en = _EXECUTOR_TEMPLATES.get(key, ("работает с вариантами", "works with the variants"))
    return (ru if language == "ru" else en).format(id=executor_id)


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
    if action_type in {"job_output", "job_stop"}:
        return _text(args.get("job_id"))
    if action_type == "wait":
        return _wait_duration(args.get("seconds"), language)
    if action_type in {"skills", "memory", "sessions", "helper", "schedule"}:
        detail = (args.get("skill") or args.get("query") or args.get("session") or args.get("agent_id")
                  or args.get("id") or args.get("source_id") or "")
        return f"{_text(args.get('op'))} {_text(detail)}".strip()
    return path or ("." if action_type in {"list_dir", "list_tree"} else "")


def _wait_duration(seconds: Any, language: str) -> str:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return ""
    units = (("ч", "h", 3600), ("мин", "min", 60), ("с", "s", 1))
    for ru, en, size in units:
        if seconds >= size:
            return f"{round(seconds / size)} {ru if language == 'ru' else en}"
    return ""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
