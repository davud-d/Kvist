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
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterator

from kernel.agent.agent_model import (
    _format_llm_error,
    _load_role_prompt,
    safe_extract_completion_content,
    safe_extract_stream_delta,
)
from kernel.agent.agent_state import AgentConfig
from kernel.llm.api_settings import apply_api_settings
from kernel.session.change_journal import journal_path_violation
from kernel.context.import_resolver import ResolvedImport, extract_raw_imports, resolve_project_imports
from kernel.llm.llm_clients import (
    FREEFLOW_PROVIDER,
    create_llm_client,
    extract_custom_provider_name,
    is_custom_provider,
    normalize_provider,
    provider_supports_streaming,
    required_secret_keys_for_provider,
    resolve_models_for_provider,
)
from kernel.llm.reasoning_effort import is_reasoning_error, merge_reasoning_kwargs, strip_reasoning_kwargs
from kernel.sandbox.redaction import defang_prompt_boundary_tags, redact_value
from kernel.config.user_config import (
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
MAX_PREFIX_CHARS = 8_000
MAX_SUFFIX_CHARS = 4_000
# Defensive ceiling on the raw payload, independent of whatever cap the
# client already applied — the client's cap is never trusted on its own.
MAX_RAW_FIELD_CHARS = 200_000

# The reply that means "nothing sensible continues here". A bare empty message is
# not usable: models narrate instead ("I'm returning an empty continuation").
NO_COMPLETION = "NO_COMPLETION"
# DeepSeek's tool-call markup (``</｜DSML｜parameter>``) and chat-template tokens
# leak into the content when the model tries to call a tool it was not given.
# Plain ``<|`` is not matched: Haskell and Elixir use it as an operator.
_TOOL_MARKUP_RE = re.compile(
    r"</?｜|<\|(?:DSML|tool_call|im_start|im_end)|</?(?:UNTRUSTED_TOOL_OUTPUT|PROJECT_INSTRUCTIONS)"
)
# The tail of a possible tool-markup tag that has not fully arrived yet.
_PARTIAL_TAG_RE = re.compile(r"<[/|｜\w]{0,12}$")
_WORD_AT_END_RE = re.compile(r"[A-Za-z_]\w*$")
_WORD_AT_START_RE = re.compile(r"[A-Za-z_]\w*")
_MIN_REWRITE_WORD_CHARS = 4
_REWRITE_SIMILARITY = 0.8
# A replacement reply: the old text, then the new text (see complete_replace_prompt.txt).
_REPLACE_OPEN = "<<<REPLACE"
_REPLACE_WITH = "\n>>>WITH\n"
_REPLACE_END = "\n>>>END"
_INSERT_ONLY_RULE = "- Only insert text after the cursor."


class CompletionError(ValueError):
    pass


@dataclass(slots=True)
class CompletionRequest:
    file_path: str
    language: str
    prefix: str
    suffix: str
    # Only a client that can draw a deletion asks for replacements; the others
    # would paste the new text next to the old.
    edits: bool = False


@dataclass(slots=True)
class Replacement:
    """New text for the span from ``before`` units left of the cursor to ``after`` units right of it.

    Either may be negative: a span on the cursor's line that lies wholly to one side of it.
    Units are UTF-16 code units: the editors the offsets go to count that way.
    """

    before: int
    after: int
    text: str


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
        edits=payload.get("edits") is True,
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
    if journal_path_violation(workspace, resolved.relative_to(workspace).as_posix()) is not None:
        raise CompletionError("Refusing to complete inside a protected or invalid path.")
    path_parts = Path(file_path.replace("\\", "/")).parts + resolved.parts
    if any(part.casefold().startswith(".env") for part in path_parts):
        raise CompletionError("Refusing to complete environment files.")
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


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _describe_indent(indent: str) -> str:
    if not indent:
        return "no indentation"
    if "\t" in indent:
        return f"{len(indent)} tab{'s' if len(indent) != 1 else ''}"
    return f"{len(indent)} spaces"


def _indent_unit(lines: list[str]) -> str | None:
    """How the file indents one level, read off its own lines: tabs, or the smallest step in spaces."""
    indents = [_indent_of(line) for line in lines if line.strip()]
    if any(indent.startswith("\t") for indent in indents):
        return "a tab"
    widths = sorted({len(indent) for indent in indents if indent})
    if not widths:
        return None
    steps = [later - earlier for earlier, later in zip([0, *widths], widths)]
    return f"{min(steps)} spaces"


_LINE_COMMENT = {
    **dict.fromkeys(("python", "py", "shellscript", "sh", "bash", "ruby", "rb", "yaml", "yml", "toml", "perl", "r",
                     "makefile", "dockerfile", "powershell", "ps1"), "#"),
    **dict.fromkeys(("javascript", "js", "typescript", "ts", "javascriptreact", "jsx", "typescriptreact", "tsx",
                     "java", "kotlin", "kt", "c", "cpp", "cc", "h", "csharp", "cs", "go", "rust", "rs", "swift",
                     "scala", "dart", "php"), "//"),
    **dict.fromkeys(("sql", "lua", "haskell", "hs"), "--"),
}


def cursor_facts(prefix: str, suffix: str, language: str = "") -> str:
    """What the model cannot see in the text itself: where in its line the cursor is and how the code indents."""
    *above, typed = prefix.split("\n")
    after = rest_of_line(suffix)
    previous = next((line for line in reversed(above) if line.strip()), None)
    facts = []
    if typed.strip():
        facts.append("the cursor follows text already typed on its line")
    else:
        facts.append(f"the cursor's line has {_describe_indent(typed)} typed before the cursor")
    leader = _LINE_COMMENT.get(language)
    if leader and typed.lstrip().startswith(leader):
        facts.append(
            "the cursor's line is a comment: anything written here becomes part of the comment, so a reply "
            "can only continue its words on this line (or be NO_COMPLETION)"
        )
    if after.strip():
        facts.append("more text follows the cursor on the same line")
    if previous is not None:
        opens = previous.rstrip().endswith((":", "{", "(", "["))
        facts.append(
            f"the nearest non-empty line above is indented {_describe_indent(_indent_of(previous))}"
            + (" and opens a block" if opens else "")
        )
    unit = _indent_unit(prefix.split("\n")[-200:])
    if unit:
        facts.append(f"the file indents one level with {unit}")
    return "; ".join(facts) + "."


# Shows the model exactly where its reply lands; the character cannot be mistaken for code.
CURSOR_MARK = "⟦cursor⟧"
_CURSOR_LINE_SIDE = 120


def cursor_line(prefix: str, suffix: str) -> str:
    typed = prefix.rsplit("\n", 1)[-1][-_CURSOR_LINE_SIDE:]
    rest = suffix.split("\n", 1)[0][:_CURSOR_LINE_SIDE]
    return f"{typed}{CURSOR_MARK}{rest}"


def build_user_prompt(request: CompletionRequest, resolved_imports: list[ResolvedImport]) -> str:
    prefix = request.prefix[-MAX_PREFIX_CHARS:]
    suffix = request.suffix[:MAX_SUFFIX_CHARS]
    sections = [
        f"Language: {request.language or 'unknown'}",
        f"File: {request.file_path}",
        f"Cursor: {cursor_facts(prefix, suffix, request.language)}",
    ]
    imports_block = _render_imports_block(resolved_imports)
    if imports_block:
        sections.append(imports_block)
    sections.append(_wrap_untrusted("cursor_line", cursor_line(prefix, suffix)))
    sections.append(_wrap_untrusted("prefix", prefix))
    sections.append(_wrap_untrusted("suffix", suffix))
    return "\n\n".join(sections)


def _echo_candidates(prefix: str) -> tuple[tuple[str, bool], ...]:
    """Text a model may restate before its continuation, as ``(text, whole_line_above)``."""
    last_line = prefix.rsplit("\n", 1)[-1]
    candidates = [(prefix[-80:], False), (last_line, False)]
    if not last_line.strip():
        # Cursor on a fresh line: the model may repeat the whole line above it.
        above = next((line for line in reversed(prefix.splitlines()) if line.strip()), "")
        candidates.append((above, True))
    return tuple(candidate for candidate in candidates if candidate[0])


def _strip_echo(text: str, prefix: str) -> str:
    """Drop a leading echo of the prompt's own prefix, its last line, or the
    non-blank line above a cursor on a new line.

    Some models restate a chunk of what they were given before continuing;
    without this the IDE would insert a visible duplicate of code already
    on screen. A line above is only dropped when it is repeated whole: a longer
    line that merely starts like it is new code.
    """
    for candidate, whole_line_above in _echo_candidates(prefix):
        if not text.startswith(candidate):
            continue
        rest = text[len(candidate):]
        if whole_line_above and rest.strip():
            continue
        return rest
    return text


def _cut_tool_markup(text: str) -> str:
    match = _TOOL_MARKUP_RE.search(text)
    return text[:match.start()] if match else text


def _unfence(text: str) -> str:
    """Take the code out of a markdown fence the model added around it.

    The fence may be left open or followed by junk, so only the text up to the
    closing fence is kept.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    if "\n" not in stripped:
        return stripped.strip("`")
    body = stripped.partition("\n")[2]
    if body.startswith("```"):
        return ""
    closing = body.find("\n```")
    if closing != -1:
        return body[:closing]
    return body[:-3].rstrip("\n") if body.endswith("```") else body


def _unquote_inline(text: str) -> str:
    """A reply wrapped in single backticks, as chat models like to write code."""
    if text.count("`") == 2 and text.startswith("`") and text.endswith("`") and "\n" not in text:
        return text[1:-1]
    return text


def _strip_markup(raw: str, prefix: str) -> str:
    text = _strip_echo(_unfence(_cut_tool_markup(raw)), prefix)
    return _unquote_inline(text)[:MAX_COMPLETION_CHARS]


_OPENERS = {")": "(", "]": "[", "}": "{"}
_QUOTES = "\"'`"


def _unclosed_brackets(text: str) -> list[str]:
    stack: list[str] = []
    for char in text:
        if char in "([{":
            stack.append(char)
        elif char in _OPENERS and stack and stack[-1] == _OPENERS[char]:
            stack.pop()
    return stack


def _closes_what_text_opened(closers: str, text: str) -> bool:
    stack = _unclosed_brackets(text)
    for char in closers:
        if char in _OPENERS and stack and stack[-1] == _OPENERS[char]:
            return True
        if char in _QUOTES and text.count(char) % 2 == 1:
            return True
    return False


_HELD_AT_MOST = 40


def rest_of_line(suffix: str) -> str:
    return suffix.split("\n", 1)[0]


def _trim_suffix_overlap(text: str, suffix: str) -> str:
    """Drop the end of the reply that the file already has right after the cursor.

    A model that cannot tell a bracket, or the argument after the cursor, is already there writes
    it a second time. One closing character is enough to recognise; any other text needs two, so a
    chance match of one letter is left alone. A closer the reply opened itself is its own and stays.
    """
    rest = rest_of_line(suffix)
    for length in range(min(len(rest), len(text)), 0, -1):
        piece = rest[:length]
        if length == 1 and piece not in ")]}" + _QUOTES:
            continue
        if text.endswith(piece) and not _closes_what_text_opened(piece, text[:-length]):
            return text[:-length]
    return text


def _rewrites_word_before_cursor(text: str, prefix: str) -> bool:
    """True when the reply restates the word being typed in a corrected form.

    A completion can only insert after the cursor; a reply such as
    ``connection_method`` after ``connecthion_method`` would be pasted as a
    second, unrelated word.
    """
    before = _WORD_AT_END_RE.search(prefix)
    after = _WORD_AT_START_RE.match(text)
    if not before or not after:
        return False
    typed, replied = before.group(), after.group()
    if min(len(typed), len(replied)) < _MIN_REWRITE_WORD_CHARS:
        return False
    return SequenceMatcher(None, typed, replied).ratio() >= _REWRITE_SIMILARITY


def _repeats_what_is_typed(text: str, prefix: str) -> bool:
    """True when the reply is only the end of the line again, e.g. the last word of a comment just typed.

    One character is left alone: ``fo`` + ``o`` is a word being finished, not an echo. Closing brackets
    and quotes are left out too: a second ``)`` can close an outer call.
    """
    reply = text.strip()
    if len(reply) < 2 or "\n" in text or all(char in ")]}" + _QUOTES for char in reply):
        return False
    return prefix.rsplit("\n", 1)[-1].rstrip().endswith(reply)


def clean_model_output(raw: str, prefix: str, suffix: str = "") -> str:
    if NO_COMPLETION in raw:
        return ""
    if _replacement_body(raw) is not None:
        return ""
    text = _trim_suffix_overlap(_strip_markup(raw, prefix), suffix)
    # Only blanks is no suggestion: it would show as an empty ghost that Tab accepts.
    if not text.strip() or _repeats_what_is_typed(text, prefix) or _rewrites_word_before_cursor(text, prefix):
        return ""
    return text


def _replacement_body(raw: str) -> str | None:
    body = _unfence(_cut_tool_markup(raw)).lstrip()
    return body if body.startswith(_REPLACE_OPEN) else None


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def parse_replacement(raw: str, request: CompletionRequest) -> Replacement | str | None:
    """The replacement a reply asks for, or None when it is not one.

    The old text is the model's quotation; it is only believed where it really
    stands on the cursor's line (the nearest place, when it occurs twice), which
    also fixes how far it reaches to each side of the cursor.

    A reply that keeps everything left of the cursor does not delete it: the model has
    retyped the line to add to it, which is a continuation, or a replacement of the
    right side only.
    """
    body = _replacement_body(raw)
    if body is None or NO_COMPLETION in raw:
        return None
    old, with_marker, rest = body[len(_REPLACE_OPEN):].removeprefix("\n").partition(_REPLACE_WITH)
    if not with_marker:
        return None
    new = _cut_tool_markup(rest.partition(_REPLACE_END)[0])
    # Editors take a replaced range on one line; the new text may have many.
    if not old or "\n" in old or old == new or len(new) > MAX_COMPLETION_CHARS:
        return None
    left = request.prefix.rsplit("\n", 1)[-1]
    line = left + request.suffix.split("\n", 1)[0]
    start = _nearest_occurrence(line, old, len(left))
    if start is None:
        return None
    end = start + len(old)
    if start <= len(left) <= end:
        return _keep_typed_text(old, new, len(left) - start)
    return Replacement(_signed_utf16(line, start, len(left)), _signed_utf16(line, len(left), end), new)


def _nearest_occurrence(line: str, old: str, cursor: int) -> int | None:
    best: tuple[int, int] | None = None
    start = line.find(old)
    while start != -1:
        distance = max(start - cursor, cursor - (start + len(old)), 0)
        if best is None or distance < best[0]:
            best = (distance, start)
        start = line.find(old, start + 1)
    return None if best is None else best[1]


def _signed_utf16(text: str, a: int, b: int) -> int:
    """UTF-16 units from ``a`` to ``b``, negative when ``b`` lies before ``a``."""
    return _utf16_len(text[a:b]) if a <= b else -_utf16_len(text[b:a])


def _keep_typed_text(old: str, new: str, split: int) -> Replacement | str:
    typed, rest = old[:split], old[split:]
    if not typed or not new.startswith(typed):
        return Replacement(_utf16_len(typed), _utf16_len(rest), new)
    if not rest:
        return new[split:]
    return Replacement(0, _utf16_len(rest), new[split:])


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


# Names for "as little reasoning as the model allows", lowest first, tried in turn on a rejection.
# "instant" is how Inception's Mercury spells what OpenAI calls "none".
_EFFORT_LADDER = ("none", "minimal", "instant", "low")


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
            if effort in _EFFORT_LADDER[:-1]:
                kwargs["reasoning_effort"] = _EFFORT_LADDER[_EFFORT_LADDER.index(effort) + 1]
                continue
            stripped = strip_reasoning_kwargs(kwargs)
            if stripped == kwargs:
                raise
            kwargs = stripped


class CompletionStreamCleaner:
    """Applies ``clean_model_output`` to a reply that arrives in pieces.

    Cleaning is a function of the whole reply, but a leading echo of the prefix,
    an opening code fence, a half-arrived tool-markup tag or the first word
    (which may rewrite the word before the cursor) is only recognisable once
    enough text has arrived, so output is held back while the reply could still
    turn out to be one of them.
    """

    def __init__(self, prefix: str, suffix: str = "") -> None:
        self._prefix = prefix
        self._suffix = suffix
        # What the reply may still turn out to repeat from the file is not shown until it ends.
        self._held = min(len(rest_of_line(suffix)), _HELD_AT_MOST)
        self._echo_starts = tuple(text for text, _ in _echo_candidates(prefix))
        self._raw = ""
        self.emitted = 0

    @property
    def raw(self) -> str:
        return self._raw

    def feed(self, piece: str) -> str:
        self._raw += piece
        if self._is_undecided():
            return ""
        return self._new_text(final=False)

    def finish(self) -> str:
        return self._new_text(final=True)

    def _is_undecided(self) -> bool:
        stripped = self._raw.lstrip()
        if stripped.startswith("```") or "```".startswith(stripped):
            return True
        if _REPLACE_OPEN.startswith(stripped) or _replacement_body(self._raw) is not None:
            return True
        if any(start.startswith(self._raw) for start in self._echo_starts):
            return True
        # The first word is not known until a character that ends it arrives.
        return self.emitted == 0 and _WORD_AT_START_RE.fullmatch(_strip_markup(self._raw, self._prefix)) is not None

    def _new_text(self, final: bool) -> str:
        cleaned = clean_model_output(self._raw, self._prefix, self._suffix)
        if not final:
            cleaned = cleaned[: max(len(cleaned) - self._held, 0)]
            partial_tag = _PARTIAL_TAG_RE.search(cleaned)
            if partial_tag:
                cleaned = cleaned[:partial_tag.start()]
        fresh = cleaned[self.emitted:]
        self.emitted = max(self.emitted, len(cleaned))
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


def _system_prompt(request: CompletionRequest) -> str:
    base = _load_role_prompt("complete")
    if not request.edits:
        return base
    # The base rule forbids touching the word before the cursor; the replacement section lifts it,
    # and a model shown both follows the stricter one.
    kept = [line for line in base.splitlines() if not line.startswith(_INSERT_ONLY_RULE)]
    return "\n".join(kept) + "\n\n" + _load_role_prompt("complete_replace")


def prepare_completion(request: CompletionRequest, workspace: Path) -> list[dict]:
    target_file = _resolve_target_file(workspace, request.file_path)
    raw_imports = extract_raw_imports(request.prefix[:IMPORT_SCAN_CHARS], request.language)
    resolved_imports = resolve_project_imports(workspace, target_file, raw_imports, request.language)
    return [
        {"role": "system", "content": _system_prompt(request)},
        {"role": "user", "content": build_user_prompt(request, resolved_imports)},
    ]


def iter_completion(
    caller: _ModelCaller,
    model: str,
    request: CompletionRequest,
    messages: list[dict],
    should_stop: Callable[[], bool] = lambda: False,
) -> Iterator[str | Replacement]:
    """Yield the completion text in pieces as the model produces it.

    Providers without verified streaming answer with one piece. Stops early,
    closing the provider stream, once ``should_stop`` is true or the reply
    reaches ``MAX_COMPLETION_CHARS``. A replacement is only known once the
    whole reply is in, so it comes as a single last item.
    """
    provider = caller.config.provider
    streaming = provider_supports_streaming(provider)
    cleaner = CompletionStreamCleaner(request.prefix[-MAX_PREFIX_CHARS:], request.suffix[:MAX_SUFFIX_CHARS])
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
            if should_stop() or cleaner.emitted >= MAX_COMPLETION_CHARS or len(cleaner.raw) >= 2 * MAX_COMPLETION_CHARS:
                return
        replacement = parse_replacement(cleaner.raw, request) if request.edits else None
        if replacement:
            yield replacement
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
    return "".join(piece for piece in iter_completion(caller, model, request, messages) if isinstance(piece, str))
