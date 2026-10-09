# -*- coding: utf-8 -*-
"""Headless inline-completion generation for IDE frontends.

Mirrors commit_message.py deliberately: no ``LocalAgent`` is built, one
direct fast-model call, no ``.kvist/`` writes. Unlike the action loop or
native tool calling, this path has no ``ActionType`` and no executor — the
model can only return a text continuation, never run a command or write a
file. That absence of capability is this feature's actual defense against
prompt injection hidden in the code being completed: even a successful
injection can only change what text comes back, and that text is inserted
into the editor as an ordinary suggestion the developer still has to accept.

Only imports that resolve to files inside the workspace are ever read or
sent to the model (see ``import_resolver.py``); third-party and stdlib
imports are named but never opened, which keeps their content off the wire
and out of the model's context.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from kernel.agent_model import (
    _format_llm_error,
    _load_role_prompt,
    safe_extract_completion_content,
    safe_extract_stream_delta,
)
from kernel.agent_state import AgentConfig
from kernel.api_settings import apply_api_settings
from kernel.change_journal import journal_path_violation
from kernel.import_resolver import ResolvedImport, extract_raw_imports, resolve_project_imports
from kernel.llm_clients import (
    FREEFLOW_PROVIDER,
    create_llm_client,
    extract_custom_provider_name,
    is_custom_provider,
    normalize_provider,
    provider_supports_streaming,
    required_secret_keys_for_provider,
    resolve_models_for_provider,
)
from kernel.reasoning_effort import is_reasoning_error, merge_reasoning_kwargs, strip_reasoning_kwargs
from kernel.redaction import defang_prompt_boundary_tags, redact_value
from kernel.user_config import (
    ensure_provider_credentials,
    get_custom_provider_by_name,
    get_saved_models,
    get_saved_provider_or_none,
    load_available_credentials,
)

# A completion is a short continuation, not a file: past this the model has
# almost certainly lost the plot rather than produced a legitimately long edit.
MAX_COMPLETION_CHARS = 2_000
# Imports live at the top of a file; scanning further only costs time on a
# file that will not benefit from the enrichment anyway.
IMPORT_SCAN_CHARS = 4_000
MAX_PREFIX_CHARS = 6_000
MAX_SUFFIX_CHARS = 1_500
# Defensive ceiling on the raw payload, independent of whatever cap the
# client already applied — the client's cap is never trusted on its own.
MAX_RAW_FIELD_CHARS = 200_000

_FENCE_RE = re.compile(r"^```[\w-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


class CompletionError(ValueError):
    pass


@dataclass(slots=True)
class CompletionRequest:
    file_path: str
    language: str
    prefix: str
    suffix: str


class _StderrUI:
    """Receives the completion helper's warnings; stdout carries the JSON reply."""

    verbose = False

    def print_error(self, message: str) -> None:
        print(message, file=sys.stderr)

    def debug_print(self, message: str) -> None:
        return None


@dataclass(slots=True)
class _ModelCaller:
    """Minimal configuration for the standalone completion request."""

    client: Any
    config: AgentConfig
    _ui: _StderrUI


def parse_payload(raw_text: str) -> CompletionRequest:
    try:
        payload = json.loads(raw_text or "null")
    except json.JSONDecodeError as error:
        raise CompletionError(f"Payload is not valid JSON: {error.msg}.") from error
    return request_from_payload(payload)


def request_from_payload(payload: object) -> CompletionRequest:
    if not isinstance(payload, dict):
        raise CompletionError("Payload must be a JSON object.")

    file_path = payload.get("file_path")
    language = payload.get("language")
    prefix = payload.get("prefix")
    suffix = payload.get("suffix")
    if not isinstance(file_path, str) or not file_path.strip():
        raise CompletionError("Payload field 'file_path' must be a non-empty string.")
    if not isinstance(language, str):
        raise CompletionError("Payload field 'language' must be a string.")
    if not isinstance(prefix, str) or not isinstance(suffix, str):
        raise CompletionError("Payload fields 'prefix' and 'suffix' must be strings.")

    return CompletionRequest(
        file_path=file_path.replace("\\", "/"),
        language=language.strip().lower(),
        prefix=prefix[-MAX_RAW_FIELD_CHARS:],
        suffix=suffix[:MAX_RAW_FIELD_CHARS],
    )


