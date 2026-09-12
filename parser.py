# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from typing import Any, Iterable
from urllib.parse import urlparse

from protocol import AgentCommand, Action

ALLOWED_ACTIONS = {
    "final_answer",
    "refuse",
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
    "delegate_task",
    "report_to_user",
}

ALLOWED_PLAN_STEPS = {"inspect", "plan", "edit", "verify", "done"}

MAX_BATCH_ACTIONS = 8

_FINISHING_ACTIONS = {"final_answer", "report_to_user"}

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

    for candidate in _json_candidates(text):
        for sanitized in _sanitized_candidates(candidate):
            parsed = _extract_embedded_json_object(decoder, sanitized)
            if parsed is not _JSON_NOT_FOUND:
                return parsed

    if last_error is None:
        raise CommandValidationError("Could not find a JSON object in the model response")
    raise CommandValidationError(f"Invalid JSON: {last_error}")


def parse_agent_commands(text: str) -> list[AgentCommand]:
    raw = load_single_json_object(text)

    if not isinstance(raw, dict):
        raise CommandValidationError("Root object must be a JSON object")

    # "progress" is accepted and discarded: the runtime derives progress from the
    # plan and plan_step, but a model prompted from a cached older schema still
    # emits the key, and rejecting it would cost a step for nothing. A single
    # "action" object is the pre-batch form and is accepted for the same reason.
    require_only_allowed_keys(
        raw,
        {"thought", "plan_step", "progress", "user_message", "action", "actions", "step_summary"},
    )

    thought = raw.get("thought")
    plan_step = raw.get("plan_step")
    user_message = raw.get("user_message")
    step_summary = raw.get("step_summary")

    if not isinstance(thought, str) or not thought.strip():
        raise CommandValidationError("Field 'thought' must be a non-empty string")

    if not isinstance(plan_step, str) or not plan_step.strip():
        raise CommandValidationError("Field 'plan_step' must be a non-empty string")

    if plan_step.strip() not in ALLOWED_PLAN_STEPS:
        raise CommandValidationError(
            "Field 'plan_step' must be one of: inspect, plan, edit, verify, done"
        )

    if not isinstance(user_message, str) or not user_message.strip():
        raise CommandValidationError("Field 'user_message' must be a non-empty string")

    if step_summary is not None and not isinstance(step_summary, str):
        raise CommandValidationError("Field 'step_summary' must be a string")

    actions = [_parse_action(item) for item in _raw_actions(raw)]
    _validate_batch_order(actions)

    return [
        AgentCommand(
            thought=thought.strip(),
            plan_step=plan_step.strip(),
            user_message=user_message.strip(),
            action=action,
            step_summary=step_summary.strip() if isinstance(step_summary, str) else "",
        )
        for action in actions
    ]


def _raw_actions(raw: dict[str, Any]) -> list[Any]:
    if "action" in raw and "actions" in raw:
        raise CommandValidationError("Use either 'actions' or 'action', not both")
    if "action" in raw:
        return [raw["action"]]

    actions = raw.get("actions")
    if not isinstance(actions, list) or not actions:
        raise CommandValidationError("Field 'actions' must be a non-empty list of action objects")
    # A response cut off by the output-token limit is invalid JSON and loses
    # every action in it, so a batch is kept short.
    if len(actions) > MAX_BATCH_ACTIONS:
        raise CommandValidationError(
            f"Field 'actions' may hold at most {MAX_BATCH_ACTIONS} actions"
        )
    return actions


def _parse_action(item: Any) -> Action:
    if not isinstance(item, dict):
        raise CommandValidationError("Each action must be an object")

    action_type = item.get("type")
    args = item.get("args")
    description = item.get("description", "")

    if action_type not in ALLOWED_ACTIONS:
        raise CommandValidationError(f"Invalid action.type: {action_type!r}")

    if not isinstance(args, dict):
        raise CommandValidationError("Field 'action.args' must be an object")

    if not isinstance(description, str):
        raise CommandValidationError("Field 'action.description' must be a string")

    validate_action_args(str(action_type), args)
    return Action(type=action_type, args=args, description=description.strip())


