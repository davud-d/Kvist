# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Iterable
from urllib.parse import urlparse

from kernel.call_labels import call_label, trim_purpose
from kernel.protocol import (
    MAX_SHELL_TIMEOUT_SECONDS,
    MIN_SHELL_TIMEOUT_SECONDS,
    AgentCommand,
    Action,
    ModelResponse,
    ToolCallRequest,
)

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
    "offer_plan",
    "propose_plan",
    "edit_plan",
    "cancel_plan",
    # Offered only by an interactive frontend (end_session.available); the
    # runtime answers it as an unknown tool everywhere else.
    "end_session",
}

MAX_BATCH_ACTIONS = 8

# Not offered to the model since the v2 contract (a response without calls
# ends the turn), but still honoured when a model prompted with an older
# schema calls them.
FINISHING_ACTIONS = frozenset({"final_answer", "report_to_user", "refuse"})
_WRITE_ACTIONS = frozenset({"write_file", "append_file", "replace_in_file"})
PURPOSE_KEY = "purpose"
# run_shell's purpose is its label; git_status/git_diff take a stand-in one
# (native_tools._PURPOSE) that is dropped unseen.
_PURPOSE_ACTIONS = frozenset({"run_shell", "git_status", "git_diff"})
# "incomplete" lets the model end a run it cannot or should not finish
# without having to claim success. "no_change_needed" ends a run whose
# reported problem the model exercised and found absent: a finished answer
# with no file change, backed by the output of that run.
FINISH_OUTCOMES = ("done", "incomplete", "no_change_needed")

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


def build_command(
    call: ToolCallRequest, *, text: str, batch_id: str, plan_step: str, language: str
) -> AgentCommand:
    """One call of a response as a runtime command, or CommandValidationError.

    A call's ``purpose`` (run_shell's reason, and the stand-in argument of the
    tools that take none) is taken off before the arguments are checked: the
    executor never sees it, and only run_shell's reaches the person.
    """
    if call.error:
        raise CommandValidationError(call.error)
    if call.name not in ALLOWED_ACTIONS:
        raise CommandValidationError(f"Unknown tool {call.name!r}")
    args = dict(call.arguments or {})
    purpose = trim_purpose(args.pop(PURPOSE_KEY, "")) if call.name in _PURPOSE_ACTIONS else ""
    validate_action_args(call.name, args)
    return AgentCommand(
        plan_step=plan_step,
        action=Action(
            type=call.name,  # type: ignore[arg-type]
            args=args,
            description=call_label(call.name, args, language, purpose),
        ),
        text=text,
        call_id=call.call_id,
        batch_id=batch_id,
    )


def plan_step_for_calls(names: list[str], files_changed: bool) -> str:
    """The phase a response is in, read from what it calls, never declared by the model."""
    kinds = set(names)
    if kinds & FINISHING_ACTIONS:
        return "done"
    if kinds & {"offer_plan", "propose_plan", "edit_plan", "cancel_plan"}:
        return "plan"
    if kinds & _WRITE_ACTIONS:
        return "edit"
    if "run_shell" in kinds and files_changed:
        return "verify"
    return "inspect"


def validate_action_args(action_type: str, args: dict[str, Any]) -> None:
    if action_type == "final_answer":
        require_keys(args, {"message"})
        require_only_allowed_keys(args, {"message", "outcome"})
        require_str(args, "message")
        _require_outcome(args)

    elif action_type == "refuse":
        require_keys(args, {"reason"})
        require_only_allowed_keys(args, {"reason"})
        require_str(args, "reason")

    elif action_type == "read_file":
        require_keys(args, {"path"})
        require_only_allowed_keys(args, {"path", "start_line", "end_line"})
        require_str(args, "path")
        _require_line_range(args)

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
        require_only_allowed_keys(args, {"command", "cwd", "timeout", "sandbox", "network", "justification"})
        if not isinstance(args.get("sandbox", "default"), str) or args.get("sandbox", "default") not in {"default", "host"}:
            raise CommandValidationError("'sandbox' must be 'default' or 'host'")
        if not isinstance(args.get("network", False), bool):
            raise CommandValidationError("'network' must be a boolean")
        require_optional_str(args, "justification")
        if (args.get("sandbox") == "host" or args.get("network")) and not str(args.get("justification") or "").strip():
            raise CommandValidationError("'justification' is required for host or network access")
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
            # The model cannot know the active profile's ceiling, so an
            # out-of-range number is clamped here instead of costing the whole
            # response a format error. The terminal clamps again per profile.
            args["timeout"] = min(
                max(timeout, MIN_SHELL_TIMEOUT_SECONDS), MAX_SHELL_TIMEOUT_SECONDS
            )

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

    elif action_type in {"offer_plan", "cancel_plan"}:
        require_keys(args, {"reason"})
        require_only_allowed_keys(args, {"reason"})
        require_str(args, "reason")

    elif action_type in {"propose_plan", "edit_plan"}:
        require_keys(args, {"plan"})
        require_only_allowed_keys(args, {"plan"})
        require_str(args, "plan")

    elif action_type == "end_session":
        _validate_end_session(args)

    elif action_type == "report_to_user":
        require_keys(args, {"summary", "result"})
        require_only_allowed_keys(args, {"summary", "result", "issues", "outcome"})
        require_str(args, "summary")
        require_str(args, "result")
        if "issues" in args:
            require_text(args, "issues")
        _require_outcome(args)


