# -*- coding: utf-8 -*-
"""The action protocol offered as a provider's native tools.

A run on a model that calls tools natively keeps one growing conversation —
the task, each response's calls, their results — instead of a prompt rebuilt
from scratch every step. Everything past the model call is shared with the
text-mode protocol: a response's calls become the same call requests, pass
the same validation and run through the same guards.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from kernel.protocol import ToolCallRequest

_STRING: dict[str, Any] = {"type": "string"}
_STRINGS: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
_OUTCOME: dict[str, Any] = {
    "type": "string",
    "enum": ["done", "incomplete", "no_change_needed"],
    "description": (
        "incomplete: the work was not done. no_change_needed: you ran the reported "
        "problem and it does not occur; show that run's output in the message."
    ),
}
# The schema is what a model reads while it fills the arguments, so the path
# rule belongs here as well as in the prompt: a task naming the repository by
# its absolute path otherwise walks the model into refusals.
_PATH: dict[str, Any] = {"type": "string", "description": "Relative to the workspace root."}
_PATHS: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Paths relative to the workspace root.",
}
_ARGV: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        'The argv list, e.g. ["git", "status"]. Paths relative to cwd, no shell operators; '
        "git takes no global options such as -C."
    ),
}
_CWD: dict[str, Any] = {"type": "string", "description": "Directory relative to the workspace root; defaults to it."}
_SHELL_PURPOSE: dict[str, Any] = {
    "type": "string",
    "description": "Why this command runs, in the person's language, at most 10 words. Shown to them.",
}


def _integer(low: int, high: int) -> dict[str, Any]:
    return {"type": "integer", "minimum": low, "maximum": high}


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One tool of the registry.

    ``readonly`` marks a tool with no side effect that may run in parallel with
    its neighbours (agent_runtime._run_calls). Every other tool is treated as
    writing: it runs alone, in call order, and is skipped after a failed write
    in the same response. run_shell is always writing; git_status/git_diff
    are too, because they spawn through process_tree.run_supervised, whose
    preexec_fn Python does not guarantee in a process with threads.
    """

    summary: str
    properties: dict[str, Any]
    required: tuple[str, ...]
    readonly: bool = False


# Bounds mirror the parser's validate_action_args, which still checks every call.
ACTION_TOOLS: dict[str, ToolSpec] = {
    "final_answer": ToolSpec(
        "Finish the task with a direct answer to the person.",
        {"message": _STRING, "outcome": _OUTCOME},
        ("message",),
    ),
    "report_to_user": ToolSpec(
        "Finish the task with facts a reporter turns into the person's report.",
        {"summary": _STRING, "result": _STRING, "issues": _STRING, "outcome": _OUTCOME},
        ("summary", "result"),
    ),
    "refuse": ToolSpec("Refuse an illegal or malicious task.", {"reason": _STRING}, ("reason",)),
    "read_file": ToolSpec(
        "Read one workspace file, whole or a range of its lines. The answer carries the file's total_lines.",
        {
            "path": _PATH,
            "start_line": {"type": "integer", "minimum": 1, "description": "First line to read (1-based)."},
            "end_line": {"type": "integer", "minimum": 1, "description": "Last line to read, inclusive."},
        },
        ("path",),
        readonly=True,
    ),
    "read_multiple_files": ToolSpec("Read several workspace files.", {"paths": _PATHS}, ("paths",), readonly=True),
    "write_file": ToolSpec("Create or overwrite a file.", {"path": _PATH, "content": _STRING}, ("path", "content")),
    "append_file": ToolSpec("Append to a file.", {"path": _PATH, "content": _STRING}, ("path", "content")),
    "replace_in_file": ToolSpec(
        "Replace an exact fragment of a file.",
        {"path": _PATH, "old_text": _STRING, "new_text": _STRING, "count": _integer(1, 1000)},
        ("path", "old_text", "new_text"),
    ),
    "list_dir": ToolSpec("List one directory.", {"path": _PATH}, ("path",), readonly=True),
    "list_tree": ToolSpec(
        "List a directory tree.", {"path": _PATH, "max_depth": _integer(1, 10)}, ("path",), readonly=True
    ),
    "find_files": ToolSpec("Find files by glob. truncated: true means more files match than were returned.", {"pattern": _STRING, "path": _PATH}, ("pattern",), readonly=True),
    "search_code": ToolSpec(
        "Search file contents for a fixed string, case-insensitively. truncated: true means more matches exist than were returned.",
        {"query": _STRING, "path": _PATH, "max_results": _integer(1, 200)},
        ("query",),
        readonly=True,
    ),
    "git_status": ToolSpec("Show git status.", {}, ()),
    "git_diff": ToolSpec(
        "Show a git diff.",
        {"path": _PATH, "ref": _STRING, "staged": {"type": "boolean"}},
        (),
    ),
    "run_shell": ToolSpec(
        "Run one command as an argv list, never a shell string.",
        {"command": _ARGV, "purpose": _SHELL_PURPOSE, "cwd": _CWD, "timeout": {"type": "number"}},
        ("command", "purpose"),
    ),
    "web_search": ToolSpec(
        "Search the web.",
        {
            "query": _STRING,
            "top_k": _integer(1, 50),
            "max_results": _integer(1, 50),
            "domain_filter": _STRINGS,
            "freshness": {"type": "string", "enum": ["any", "day", "week", "month"]},
        },
        ("query",),
        readonly=True,
    ),
    "fetch_url": ToolSpec(
        "Fetch one web page.",
        {
            "url": _STRING,
            "max_chars": _integer(500, 50000),
            "extract_mode": {"type": "string", "enum": ["text", "markdown"]},
        },
        ("url",),
        readonly=True,
    ),
    "delegate_task": ToolSpec(
        "Hand a sub-task to another role.",
        {
            "target_role": {"type": "string", "enum": ["coder", "devops", "chat"]},
            "task": _STRING,
            "commands": _STRINGS,
        },
        ("target_role", "task"),
    ),
    "offer_plan": ToolSpec(
        "Ask the person whether to show a plan before any change. Shown to them with your reason.",
        {"reason": {"type": "string", "description": "One or two sentences, in the person's language: why a plan helps here."}},
        ("reason",),
    ),
    "propose_plan": ToolSpec(
        "Show the person your plan for their decision. Only in plan mode, after exploring read-only.",
        {"plan": {"type": "string", "description": "Short Markdown: goal, key changes, how you will verify, assumptions."}},
        ("plan",),
    ),
    # Never in a role prompt's ALLOWED ACTIONS: the runtime adds it only where
    # end_session.available holds. Two forms share one flat schema, because a
    # oneOf is not accepted by every provider's tool validator.
    "end_session": ToolSpec(
        "Close this conversation for good; see end_session in the system prompt. First call: message_id "
        "alone. The answer to END_SESSION REVIEW: confirm and reason, without message_id.",
        {
            "message_id": {
                "type": "integer",
                "minimum": 1,
                "description": "The number of the person's message, as the conversation shows it (#N).",
            },
            "confirm": {"type": "boolean"},
            "reason": {"type": "string", "description": "One line: which message, what it targets."},
        },
        (),
    ),
}