def resolve_workspace_for_completion(raw_path: str | None) -> Path:
    """Narrower than ``main.resolve_workspace_path``: a completion request
    always names an existing project directory. It must never default to
    the "Documents/Kvist Workspace" fallback or create a directory as a
    side effect of a keystroke."""
    if not raw_path or not raw_path.strip():
        raise CompletionError("A workspace path is required for --complete.")
    candidate = Path(raw_path).expanduser().resolve()
    if not candidate.is_dir():
        raise CompletionError("Workspace path must be an existing directory.")
    return candidate


def _resolve_target_file(workspace: Path, file_path: str) -> Path:
    if journal_path_violation(workspace, file_path) is not None:
        raise CompletionError("Refusing to complete inside a protected or invalid path.")
    resolved = (workspace / file_path).resolve()
    if not resolved.is_relative_to(workspace):
        raise CompletionError("Refusing to complete outside the workspace.")
    return resolved


def _wrap_untrusted(source: str, text: str) -> str:
    return (
        f'<UNTRUSTED_TOOL_OUTPUT source="{source}">\n'
        f"{defang_prompt_boundary_tags(text)}\n"
        "</UNTRUSTED_TOOL_OUTPUT>"
    )


def _render_imports_block(resolved: list[ResolvedImport]) -> str:
    if not resolved:
        return ""
    parts = [
        f"# {item.module} ({item.relative_path})\n" + "\n".join(item.signatures)
        for item in resolved
    ]
    return _wrap_untrusted("project_imports", "\n\n".join(parts))


def build_user_prompt(request: CompletionRequest, resolved_imports: list[ResolvedImport]) -> str:
    prefix = request.prefix[-MAX_PREFIX_CHARS:]
    suffix = request.suffix[:MAX_SUFFIX_CHARS]
    sections = [f"Language: {request.language or 'unknown'}", f"File: {request.file_path}"]
    imports_block = _render_imports_block(resolved_imports)
    if imports_block:
        sections.append(imports_block)
    sections.append(_wrap_untrusted("prefix", prefix))
    sections.append(_wrap_untrusted("suffix", suffix))
    return "\n\n".join(sections)


def _strip_echo(text: str, prefix: str) -> str:
    """Drop a leading echo of the prompt's own prefix or its last line.

    Some models restate a chunk of what they were given before continuing;
    without this the IDE would insert a visible duplicate of code already
    on screen.
    """
    tail = prefix[-80:]
    if tail and text.startswith(tail):
        return text[len(tail):]
    last_line = prefix.rsplit("\n", 1)[-1]
    if last_line and text.startswith(last_line):
        return text[len(last_line):]
    return text


def clean_model_output(raw: str, prefix: str) -> str:
    text = raw
    fenced = _FENCE_RE.match(text.strip())
    if fenced:
        text = fenced.group(1)
    text = _strip_echo(text, prefix)
    return text[:MAX_COMPLETION_CHARS]


def _resolve_provider() -> str:
    provider = os.getenv("AGENT_LLM_PROVIDER") or get_saved_provider_or_none()
    if not provider:
        raise CompletionError(
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


def completion_target() -> tuple[str, str]:
    """Provider and fast model that a completion request would use right now."""
    provider = _resolve_provider()
    models, _warnings = resolve_models_for_provider(provider, _saved_models(provider))
    model = models.get("fast", "")
    if not model:
        raise CompletionError(
            "No fast model is configured for this provider. Set one with /models."
        )
    return provider, model


def _build_caller() -> tuple[_ModelCaller, str]:
    provider, model = completion_target()

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
        reasoning_effort="false",
    )
    apply_api_settings(config)
    client = create_llm_client(provider=provider, credentials=credentials)
    return _ModelCaller(client=client, config=config, _ui=_StderrUI()), model


