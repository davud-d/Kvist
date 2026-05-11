# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from typing import Any, Iterable
from urllib.parse import urlparse

from protocol import AgentCommand, Action

ALLOWED_ACTIONS = {
    "final_answer",
    "read_file",
    "read_multiple_files",
    "write_file",
    "append_file",
    "replace_in_file",
    "list_dir",
    "list_tree",
    "find_files",
    "search_code",
    "git_status",
    "git_diff",
    "run_shell",
    "web_search",
    "fetch_url",
}

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


class CommandValidationError(Exception):
    pass


def load_single_json_object(text: str) -> Any:
    last_error: str | None = None
    decoder = json.JSONDecoder()

    for candidate in _json_candidates(text):
        for sanitized in _sanitized_candidates(candidate):
            try:
                parsed, end = decoder.raw_decode(sanitized)
            except json.JSONDecodeError as error:
                last_error = str(error)
                continue

            if sanitized[end:].strip():
                last_error = "JSON must contain exactly one object and no trailing text"
                continue

            return parsed

    if last_error is None:
        raise CommandValidationError("Не удалось найти JSON-объект в ответе модели")
    raise CommandValidationError(f"Невалидный JSON: {last_error}")


def parse_agent_command(text: str) -> AgentCommand:
    raw = load_single_json_object(text)

    if not isinstance(raw, dict):
        raise CommandValidationError("Корневой объект должен быть JSON-объектом")

    require_only_allowed_keys(raw, {"thought", "plan_step", "progress", "user_message", "action"})

    thought = raw.get("thought")
    plan_step = raw.get("plan_step")
    progress = raw.get("progress")
    user_message = raw.get("user_message")
    action = raw.get("action")

    if not isinstance(thought, str) or not thought.strip():
        raise CommandValidationError("Поле 'thought' должно быть непустой строкой")

    if not isinstance(plan_step, str) or not plan_step.strip():
        raise CommandValidationError("Поле 'plan_step' должно быть непустой строкой")

    if not isinstance(progress, int):
        raise CommandValidationError("Поле 'progress' должно быть целым числом")

    if progress < 0 or progress > 100:
        raise CommandValidationError("Поле 'progress' должно быть в диапазоне 0..100")

    if not isinstance(user_message, str) or not user_message.strip():
        raise CommandValidationError("Поле 'user_message' должно быть непустой строкой")

    if not isinstance(action, dict):
        raise CommandValidationError("Поле 'action' должно быть объектом")

    action_type = action.get("type")
    args = action.get("args")

    if action_type not in ALLOWED_ACTIONS:
        raise CommandValidationError(f"Недопустимый action.type: {action_type!r}")

    if not isinstance(args, dict):
        raise CommandValidationError("Поле 'action.args' должно быть объектом")

    validate_action_args(str(action_type), args)

    return AgentCommand(
        thought=thought.strip(),
        plan_step=plan_step.strip(),
        progress=progress,
        user_message=user_message.strip(),
        action=Action(type=action_type, args=args),
    )