def _validate_batch_order(actions: list[Action]) -> None:
    # A terminal action reports on work it has not seen finish when anything
    # follows it, and a refusal must stop the run before any other action runs.
    if len(actions) == 1:
        return
    types = [action.type for action in actions]
    if "refuse" in types:
        raise CommandValidationError("'refuse' must be the only action in a response")
    if any(action_type in _FINISHING_ACTIONS for action_type in types[:-1]):
        raise CommandValidationError(
            "final_answer and report_to_user may only be the last action in 'actions'"
        )


def validate_action_args(action_type: str, args: dict[str, Any]) -> None:
    if action_type == "final_answer":
        require_keys(args, {"message"})
        require_only_allowed_keys(args, {"message"})
        require_str(args, "message")

    elif action_type == "refuse":
        require_keys(args, {"reason"})
        require_only_allowed_keys(args, {"reason"})
        require_str(args, "reason")

    elif action_type == "read_file":
        require_keys(args, {"path"})
        require_only_allowed_keys(args, {"path"})
        require_str(args, "path")

    elif action_type == "read_multiple_files":
        require_keys(args, {"paths"})
        require_only_allowed_keys(args, {"paths"})
        paths = args["paths"]
        if not isinstance(paths, list) or not paths:
            raise CommandValidationError("'paths' must be a non-empty list of strings")
        if not all(isinstance(item, str) and item.strip() for item in paths):
            raise CommandValidationError("'paths' must contain only non-empty strings")

    elif action_type == "write_file":
        require_keys(args, {"path", "content"})
        require_only_allowed_keys(args, {"path", "content"})
        require_str(args, "path")
        require_text(args, "content")

    elif action_type == "append_file":
        require_keys(args, {"path", "content"})
        require_only_allowed_keys(args, {"path", "content"})
        require_str(args, "path")
        require_text(args, "content")

    elif action_type == "replace_in_file":
        require_keys(args, {"path", "old_text", "new_text"})
        require_only_allowed_keys(args, {"path", "old_text", "new_text", "count"})
        require_str(args, "path")
        require_str(args, "old_text")
        new_text = args.get("new_text")
        if not isinstance(new_text, str):
            raise CommandValidationError("'new_text' must be a string")
        count = args.get("count")
        if count is not None:
            if not isinstance(count, int):
                raise CommandValidationError("'count' must be an integer")
            if count < 1 or count > 1000:
                raise CommandValidationError("'count' must be in range 1..1000")

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
                raise CommandValidationError("'max_depth' must be an integer")
            if max_depth < 1 or max_depth > 10:
                raise CommandValidationError("'max_depth' must be in range 1..10")

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
                raise CommandValidationError("'max_results' must be an integer")
            if max_results < 1 or max_results > 200:
                raise CommandValidationError("'max_results' must be in range 1..200")

    elif action_type == "git_status":
        if args:
            require_only_allowed_keys(args, set())

    elif action_type == "git_diff":
        require_only_allowed_keys(args, {"path", "ref", "staged"})
        require_optional_str(args, "path")
        require_optional_str(args, "ref")
        staged = args.get("staged")
        if staged is not None and not isinstance(staged, bool):
            raise CommandValidationError("'staged' must be a boolean")

    elif action_type == "run_shell":
        require_keys(args, {"command"})
        require_only_allowed_keys(args, {"command", "cwd", "timeout"})
        command = args["command"]
        if not isinstance(command, list):
            raise CommandValidationError("'command' must be a list of strings")
        if not command:
            raise CommandValidationError("'command' must not be empty")
        if not all(isinstance(x, str) and x for x in command):
            raise CommandValidationError("'command' must contain only non-empty strings")
        require_optional_str(args, "cwd")
        timeout = args.get("timeout")
        if timeout is not None:
            if not isinstance(timeout, (int, float)):
                raise CommandValidationError("'timeout' must be a number")
            if timeout < 1 or timeout > 30:
                raise CommandValidationError("'timeout' must be in range 1..30")

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
                raise CommandValidationError("'top_k' must be an integer")
            if top_k < 1 or top_k > 50:
                raise CommandValidationError("'top_k' must be in range 1..50")

        max_results = args.get("max_results")
        if max_results is not None:
            if not isinstance(max_results, int):
                raise CommandValidationError("'max_results' must be an integer")
            if max_results < 1 or max_results > 50:
                raise CommandValidationError("'max_results' must be in range 1..50")

        domain_filter = args.get("domain_filter")
        if domain_filter is not None:
            if not isinstance(domain_filter, list) or not domain_filter:
                raise CommandValidationError("'domain_filter' must be a non-empty list of strings")
            if not all(isinstance(item, str) and item.strip() for item in domain_filter):
                raise CommandValidationError("'domain_filter' must contain only non-empty strings")

        freshness = args.get("freshness")
        if freshness is not None:
            if not isinstance(freshness, str):
                raise CommandValidationError("'freshness' must be a string")
            if freshness not in {"any", "day", "week", "month"}:
                raise CommandValidationError("'freshness' must be one of: any, day, week, month")

    elif action_type == "fetch_url":
        require_keys(args, {"url"})
        require_only_allowed_keys(args, {"url", "max_chars", "extract_mode"})
        require_str(args, "url")

        url = str(args.get("url", "")).strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise CommandValidationError("'url' must use http or https scheme")
        if not parsed.netloc:
            raise CommandValidationError("'url' must contain a domain")

        max_chars = args.get("max_chars")
        if max_chars is not None:
            if not isinstance(max_chars, int):
                raise CommandValidationError("'max_chars' must be an integer")
            if max_chars < 500 or max_chars > 50000:
                raise CommandValidationError("'max_chars' must be in range 500..50000")

        extract_mode = args.get("extract_mode")
        if extract_mode is not None:
            if not isinstance(extract_mode, str):
                raise CommandValidationError("'extract_mode' must be a string")
            if extract_mode not in {"text", "markdown"}:
                raise CommandValidationError("'extract_mode' must be one of: text, markdown")

    elif action_type == "delegate_task":
        require_keys(args, {"target_role", "task"})
        require_only_allowed_keys(args, {"target_role", "task", "commands"})
        require_str(args, "target_role")
        target_role = str(args["target_role"]).strip()
        if target_role not in {"coder", "devops", "chat"}:
            raise CommandValidationError(
                "'target_role' must be one of: coder, devops, chat"
            )
        require_str(args, "task")
        commands = args.get("commands")
        if commands is not None:
            if not isinstance(commands, list):
                raise CommandValidationError("'commands' must be a list of strings")
            if not all(isinstance(c, str) and c.strip() for c in commands):
                raise CommandValidationError(
                    "'commands' must contain only non-empty strings"
                )

    elif action_type == "report_to_user":
        require_keys(args, {"summary", "result"})
        require_only_allowed_keys(args, {"summary", "result", "issues"})
        require_str(args, "summary")
        require_str(args, "result")
        if "issues" in args:
            require_text(args, "issues")