def _request_completion(caller: _ModelCaller, model: str, messages: list[dict], stream: bool) -> Any:
    # Completion has its own latency policy, independent of chat's /effort or env.
    # Omit sampling controls: reasoning-only models often reject temperature.
    kwargs: dict[str, Any] = {
        "model": model, "messages": redact_value(messages),
        "timeout": min(caller.config.api_timeout, 4.0),
    }
    if stream:
        kwargs["stream"] = True
    merge_reasoning_kwargs(kwargs, caller.config.provider, "false")
    if kwargs.get("reasoning_effort") == "minimal":
        kwargs["reasoning_effort"] = "none"
    while True:
        try:
            return caller.client.chat.completions.create(**kwargs)
        except Exception as error:
            if not is_reasoning_error(error):
                raise
            # A model may require reasoning. Try its lowest supported effort,
            # and only omit the control if the endpoint rejects it altogether.
            effort = kwargs.get("reasoning_effort")
            if effort in ("none", "minimal"):
                kwargs["reasoning_effort"] = "minimal" if effort == "none" else "low"
                continue
            stripped = strip_reasoning_kwargs(kwargs)
            if stripped == kwargs:
                raise
            kwargs = stripped


class CompletionStreamCleaner:
    """Applies ``clean_model_output`` to a reply that arrives in pieces.

    Cleaning is a function of the whole reply, but a leading echo of the prefix
    or an opening code fence is only recognisable once enough text has arrived,
    so output is held back while the reply could still turn out to be either.
    """

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._echo_starts = (prefix[-80:], prefix.rsplit("\n", 1)[-1])
        self._raw = ""
        self.emitted = 0

    def feed(self, piece: str) -> str:
        self._raw += piece
        if self._is_undecided():
            return ""
        return self._new_text()

    def finish(self) -> str:
        return self._new_text()

    def _is_undecided(self) -> bool:
        stripped = self._raw.lstrip()
        if stripped.startswith("```") or "```".startswith(stripped):
            return True
        return any(start.startswith(self._raw) for start in self._echo_starts if start)

    def _new_text(self) -> str:
        cleaned = clean_model_output(self._raw, self._prefix)
        fresh = cleaned[self.emitted:]
        self.emitted = len(cleaned)
        return fresh


def _stream_pieces(stream: Any) -> Iterator[str]:
    try:
        for chunk in stream:
            delta = safe_extract_stream_delta(chunk)
            piece = getattr(delta, "content", None) if delta is not None else None
            if piece:
                yield piece
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            close()


def prepare_completion(request: CompletionRequest, workspace: Path) -> list[dict]:
    target_file = _resolve_target_file(workspace, request.file_path)
    raw_imports = extract_raw_imports(request.prefix[:IMPORT_SCAN_CHARS], request.language)
    resolved_imports = resolve_project_imports(workspace, target_file, raw_imports, request.language)
    return [
        {"role": "system", "content": _load_role_prompt("complete")},
        {"role": "user", "content": build_user_prompt(request, resolved_imports)},
    ]


def iter_completion(
    caller: _ModelCaller,
    model: str,
    request: CompletionRequest,
    messages: list[dict],
    should_stop: Callable[[], bool] = lambda: False,
) -> Iterator[str]:
    """Yield the completion text in pieces as the model produces it.

    Providers without verified streaming answer with one piece. Stops early,
    closing the provider stream, once ``should_stop`` is true or the reply
    reaches ``MAX_COMPLETION_CHARS``.
    """
    provider = caller.config.provider
    streaming = provider_supports_streaming(provider)
    cleaner = CompletionStreamCleaner(request.prefix[-MAX_PREFIX_CHARS:])
    try:
        if should_stop():
            return
        response = _request_completion(caller, model, messages, streaming)
        if streaming:
            pieces = _stream_pieces(response)
        else:
            pieces = iter([safe_extract_completion_content(response, provider, model) or ""])
        for piece in pieces:
            fresh = cleaner.feed(piece)
            if fresh:
                yield fresh
            if should_stop() or cleaner.emitted >= MAX_COMPLETION_CHARS:
                return
        tail = cleaner.finish()
        if tail:
            yield tail
    # Provider SDKs share no exception base class; this is the same boundary
    # call_model draws, and the error is reported, not swallowed.
    except Exception as error:
        raise CompletionError(_format_llm_error(error, provider)) from error


def generate_completion(payload_text: str, workspace: Path) -> str:
    request = parse_payload(payload_text)
    messages = prepare_completion(request, workspace)
    caller, model = _build_caller()
    return "".join(iter_completion(caller, model, request, messages))
