# -*- coding: utf-8 -*-
"""OpenAI chat-completions shapes translated to Anthropic Messages shapes.

The agent speaks OpenAI (``tools``, assistant ``tool_calls``, ``role: tool``
results); the Anthropic adapter calls these helpers on the way out so the rest
of the kernel never sees the difference.
"""
from __future__ import annotations

import json
from typing import Any

from kernel.vision.vision import anthropic_content


def tools_to_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for tool in tools:
        function = tool["function"]
        entry: dict[str, Any] = {
            "name": function["name"],
            "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
        }
        if function.get("description"):
            entry["description"] = function["description"]
        converted.append(entry)
    return converted


def tool_choice_to_anthropic(choice: Any, *, thinking: bool) -> dict[str, Any] | None:
    """The OpenAI ``tool_choice`` as Anthropic's, or None to leave it to the default.

    Anthropic rejects a forced tool while extended thinking is on, so a forced
    choice degrades to ``auto`` there: the model may answer in prose, which the
    probe reads as "no native tools" rather than as a request error.
    """
    if not choice:
        return None
    if choice == "none":
        return {"type": "none"}
    if choice == "auto" or (thinking and choice != "none"):
        return {"type": "auto"}
    if choice == "required":
        return {"type": "any"}
    if isinstance(choice, dict) and choice.get("type") == "function":
        return {"type": "tool", "name": choice["function"]["name"]}
    return None


def _tool_input(arguments: Any) -> dict[str, Any]:
    """The decoded arguments of a recorded call.

    History keeps the model's raw argument text, including text that never
    parsed (the run answered it with an error). Anthropic wants an object, so
    an unparseable record is replayed as an empty one rather than failing the
    whole request.
    """
    if not arguments:
        return {}
    try:
        value = json.loads(arguments)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _assistant_blocks(item: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    text = item.get("content")
    if isinstance(text, str) and text:
        blocks.append({"type": "text", "text": text})
    elif isinstance(text, list):
        blocks.extend(anthropic_content(text))
    for call in item["tool_calls"]:
        function = call["function"]
        blocks.append(
            {
                "type": "tool_use",
                "id": call["id"],
                "name": function["name"],
                "input": _tool_input(function.get("arguments")),
            }
        )
    return blocks


def _is_results_message(message: dict[str, Any]) -> bool:
    content = message["content"]
    return (
        message["role"] == "user"
        and isinstance(content, list)
        and bool(content)
        and all(block.get("type") == "tool_result" for block in content)
    )


def history_to_anthropic(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Non-system chat messages as Anthropic messages.

    Results of parallel calls arrive as consecutive ``role: tool`` messages;
    Anthropic wants them together in one user message, right after the
    assistant turn that made the calls.
    """
    messages: list[dict[str, Any]] = []
    for item in items:
        role = str(item.get("role", "")).casefold()
        if role == "tool":
            result = {
                "type": "tool_result",
                "tool_use_id": item["tool_call_id"],
                "content": anthropic_content(item.get("content", "")),
            }
            if messages and _is_results_message(messages[-1]):
                messages[-1]["content"].append(result)
            else:
                messages.append({"role": "user", "content": [result]})
        elif role == "assistant" and item.get("tool_calls"):
            messages.append({"role": "assistant", "content": _assistant_blocks(item)})
        else:
            messages.append({"role": role, "content": anthropic_content(item.get("content", ""))})
    return messages