_MESSAGE_ID_RE = re.compile(r"#?\s*([1-9][0-9]*)")


def _validate_end_session(args: dict[str, Any]) -> None:
    """Two forms: ``{message_id}`` flags a message, ``{confirm, reason}`` answers the review."""
    if "message_id" in args:
        require_only_allowed_keys(args, {"message_id"})
        value = args["message_id"]
        text = str(value).strip() if isinstance(value, (int, str)) and not isinstance(value, bool) else ""
        match = _MESSAGE_ID_RE.fullmatch(text)
        if match is None:
            raise CommandValidationError("'message_id' must be the number of a message, e.g. 7")
        args["message_id"] = int(match.group(1))
        return
    require_keys(args, {"confirm", "reason"})
    require_only_allowed_keys(args, {"confirm", "reason"})
    if not isinstance(args["confirm"], bool):
        raise CommandValidationError("'confirm' must be true or false")
    require_str(args, "reason")


def _require_outcome(args: dict[str, Any]) -> None:
    if "outcome" in args and args["outcome"] not in FINISH_OUTCOMES:
        raise CommandValidationError(
            f"'outcome' must be one of: {', '.join(FINISH_OUTCOMES)}"
        )


def _require_line_range(args: dict[str, Any]) -> None:
    for key in ("start_line", "end_line"):
        value = args.get(key)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise CommandValidationError(f"'{key}' must be an integer >= 1")
    start, end = args.get("start_line"), args.get("end_line")
    if start is not None and end is not None and end < start:
        raise CommandValidationError("'end_line' must not be less than 'start_line'")


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


# --- v2 response: free text plus <tool_call>{json}</tool_call> blocks --------

_OPEN_TAG_RE = re.compile(r"<\s*tool_call\s*>", re.IGNORECASE)
_CLOSE_TAG_RE = re.compile(r"<\s*/\s*tool_call\s*>", re.IGNORECASE)
# A model that wraps its tags in a markdown fence leaves the opening fence line
# in the text before them; only a fence touching a tag is removed, so a code
# block in the narration itself survives.
_FENCE_BEFORE_TAG_RE = re.compile(r"```[\w+-]*[ \t]*$")
_NAME_KEYS = ("name", "tool", "type")
_ARGUMENT_KEYS = ("arguments", "args", "parameters", "input")
_CALL_SHAPE = '{"name": "<tool>", "arguments": {...}}'


def parse_model_response(text: str) -> ModelResponse:
    """Split a text-mode response into the words for the person and its calls.

    Never raises: a call that cannot be read becomes a call carrying an error,
    so one broken block costs only itself.
    """
    if not _OPEN_TAG_RE.search(text):
        return _whole_object_response(text) or ModelResponse(text=text.strip(), calls=[])
    calls: list[ToolCallRequest] = []
    # Only the words before the first call are kept: whatever follows describes
    # a result the model has not been given, so it is a prediction ("done!").
    opening = text[: _OPEN_TAG_RE.search(text).start()]
    for _start, body, _end, _closed in _tagged_bodies(text):
        calls.append(_read_tagged_call(body, len(calls) + 1))
    return ModelResponse(text=_FENCE_BEFORE_TAG_RE.sub("", opening.rstrip(), count=1).strip(), calls=calls)


