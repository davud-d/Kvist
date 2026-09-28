# -*- coding: utf-8 -*-
"""Headless commit-message generation for IDE frontends.

The JetBrains plugin pipes the changes selected in its commit dialog to
``main.py --commit-message`` and puts the answer into the message field. The
provider, model and keys are the user's saved Kvist configuration, but no
``LocalAgent`` is built: constructing one reads and writes ``.kvist/`` session
state and ``build_agent`` seeds ``KVIST.md`` and ``.gitignore`` — changes that
would land in the very commit being described.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_model import (
    _create_json_completion,
    _format_llm_error,
    _load_role_prompt,
    init_tiktoken_encoding,
    safe_extract_completion_content,
)
from agent_state import AgentConfig
from api_settings import apply_api_settings
from change_journal import journal_path_violation
from context_budget import derived_prompt_budget, env_prompt_budget, learned_budget_for
from llm_clients import (
    FREEFLOW_PROVIDER,
    create_llm_client,
    extract_custom_provider_name,
    is_custom_provider,
    normalize_provider,
    required_secret_keys_for_provider,
    resolve_models_for_provider,
)
from reasoning_effort import normalize_effort
from redaction import defang_prompt_boundary_tags
from user_config import (
    ensure_provider_credentials,
    get_custom_provider_by_name,
    get_saved_models,
    get_saved_provider_or_none,
    get_saved_reasoning_effort,
    load_available_credentials,
)

MAX_SUBJECT_CHARS = 72
MAX_RECENT_SUBJECTS = 15
# A commit message needs the shape of a change, not all of it; past this the
# extra diff only adds cost and latency on large refactors.
MAX_DIFF_TOKENS = 30_000
# Room left for the recent-commit block, the block markup and the reply.
_PROMPT_OVERHEAD_TOKENS = 2_000

_FENCE_RE = re.compile(r"^```[\w-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)
_TRAILER_RE = re.compile(r"^(co-authored-by|signed-off-by|change-id)\s*:", re.IGNORECASE)
_QUOTE_PAIRS = (('"', '"'), ("'", "'"), ("“", "”"))


class CommitMessageError(ValueError):
    pass


@dataclass(slots=True)
class FilePatch:
    path: str
    patch: str


class _StderrUI:
    """Receives the completion helper's warnings; stdout carries the JSON reply."""

    verbose = False

    def print_error(self, message: str) -> None:
        print(message, file=sys.stderr)

    def debug_print(self, message: str) -> None:
        return None


@dataclass(slots=True)
class _ModelCaller:
    """The three attributes ``_create_json_completion`` reads from an agent."""

    client: Any
    config: AgentConfig
    _ui: _StderrUI


def parse_payload(raw_text: str) -> tuple[list[FilePatch], list[str]]:
    try:
        payload = json.loads(raw_text or "null")
    except json.JSONDecodeError as error:
        raise CommitMessageError(f"Payload is not valid JSON: {error.msg}.") from error
    if not isinstance(payload, dict):
        raise CommitMessageError("Payload must be a JSON object.")

    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise CommitMessageError("Payload field 'files' must be a list.")
    files = [_parse_file(item) for item in raw_files]

    raw_subjects = payload.get("recent_subjects", [])
    if not isinstance(raw_subjects, list):
        raise CommitMessageError("Payload field 'recent_subjects' must be a list.")
    subjects = [str(item).strip() for item in raw_subjects if str(item).strip()]
    return files, subjects[:MAX_RECENT_SUBJECTS]


def _parse_file(item: object) -> FilePatch:
    if not isinstance(item, dict):
        raise CommitMessageError("Every entry in 'files' must be an object.")
    path = item.get("path")
    patch = item.get("patch")
    if not isinstance(path, str) or not isinstance(patch, str):
        raise CommitMessageError("Every entry in 'files' needs string 'path' and 'patch'.")
    return FilePatch(path=path.replace("\\", "/"), patch=patch)


