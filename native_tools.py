# -*- coding: utf-8 -*-
"""The action protocol offered as a provider's native tools.

A run on a model that calls tools natively keeps one growing conversation —
the task, each response's calls, their results — instead of a prompt rebuilt
from scratch every step. Everything past the model call is shared with the
JSON protocol: a response's calls become the same JSON object, pass the same
parser and run through the same guards.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from parser import CommandValidationError, parse_agent_commands
from protocol import AgentCommand

_STRING: dict[str, Any] = {"type": "string"}
_STRINGS: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
_OUTCOME: dict[str, Any] = {"type": "string", "enum": ["done", "incomplete"]}


def _integer(low: int, high: int) -> dict[str, Any]:
    return {"type": "integer", "minimum": low, "maximum": high}


# name -> (summary, properties, required). Bounds mirror the parser's
# validate_action_args, which still checks every call.
ACTION_TOOLS: dict[str, tuple[str, dict[str, Any], tuple[str, ...]]] = {
    "final_answer": (
        "Finish the task with a direct answer to the person.",
        {"message": _STRING, "outcome": _OUTCOME},
        ("message",),
    ),
    "report_to_user": (
        "Finish the task with facts a reporter turns into the person's report.",
        {"summary": _STRING, "result": _STRING, "issues": _STRING, "outcome": _OUTCOME},
        ("summary", "result"),
    ),
    "refuse": ("Refuse an illegal or malicious task.", {"reason": _STRING}, ("reason",)),
    "read_file": ("Read one workspace file.", {"path": _STRING}, ("path",)),
    "read_multiple_files": ("Read several workspace files.", {"paths": _STRINGS}, ("paths",)),
    "write_file": ("Create or overwrite a file.", {"path": _STRING, "content": _STRING}, ("path", "content")),
    "append_file": ("Append to a file.", {"path": _STRING, "content": _STRING}, ("path", "content")),
    "replace_in_file": (
        "Replace an exact fragment of a file.",
        {"path": _STRING, "old_text": _STRING, "new_text": _STRING, "count": _integer(1, 1000)},
        ("path", "old_text", "new_text"),
    ),
    "list_dir": ("List one directory.", {"path": _STRING}, ("path",)),
    "list_tree": ("List a directory tree.", {"path": _STRING, "max_depth": _integer(1, 10)}, ("path",)),
    "find_files": ("Find files by glob.", {"pattern": _STRING, "path": _STRING}, ("pattern",)),
    "search_code": (
        "Search file contents for a fixed string, case-insensitively.",
        {"query": _STRING, "path": _STRING, "max_results": _integer(1, 200)},
        ("query",),
    ),
    "git_status": ("Show git status.", {}, ()),
    "git_diff": (
        "Show a git diff.",
        {"path": _STRING, "ref": _STRING, "staged": {"type": "boolean"}},
        (),
    ),
    "run_shell": (
        "Run one command as an argv list, never a shell string.",
        {"command": _STRINGS, "cwd": _STRING, "timeout": {"type": "number"}},
        ("command",),
    ),
    "web_search": (
        "Search the web.",
        {
            "query": _STRING,
            "top_k": _integer(1, 50),
            "max_results": _integer(1, 50),
            "domain_filter": _STRINGS,
            "freshness": {"type": "string", "enum": ["any", "day", "week", "month"]},
        },
        ("query",),
    ),
    "fetch_url": (
        "Fetch one web page.",
        {
            "url": _STRING,
            "max_chars": _integer(500, 50000),
            "extract_mode": {"type": "string", "enum": ["text", "markdown"]},
        },
        ("url",),
    ),
    "delegate_task": (
        "Hand a sub-task to another role.",
        {
            "target_role": {"type": "string", "enum": ["coder", "devops", "chat"]},
            "task": _STRING,
            "commands": _STRINGS,
        },
        ("target_role", "task"),
    ),
}

_FINISHING = frozenset({"final_answer", "report_to_user", "refuse"})
_WRITES = frozenset({"write_file", "append_file", "replace_in_file"})
_ALLOWED_ACTIONS_RE = re.compile(r"^== \d+\. ALLOWED ACTIONS ==\s*\n\s*([^\n]+)", re.MULTILINE)
_SECTION_RE = re.compile(r"^== \d+\. (.+?) ==$", re.MULTILINE)
# Sections that describe the JSON protocol itself; the tools replace them.
_PROTOCOL_SECTIONS = ("RESPONSE FORMAT", "ARGS SCHEMAS", "EXAMPLES", "SELF-CHECK")
_JSON_ONLY_LINE_RE = re.compile(r"\s*Return only one valid JSON object\. Always\.")
ELIDED_OUTPUT = "[output removed to fit the context window; run the action again if you need it]"


class NativeToolsRejected(RuntimeError):
    """The provider refused the ``tools`` parameter mid-run."""


@dataclass(slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass(slots=True)
class NativeTurn:
    content: str
    reasoning: str = ""
    # True when the provider returned reasoning as ``reasoning_content``,
    # the dialect (DeepSeek) that requires it back on later requests.
    echo_reasoning: bool = False
    tool_calls: list[ToolCall] = field(default_factory=list)


def tools_for_role(role_prompt: str) -> list[dict[str, Any]]:
    """Tool definitions for the actions the role prompt allows.

    The role prompt's ALLOWED ACTIONS line is already kept in sync with the
    parser by test_prompt_protocol_sync, so the reader gets no write tools.
    """
    match = _ALLOWED_ACTIONS_RE.search(role_prompt)
    if match is None:
        raise ValueError("role prompt has no ALLOWED ACTIONS section")
    names = [name.strip(" .") for name in match.group(1).split(",")]
    return [_tool(name) for name in names if name in ACTION_TOOLS]


def _tool(name: str) -> dict[str, Any]:
    summary, properties, required = ACTION_TOOLS[name]
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": summary,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


def native_role_prompt(role_prompt: str, channel_section: str) -> str:
    """The role prompt with its JSON-protocol sections replaced by the tool channel.

    Left in, "every response = exactly one JSON object" and a page of JSON
    examples would contradict the tools on every call.
    """
    parts: list[str] = []
    position = 0
    matches = list(_SECTION_RE.finditer(role_prompt))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(role_prompt)
        parts.append(role_prompt[position:match.start()])
        title = match.group(1)
        if title == "RESPONSE FORMAT":
            parts.append(channel_section.strip() + "\n\n")
        elif not title.startswith(_PROTOCOL_SECTIONS):
            parts.append(role_prompt[match.start():end])
        position = end
    parts.append(role_prompt[position:])
    text = "".join(parts).replace(" Strict JSON protocol.", "")
    return _JSON_ONLY_LINE_RE.sub("", text).rstrip() + "\n"


def commands_from_turn(turn: NativeTurn, *, files_changed: bool) -> list[AgentCommand]:
    """The turn's calls as the JSON protocol's commands, through the same parser.

    Raises CommandValidationError for arguments that are not a JSON object,
    an unknown tool, or a batch the parser refuses.
    """
    names = [call.name for call in turn.tool_calls]
    actions = [
        {"type": call.name, "args": _arguments(call), "description": _describe(call)}
        for call in turn.tool_calls
    ]
    return _parse(turn.content, names, actions, files_changed)


def final_answer_from_turn(turn: NativeTurn) -> list[AgentCommand]:
    """A response without calls ends the task; its text is the final answer."""
    action = {"type": "final_answer", "args": {"message": turn.content.strip()}}
    return _parse(turn.content, ["final_answer"], [action], files_changed=False)


def _parse(text: str, names: list[str], actions: list[dict[str, Any]], files_changed: bool) -> list[AgentCommand]:
    line = text.strip()
    payload = {
        "thought": line.splitlines()[0][:140] if line else "Calling " + ", ".join(names),
        "plan_step": _plan_step(names, files_changed),
        "user_message": line or " · ".join(names),
        "actions": actions,
    }
    return parse_agent_commands(json.dumps(payload, ensure_ascii=False))


def _arguments(call: ToolCall) -> dict[str, Any]:
    try:
        value = json.loads(call.arguments or "{}")
    except json.JSONDecodeError as error:
        raise CommandValidationError(f"Arguments of {call.name} are not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise CommandValidationError(f"Arguments of {call.name} must be a JSON object")
    return value


def _describe(call: ToolCall) -> str:
    """A label for the banner's Action row, which holds 16 characters."""
    try:
        args = json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        return call.name[:16]
    if not isinstance(args, dict):
        return call.name[:16]
    for key in ("path", "query", "pattern", "url"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return (Path(value).name if key == "path" else value)[:16]
    command = args.get("command")
    if isinstance(command, list) and command:
        return " ".join(str(part) for part in command)[:16]
    return call.name[:16]


def _plan_step(names: list[str], files_changed: bool) -> str:
    kinds = set(names)
    if kinds & _FINISHING:
        return "done"
    if kinds & _WRITES:
        return "edit"
    if "run_shell" in kinds and files_changed:
        return "verify"
    return "inspect"


def assistant_message(turn: NativeTurn) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": turn.content or ""}
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments or "{}"},
            }
            for call in turn.tool_calls
        ]
    if turn.echo_reasoning and turn.reasoning:
        message["reasoning_content"] = turn.reasoning
    return message


def tool_message(call_id: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def compact_history(
    messages: list[dict[str, Any]],
    count_tokens: Callable[[str], int],
    budget: int,
) -> int:
    """Elide the oldest tool outputs until the conversation fits ``budget``.

    Only tool results are shortened: the structure the API validates (every
    call answered, reasoning kept on the turn that made it) stays whole, and
    the calls themselves still say what was looked at. Returns how many
    outputs were elided.
    """
    total = sum(_message_tokens(message, count_tokens) for message in messages)
    elided = 0
    for message in messages:
        if total <= budget:
            break
        if message.get("role") != "tool" or message.get("content") == ELIDED_OUTPUT:
            continue
        total -= _message_tokens(message, count_tokens)
        message["content"] = ELIDED_OUTPUT
        total += _message_tokens(message, count_tokens)
        elided += 1
    return elided


def _message_tokens(message: dict[str, Any], count_tokens: Callable[[str], int]) -> int:
    text = str(message.get("content") or "")
    for call in message.get("tool_calls") or ():
        text += call["function"]["arguments"]
    return count_tokens(text) + count_tokens(str(message.get("reasoning_content") or ""))