def require_keys(data: dict[str, Any], required: set[str]) -> None:
    missing = required - set(data.keys())
    if missing:
        raise CommandValidationError(
            f"Missing required fields: {', '.join(sorted(missing))}"
        )


def require_only_allowed_keys(data: dict[str, Any], allowed: set[str]) -> None:
    extra = set(data.keys()) - allowed
    if extra:
        raise CommandValidationError(f"Extra fields: {', '.join(sorted(extra))}")


def require_str(data: dict[str, Any], key: str) -> None:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CommandValidationError(f"'{key}' must be a non-empty string")


def require_text(data: dict[str, Any], key: str) -> None:
    value = data.get(key)
    if not isinstance(value, str):
        raise CommandValidationError(f"'{key}' must be a string")


def require_optional_str(data: dict[str, Any], key: str) -> None:
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise CommandValidationError(f"'{key}' must be a string")


def _json_candidates(text: str) -> Iterable[str]:
    stripped = text.strip()
    if stripped:
        yield stripped

    fenced = FENCE_RE.fullmatch(stripped)
    if fenced is not None:
        cleaned = fenced.group(1).strip()
        if cleaned:
            yield cleaned

    for fenced_match in FENCE_RE.finditer(text):
        cleaned = fenced_match.group(1).strip()
        if cleaned:
            yield cleaned


def _sanitized_candidates(text: str) -> Iterable[str]:
    replacements = {
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\u00a0": " ",
    }

    cleaned = text.strip()
    for src, dst in replacements.items():
        cleaned = cleaned.replace(src, dst)

    yield cleaned
    yield re.sub(r",(\s*[}\]])", r"\1", cleaned)


_JSON_NOT_FOUND = object()


def _extract_embedded_json_object(decoder: json.JSONDecoder, text: str) -> Any:
    for index, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return _JSON_NOT_FOUND