def drop_protected_files(files: list[FilePatch]) -> list[FilePatch]:
    """Keep secret files and Kvist's own state out of the prompt entirely.

    The executor refuses to read these, so a diff must not become the way
    around that refusal.
    """
    return [
        item
        for item in files
        if item.patch.strip() and journal_path_violation(Path("."), item.path) is None
    ]


_DIFF_FILE_RE = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")


def split_unified_diff(diff_text: str) -> list[FilePatch]:
    """Split a unified diff into one :class:`FilePatch` per changed file.

    Per-file splitting is what lets :func:`drop_protected_files` remove a
    secret before it reaches the model: a whole-repo diff as one blob would
    carry ``.env`` content past that check.

    :param diff_text: ``git diff`` output, possibly empty.
    :returns: one entry per file section, in diff order.
    """
    files: list[FilePatch] = []
    current_path: str | None = None
    current_lines: list[str] = []
    for line in diff_text.splitlines():
        header = _DIFF_FILE_RE.match(line)
        if header is not None:
            if current_path is not None:
                files.append(FilePatch(path=current_path, patch="\n".join(current_lines)))
            current_path = header.group("b")
            current_lines = [line]
            continue
        if current_path is not None:
            current_lines.append(line)
    if current_path is not None:
        files.append(FilePatch(path=current_path, patch="\n".join(current_lines)))
    return files


def _cut_to_tokens(text: str, max_tokens: int, encoding: Any) -> str:
    if encoding is None:
        return text[: max_tokens * 4]
    tokens = encoding.encode(text, disallowed_special=())
    return encoding.decode(tokens[:max_tokens])


def _count_tokens(text: str, encoding: Any) -> int:
    if encoding is None:
        return len(text) // 4
    return len(encoding.encode(text, disallowed_special=()))


def fit_patches(
    files: list[FilePatch], token_budget: int, encoding: Any
) -> tuple[str, bool]:
    """Join patches in order until *token_budget* is spent.

    Returns the diff text and whether anything was cut. Files that no longer
    fit are still named, so the model knows the change is wider than it sees.
    """
    parts: list[str] = []
    omitted: list[str] = []
    remaining = token_budget
    truncated = False
    for item in files:
        if truncated:
            omitted.append(item.path)
            continue
        cost = _count_tokens(item.patch, encoding)
        if cost <= remaining:
            parts.append(item.patch)
            remaining -= cost
            continue
        parts.append(_cut_to_tokens(item.patch, remaining, encoding) + "\n[... patch cut]")
        truncated = True
    if omitted:
        parts.append("[Files changed but not shown: " + ", ".join(omitted) + "]")
    return "\n".join(parts), truncated


def build_user_prompt(diff_text: str, truncated: bool, subjects: list[str]) -> str:
    history = "\n".join(subjects) if subjects else "(no commits yet)"
    return (
        '<UNTRUSTED_TOOL_OUTPUT source="recent_commits">\n'
        f"{defang_prompt_boundary_tags(history)}\n"
        "</UNTRUSTED_TOOL_OUTPUT>\n\n"
        f'<UNTRUSTED_TOOL_OUTPUT source="git_diff" truncated="{str(truncated).lower()}">\n'
        f"{defang_prompt_boundary_tags(diff_text)}\n"
        "</UNTRUSTED_TOOL_OUTPUT>"
    )


def clean_model_output(raw: str) -> str:
    """Strip the wrapping a model adds despite the prompt, then enforce the shape."""
    text = raw.strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    for opening, closing in _QUOTE_PAIRS:
        if len(text) > 1 and text.startswith(opening) and text.endswith(closing):
            text = text[1:-1].strip()

    lines = [line.rstrip() for line in text.splitlines()]
    while lines and (not lines[-1] or _TRAILER_RE.match(lines[-1])):
        lines.pop()
    message = "\n".join(lines).strip()
    if not message:
        raise CommitMessageError("The model returned an empty commit message.")

    subject = message.splitlines()[0]
    if len(subject) > MAX_SUBJECT_CHARS:
        raise CommitMessageError(
            f"The model returned a {len(subject)}-character subject "
            f"(limit {MAX_SUBJECT_CHARS}). Try again or pick another model."
        )
    return message