READONLY_TOOLS = frozenset(name for name, spec in ACTION_TOOLS.items() if spec.readonly)

_ALLOWED_ACTIONS_RE = re.compile(r"^== \d+\. ALLOWED ACTIONS ==\s*\n\s*([^\n]+)", re.MULTILINE)
_SECTION_RE = re.compile(r"^== \d+\. (.+?) ==$", re.MULTILINE)
# Sections that describe the text-mode protocol itself; the tools replace them.
_PROTOCOL_SECTIONS = ("RESPONSE FORMAT", "ARGS SCHEMAS", "EXAMPLES", "SELF-CHECK")
ELIDED_OUTPUT = "[output removed to fit the context window; run the action again if you need it]"
# Closes the first user message of a native run. Said only in the system
# prompt, the opening line lost to a gateway's own hidden prompt (Claude via
# AnyModel stayed silent on every first turn); beside the task it held.
OPENING_NOTE = (
    "\n\nOPENING: the person has just handed you this task and sees nothing until you write. "
    "Your first response opens with your line to them, in response_language and in the voice "
    "your working style sets, before its first tool call."
)


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
    # Provider totals include hidden reasoning and tool-schema overhead. None
    # means the provider omitted usage, so the caller must estimate locally.
    input_tokens: int | None = None
    output_tokens: int | None = None


def tools_for_role(role_prompt: str) -> list[dict[str, Any]]:
    """Tool definitions for the actions the role prompt allows.

    The role prompt's ALLOWED ACTIONS line is already kept in sync with the
    parser by test_prompt_protocol_sync, so the reader gets no write tools.
    """
    match = _ALLOWED_ACTIONS_RE.search(role_prompt)
    if match is None:
        raise ValueError("role prompt has no ALLOWED ACTIONS section")
    names = [name.strip(" .") for name in match.group(1).split(",")]
    return [tool_definition(name) for name in names if name in ACTION_TOOLS]


# A call with an empty argument object was lost on the way at least one
# gateway (Claude through AnyModel): git_status arrived as a response with
# neither text nor a call, nine times in one run. The two tools with no
# argument of their own therefore ask for the fact the call should settle, which is also
# the constitution's test of whether a call earns its step; the runtime drops
# it before the action runs, so the protocol is unchanged.
_PURPOSE: dict[str, Any] = {
    "type": "string",
    "description": "One short phrase: what this call should settle.",
}
_PURPOSE_TOOLS = frozenset({"git_status", "git_diff"})


def tool_definition(name: str) -> dict[str, Any]:
    spec = ACTION_TOOLS[name]
    properties, required = spec.properties, spec.required
    if name in _PURPOSE_TOOLS:
        properties, required = {**properties, "purpose": _PURPOSE}, ("purpose",)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": spec.summary,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


def native_role_prompt(role_prompt: str, channel_section: str) -> str:
    """The role prompt with its text-mode protocol sections replaced by the tool channel.

    Left in, the <tool_call> block format and a page of its examples would
    contradict the tools on every call.
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
    return "".join(parts).rstrip() + "\n"


def calls_from_turn(turn: NativeTurn) -> list[ToolCallRequest]:
    """The turn's calls as the v2 contract's call requests, one error per bad call."""
    return [_call_request(call, number) for number, call in enumerate(turn.tool_calls, start=1)]


def _call_request(call: ToolCall, number: int) -> ToolCallRequest:
    try:
        value = json.loads(call.arguments or "{}")
    except json.JSONDecodeError as error:
        return ToolCallRequest(
            call.call_id, call.name, None, f"Tool call {number} was not run: arguments of {call.name} are not valid JSON ({error})."
        )
    if not isinstance(value, dict):
        return ToolCallRequest(
            call.call_id, call.name, None, f"Tool call {number} was not run: arguments of {call.name} must be a JSON object."
        )
    return ToolCallRequest(call.call_id, call.name, value)


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
