# -*- coding: utf-8 -*-
"""Headless one-shot model call for IDE editor actions on a selection.

The JetBrains editor-menu actions (rewrite, translate, explain and fix) build
their own prompts and pipe ``{"system": ..., "user": ...}`` to
``main.py --selection-task``; the reply text goes back to the IDE, which shows a
diff and applies it only after the person confirms.

Same shape as completion.py on purpose: no ``LocalAgent``, one direct fast-model
call, no ``.kvist/`` writes, and no ``ActionType`` or executor on this path. The
model can only return text, so a prompt injection hidden in the selected code
can change what text comes back and nothing else. The caller is responsible for
quoting the untrusted code inside the user prompt.
"""
from __future__ import annotations

import json

from kernel.agent_model import (
    _create_json_completion,
    _format_llm_error,
    safe_extract_completion_content,
)
from kernel.completion import CompletionError, _build_caller

# Defensive ceilings, independent of whatever cap the IDE already applied.
MAX_SYSTEM_CHARS = 20_000
MAX_USER_CHARS = 200_000


def parse_payload(raw_text: str) -> tuple[str, str]:
    try:
        payload = json.loads(raw_text or "null")
    except json.JSONDecodeError as error:
        raise CompletionError(f"Payload is not valid JSON: {error.msg}.") from error
    if not isinstance(payload, dict):
        raise CompletionError("Payload must be a JSON object.")

    system = payload.get("system")
    user = payload.get("user")
    if not isinstance(system, str) or not system.strip():
        raise CompletionError("Payload field 'system' must be a non-empty string.")
    if not isinstance(user, str) or not user.strip():
        raise CompletionError("Payload field 'user' must be a non-empty string.")
    return system[:MAX_SYSTEM_CHARS], user[:MAX_USER_CHARS]


def generate_selection_reply(payload_text: str) -> str:
    system, user = parse_payload(payload_text)
    caller, model = _build_caller()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    provider = caller.config.provider
    try:
        response = _create_json_completion(caller, model, messages, stream=False, json_mode=False)
        return safe_extract_completion_content(response, provider, model)
    # Provider SDKs share no exception base class; this is the same boundary
    # call_model draws, and the error is reported, not swallowed.
    except Exception as error:
        raise CompletionError(_format_llm_error(error, provider)) from error