def validate_action_args(action_type: str, args: dict[str, Any]) -> None:
    if action_type == "final_answer":
        require_keys(args, {"message"})
        require_only_allowed_keys(args, {"message"})
        require_str(args, "message")

    elif action_type == "read_file":
        require_keys(args, {"path"})
        require_only_allowed_keys(args, {"path"})
        require_str(args, "path")

    elif action_type == "read_multiple_files":
        require_keys(args, {"paths"})
        require_only_allowed_keys(args, {"paths"})
        paths = args["paths"]
        if not isinstance(paths, list) or not paths:
            raise CommandValidationError("'paths' должен быть непустым списком строк")
        if not all(isinstance(item, str) and item.strip() for item in paths):
            raise CommandValidationError("'paths' должен содержать только непустые строки")

    elif action_type == "write_file":
        require_keys(args, {"path", "content"})
        require_only_allowed_keys(args, {"path", "content"})
        require_str(args, "path")
        require_str(args, "content")

    elif action_type == "append_file":
        require_keys(args, {"path", "content"})
        require_only_allowed_keys(args, {"path", "content"})
        require_str(args, "path")
        require_str(args, "content")

    elif action_type == "replace_in_file":
        require_keys(args, {"path", "old_text", "new_text"})
        require_only_allowed_keys(args, {"path", "old_text", "new_text", "count"})
        require_str(args, "path")
        require_str(args, "old_text")
        new_text = args.get("new_text")
        if not isinstance(new_text, str):
            raise CommandValidationError("'new_text' должен быть строкой")
        count = args.get("count")
        if count is not None:
            if not isinstance(count, int):
                raise CommandValidationError("'count' должен быть целым числом")
            if count < 1 or count > 1000:
                raise CommandValidationError("'count' должен быть в диапазоне 1..1000")

    elif action_type == "list_dir":
        require_keys(args, {"path"})
        require_only_allowed_keys(args, {"path"})
        require_str(args, "path")

    elif action_type == "list_tree":
        require_keys(args, {"path"})
        require_only_allowed_keys(args, {"path", "max_depth"})
        require_str(args, "path")
        max_depth = args.get("max_depth")
        if max_depth is not None:
            if not isinstance(max_depth, int):
                raise CommandValidationError("'max_depth' должен быть целым числом")
            if max_depth < 1 or max_depth > 10:
                raise CommandValidationError("'max_depth' должен быть в диапазоне 1..10")

    elif action_type == "find_files":
        require_keys(args, {"pattern"})
        require_only_allowed_keys(args, {"pattern", "path"})
        require_str(args, "pattern")
        require_optional_str(args, "path")

    elif action_type == "search_code":
        require_keys(args, {"query"})
        require_only_allowed_keys(args, {"query", "path", "max_results"})
        require_str(args, "query")
        require_optional_str(args, "path")
        max_results = args.get("max_results")
        if max_results is not None:
            if not isinstance(max_results, int):
                raise CommandValidationError("'max_results' должен быть целым числом")
            if max_results < 1 or max_results > 200:
                raise CommandValidationError("'max_results' должен быть в диапазоне 1..200")

    elif action_type == "git_status":
        if args:
            require_only_allowed_keys(args, set())

    elif action_type == "git_diff":
        require_only_allowed_keys(args, {"path", "ref", "staged"})
        require_optional_str(args, "path")
        require_optional_str(args, "ref")
        staged = args.get("staged")
        if staged is not None and not isinstance(staged, bool):
            raise CommandValidationError("'staged' должен быть логическим значением")

    elif action_type == "run_shell":
        require_keys(args, {"command"})
        require_only_allowed_keys(args, {"command", "cwd", "timeout"})
        command = args["command"]
        if not isinstance(command, list):
            raise CommandValidationError("'command' должен быть списком строк")
        if not command:
            raise CommandValidationError("'command' не должен быть пустым")
        if not all(isinstance(x, str) and x for x in command):
            raise CommandValidationError("'command' должен содержать только непустые строки")
        require_optional_str(args, "cwd")
        timeout = args.get("timeout")
        if timeout is not None:
            if not isinstance(timeout, (int, float)):
                raise CommandValidationError("'timeout' должен быть числом")
            if timeout < 1 or timeout > 30:
                raise CommandValidationError("'timeout' должен быть в диапазоне 1..30")

    elif action_type == "web_search":
        require_keys(args, {"query"})
        require_only_allowed_keys(
            args,
            {"query", "top_k", "domain_filter", "freshness", "max_results"},
        )
        require_str(args, "query")

        top_k = args.get("top_k")
        if top_k is not None:
            if not isinstance(top_k, int):
                raise CommandValidationError("'top_k' должен быть целым числом")
            if top_k < 1 or top_k > 50:
                raise CommandValidationError("'top_k' должен быть в диапазоне 1..50")

        max_results = args.get("max_results")
        if max_results is not None:
            if not isinstance(max_results, int):
                raise CommandValidationError("'max_results' должен быть целым числом")
            if max_results < 1 or max_results > 50:
                raise CommandValidationError("'max_results' должен быть в диапазоне 1..50")

        domain_filter = args.get("domain_filter")
        if domain_filter is not None:
            if not isinstance(domain_filter, list) or not domain_filter:
                raise CommandValidationError("'domain_filter' должен быть непустым списком строк")
            if not all(isinstance(item, str) and item.strip() for item in domain_filter):
                raise CommandValidationError("'domain_filter' должен содержать только непустые строки")

        freshness = args.get("freshness")
        if freshness is not None:
            if not isinstance(freshness, str):
                raise CommandValidationError("'freshness' должен быть строкой")
            if freshness not in {"any", "day", "week", "month"}:
                raise CommandValidationError("'freshness' должен быть одним из: any, day, week, month")

    elif action_type == "fetch_url":
        require_keys(args, {"url"})
        require_only_allowed_keys(args, {"url", "max_chars", "extract_mode"})
        require_str(args, "url")

        url = str(args.get("url", "")).strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise CommandValidationError("'url' должен использовать схему http или https")
        if not parsed.netloc:
            raise CommandValidationError("'url' должен содержать домен")

        max_chars = args.get("max_chars")
        if max_chars is not None:
            if not isinstance(max_chars, int):
                raise CommandValidationError("'max_chars' должен быть целым числом")
            if max_chars < 500 or max_chars > 50000:
                raise CommandValidationError("'max_chars' должен быть в диапазоне 500..50000")

        extract_mode = args.get("extract_mode")
        if extract_mode is not None:
            if not isinstance(extract_mode, str):
                raise CommandValidationError("'extract_mode' должен быть строкой")
            if extract_mode not in {"text", "markdown"}:
                raise CommandValidationError("'extract_mode' должен быть одним из: text, markdown")


def require_keys(data: dict[str, Any], required: set[str]) -> None:
    missing = required - set(data.keys())
    if missing:
        raise CommandValidationError(
            f"Отсутствуют обязательные поля: {', '.join(sorted(missing))}"
        )


def require_only_allowed_keys(data: dict[str, Any], allowed: set[str]) -> None:
    extra = set(data.keys()) - allowed
    if extra:
        raise CommandValidationError(f"Лишние поля: {', '.join(sorted(extra))}")


def require_str(data: dict[str, Any], key: str) -> None:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CommandValidationError(f"'{key}' должен быть непустой строкой")


def require_optional_str(data: dict[str, Any], key: str) -> None:
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise CommandValidationError(f"'{key}' должен быть строкой")


def _json_candidates(text: str) -> Iterable[str]:
    stripped = text.strip()
    if stripped:
        yield stripped

    fenced = FENCE_RE.fullmatch(stripped)
    if fenced is not None:
        cleaned = fenced.group(1).strip()
        if cleaned:
            yield cleaned


def _sanitized_candidates(text: str) -> Iterable[str]:
    replacements = {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "\u00a0": " ",
    }

    cleaned = text.strip()
    for src, dst in replacements.items():
        cleaned = cleaned.replace(src, dst)

    yield cleaned
    yield re.sub(r",(\s*[}\]])", r"\1", cleaned)