def _tagged_bodies(text: str) -> Iterable[tuple[int, str, int, bool]]:
    """(tag start, body, end, closed) per <tool_call>; an unclosed body runs to the next tag."""
    opens = list(_OPEN_TAG_RE.finditer(text))
    for index, match in enumerate(opens):
        limit = opens[index + 1].start() if index + 1 < len(opens) else len(text)
        close = _CLOSE_TAG_RE.search(text, match.end(), limit)
        if close is not None:
            yield match.start(), text[match.end():close.start()], close.end(), True
        else:
            yield match.start(), text[match.end():limit], limit, False


def _read_tagged_call(body: str, number: int) -> ToolCallRequest:
    """The call in one tag body."""
    stripped = _strip_inner_fence(body.strip())
    start = stripped.find("{")
    if start < 0:
        return _broken_call(number, f"no JSON object inside the tag; write {_CALL_SHAPE}", stripped)
    decoded = _decode_object(stripped[start:])
    if decoded is None:
        return _broken_call(number, f"invalid JSON; write {_CALL_SHAPE}", stripped)
    return _call_from_object(decoded[0], number)


def _strip_inner_fence(body: str) -> str:
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else body[3:]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3]
    return body.strip()


def _decode_object(text: str) -> tuple[Any, int | None] | None:
    """The first JSON value in ``text`` and where it ended in the raw text.

    The end is None when only a repaired copy (smart quotes, trailing commas)
    parsed, since its offsets no longer match the raw text.
    """
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(text)
        return value, end
    except json.JSONDecodeError:
        pass
    for sanitized in _sanitized_candidates(text):
        try:
            value, _end = decoder.raw_decode(sanitized)
        except json.JSONDecodeError:
            continue
        return value, None
    return None


def _call_from_object(value: Any, number: int) -> ToolCallRequest:
    if not isinstance(value, dict):
        return _broken_call(number, f"a call must be a JSON object: {_CALL_SHAPE}")
    function = value.get("function")
    if isinstance(function, dict):
        value = {**value, **function}
    name = next((value[key] for key in _NAME_KEYS if isinstance(value.get(key), str) and value[key].strip()), "")
    if not name or name == "function":
        return _broken_call(number, f"the call has no tool name: {_CALL_SHAPE}")
    arguments: Any = next((value[key] for key in _ARGUMENT_KEYS if key in value), None)
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as error:
            return _broken_call(number, f"arguments of {name} are not valid JSON ({error})", name=name)
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return _broken_call(number, f"arguments of {name} must be a JSON object", name=name)
    return ToolCallRequest(call_id=_new_call_id(), name=name.strip(), arguments=arguments)


def _broken_call(number: int, reason: str, raw: str = "", *, name: str = "") -> ToolCallRequest:
    if not name and raw:
        match = re.search(r'"(?:name|tool)"\s*:\s*"([A-Za-z_]+)"', raw)
        name = match.group(1) if match else ""
    return ToolCallRequest(
        call_id=_new_call_id(),
        name=name,
        arguments=None,
        error=f"Tool call {number} was not run: {reason}.",
    )


def _new_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:12]}"


def _whole_object_response(text: str) -> ModelResponse | None:
    """A response that is nothing but one JSON object of a call-like shape.

    The pre-v2 contract ({"actions": [...]}) from a model prompted with a cached
    older schema, or a bare call object without its tags. Only a response that
    is the object and nothing else qualifies: a JSON example inside an answer
    must never run.
    """
    stripped = text.strip()
    candidates = [stripped]
    fenced = FENCE_RE.fullmatch(stripped)
    if fenced is not None:
        candidates.append(fenced.group(1).strip())
    for candidate in candidates:
        for sanitized in _sanitized_candidates(candidate):
            try:
                value = json.loads(sanitized)
            except json.JSONDecodeError:
                continue
            return _response_from_object(value)
    return None


def _response_from_object(value: Any) -> ModelResponse | None:
    if not isinstance(value, dict):
        return None
    if "actions" in value or "action" in value:
        items = value.get("actions") if "actions" in value else [value.get("action")]
        if not isinstance(items, list):
            items = [items]
        words = value.get("user_message")
        calls = [_call_from_object(item, number) for number, item in enumerate(items, start=1)]
        return ModelResponse(text=words.strip() if isinstance(words, str) else "", calls=calls)
    if isinstance(value.get("name"), str) and any(key in value for key in _ARGUMENT_KEYS):
        return ModelResponse(text="", calls=[_call_from_object(value, 1)])
    return None