def _resolve_provider() -> str:
    provider = os.getenv("AGENT_LLM_PROVIDER") or get_saved_provider_or_none()
    if not provider:
        raise CommitMessageError(
            "No provider is configured. Run Kvist once in a terminal to choose one."
        )
    return normalize_provider(provider)


def _saved_models(provider: str) -> dict[str, str]:
    if not is_custom_provider(provider):
        return get_saved_models(provider)
    info = get_custom_provider_by_name(extract_custom_provider_name(provider)) or {}
    models = info.get("models")
    return models if isinstance(models, dict) else {}


def _no_interactive_secret(_label: str) -> str:
    # Nobody can answer a prompt here; an empty answer makes
    # ensure_provider_credentials raise its "X is required" error.
    return ""


def _build_caller() -> tuple[_ModelCaller, str]:
    provider = _resolve_provider()
    models, _warnings = resolve_models_for_provider(provider, _saved_models(provider))
    model = models.get("fast", "")
    if not model:
        raise CommitMessageError(
            "No fast model is configured for this provider. Set one with /models."
        )

    credentials = ensure_provider_credentials(
        provider=provider,
        validation_model=model,
        required_keys=required_secret_keys_for_provider(provider, [model]),
        skip_validation=True,
        prompt_secret_fn=_no_interactive_secret,
    )
    if provider == FREEFLOW_PROVIDER:
        credentials.update(load_available_credentials(("gemini_api_key", "groq_api_key")))

    config = AgentConfig(
        provider=provider,
        fast_model=model,
        smart_model=model,
        persist_logs=False,
        stream=False,
        reasoning_effort=_resolve_reasoning_effort(),
    )
    apply_api_settings(config)
    client = create_llm_client(provider=provider, credentials=credentials)
    return _ModelCaller(client=client, config=config, _ui=_StderrUI()), model


def _resolve_reasoning_effort() -> str:
    override = os.getenv("AGENT_REASONING_EFFORT")
    if override and override.strip():
        return normalize_effort(override)
    return normalize_effort(get_saved_reasoning_effort())


def _diff_token_budget(config: AgentConfig, model: str, system_tokens: int) -> int:
    model_budget = (
        env_prompt_budget()
        or config.max_context_tokens
        or derived_prompt_budget(config.provider, model)
    )
    learned = learned_budget_for(model)
    if learned is not None:
        model_budget = min(model_budget, learned)
    available = model_budget - system_tokens - _PROMPT_OVERHEAD_TOKENS
    return max(1, min(MAX_DIFF_TOKENS, available))


def generate_commit_message(payload_text: str) -> str:
    files, subjects = parse_payload(payload_text)
    files = drop_protected_files(files)
    if not files:
        raise CommitMessageError("Nothing to describe: no changed files with a diff.")

    caller, model = _build_caller()
    system_prompt = _load_role_prompt("commit")
    encoding = init_tiktoken_encoding()
    budget = _diff_token_budget(
        caller.config, model, _count_tokens(system_prompt, encoding)
    )
    diff_text, truncated = fit_patches(files, budget, encoding)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": build_user_prompt(diff_text, truncated, subjects)},
    ]
    provider = caller.config.provider
    try:
        response = _create_json_completion(
            caller, model, messages, stream=False, json_mode=False
        )
        raw = safe_extract_completion_content(response, provider, model)
    # Provider SDKs share no exception base class; this is the same boundary
    # call_model draws, and the error is reported, not swallowed.
    except Exception as error:
        raise CommitMessageError(_format_llm_error(error, provider)) from error
    return clean_model_output(raw)
