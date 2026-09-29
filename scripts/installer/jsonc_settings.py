# -*- coding: utf-8 -*-
"""Edit one member of a Zed JSONC settings file without rewriting the rest.

Zed's ``settings.json`` is JSONC: it carries comments and trailing commas that a
plain ``json`` round-trip would delete. Nothing here re-serializes the document.
It locates the exact character range of the one member being written and replaces
only that range, so every comment, key order and indentation outside it survives.

Pure text surgery with no I/O: the caller reads and writes the file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

INDENT = "  "
_WORD = "-+.0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"


class JsoncError(ValueError):
    """The document cannot be edited safely; the caller reports this and stops."""


@dataclass(slots=True)
class _Token:
    kind: str  # "string" | "word" | "punct" | "comment"
    start: int
    end: int


@dataclass(slots=True)
class _Member:
    """One ``key: value`` pair inside an object, with the offsets around it."""

    key: str
    start: int  # first character of the key's string literal
    end: int  # one past the last character of the value
    value_start: int
    value_end: int
    comma_start: int | None = None  # the "," after the value, when present
    previous_comma_start: int | None = None  # the "," before the key, when present


def set_agent_server(text: str, name: str, entry_text: str) -> str:
    """Write ``agent_servers.<name>`` into a settings document.

    Inserts the member when it is absent and replaces only its value when it is
    present, so an entry the user edited by hand keeps its key and surroundings.

    :param text: the whole settings file.
    :param name: the agent entry name, e.g. ``"Kvist"``.
    :param entry_text: JSON text for the entry's value; must be a JSON object.
    :returns: the updated document.
    :raises JsoncError: when the document or the entry cannot be written safely.
    """
    entry = _validated_entry(entry_text)
    key_text = f"{json.dumps(name, ensure_ascii=False)}: "
    root_open, root_close = _root_object(text)
    servers = _find_member(text, root_open, root_close, "agent_servers")
    if servers is None:
        top = _member_indent(text, _object_members(text, _tokenize(text), root_open, root_close), root_open)
        inner = top + INDENT
        block = (
            f'"agent_servers": {{\n{inner}{key_text}'
            f"{_indent_block(entry, inner)}\n{top}}}"
        )
        return _insert_member(text, root_open, root_close, block)
    if not _is_object(text, servers):
        raise JsoncError(
            "'agent_servers' is not a JSON object in this settings file; "
            "fix it by hand before installing."
        )
    members = _object_members(text, _tokenize(text), servers.value_start, servers.value_end - 1)
    existing = next((item for item in members if item.key == name), None)
    if existing is not None:
        written = _indent_block(entry, _line_indent(text, existing.start))
        return text[: existing.value_start] + written + text[existing.value_end :]
    indent = _member_indent(text, members, servers.value_start)
    return _insert_member(
        text, servers.value_start, servers.value_end - 1, f"{key_text}{_indent_block(entry, indent)}"
    )


def remove_agent_server(text: str, name: str) -> str:
    """Delete ``agent_servers.<name>`` and nothing else.

    When that was the only entry, the now-empty ``agent_servers`` member goes too.

    :param text: the whole settings file.
    :param name: the agent entry name to remove.
    :returns: the updated document, unchanged when the entry is not there.
    :raises JsoncError: when the document cannot be read safely.
    """
    root_open, root_close = _root_object(text)
    servers = _find_member(text, root_open, root_close, "agent_servers")
    if servers is None:
        return text
    inner_open = servers.value_start
    existing = _find_member(text, inner_open, servers.value_end - 1, name)
    if existing is None:
        return text
    cleaned = _cut_member(text, existing)
    holder_open, holder_close = _object_span(cleaned, inner_open)
    if _object_members(cleaned, _tokenize(cleaned), holder_open, holder_close):
        return cleaned
    top_open, top_close = _root_object(cleaned)
    holder = _find_member(cleaned, top_open, top_close, "agent_servers")
    if holder is None:
        return cleaned
    return _cut_member(cleaned, holder)


def has_agent_server(text: str, name: str) -> bool:
    """Report whether ``agent_servers.<name>`` is already present.

    :param text: the whole settings file.
    :param name: the agent entry name.
    :returns: True when the entry exists.
    :raises JsoncError: when the document cannot be read safely.
    """
    root_open, root_close = _root_object(text)
    servers = _find_member(text, root_open, root_close, "agent_servers")
    if servers is None or not _is_object(text, servers):
        return False
    return _find_member(text, servers.value_start, servers.value_end - 1, name) is not None


def _validated_entry(entry_text: str) -> str:
    try:
        parsed = json.loads(entry_text)
    except json.JSONDecodeError as error:
        raise JsoncError(f"the generated entry is not valid JSON: {error.msg}") from error
    if not isinstance(parsed, dict):
        raise JsoncError("the generated entry must be a JSON object")
    return entry_text


# --- Tokenizer ---------------------------------------------------------------


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    size = len(text)
    while index < size:
        char = text[index]
        if char in " \t\r\n":
            index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            end = size if end < 0 else end
            tokens.append(_Token("comment", index, end))
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = size if end < 0 else end + 2
            tokens.append(_Token("comment", index, end))
            index = end
            continue
        if char == '"':
            end = _string_end(text, index)
            tokens.append(_Token("string", index, end))
            index = end
            continue
        if char in _WORD:
            end = index + 1
            while end < size and text[end] in _WORD:
                end += 1
            tokens.append(_Token("word", index, end))
            index = end
            continue
        tokens.append(_Token("punct", index, index + 1))
        index += 1
    return tokens


def _string_end(text: str, start: int) -> int:
    index = start + 1
    size = len(text)
    while index < size:
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return index + 1
        index += 1
    return size


# --- Object and member lookup ------------------------------------------------


def _root_object(text: str) -> tuple[int, int]:
    """Offsets of the document's root ``{`` and its matching ``}``."""
    if _only_trivia(text):
        raise JsoncError("the settings file has no JSON object to edit")
    tokens = _tokenize(text)
    for token in tokens:
        if token.kind != "punct":
            continue
        char = text[token.start]
        if char == "[":
            raise JsoncError("the settings file starts with an array, expected an object")
        if char == "{":
            return token.start, _matching_brace(tokens, token.start, text)
    raise JsoncError("the settings file has no JSON object to edit")


def _only_trivia(text: str) -> bool:
    return all(token.kind == "comment" for token in _tokenize(text))


def _matching_brace(tokens: list[_Token], open_index: int, text: str) -> int:
    depth = 0
    for token in tokens:
        if token.start < open_index or token.kind != "punct":
            continue
        char = text[token.start]
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                return token.start
    raise JsoncError("the settings file has an unclosed object")


def _object_span(text: str, open_index: int) -> tuple[int, int]:
    return open_index, _matching_brace(_tokenize(text), open_index, text)


def _is_object(text: str, member: _Member) -> bool:
    return text[member.value_start : member.value_start + 1] == "{"


def _find_member(text: str, open_index: int, close_index: int, key: str) -> _Member | None:
    for member in _object_members(text, _tokenize(text), open_index, close_index):
        if member.key == key:
            return member
    return None


def _object_members(
    text: str, tokens: list[_Token], open_index: int, close_index: int
) -> list[_Member]:
    members: list[_Member] = []
    depth = 0
    key_token: _Token | None = None
    value_start: int | None = None
    previous_comma: int | None = None
    for token in tokens:
        if token.start <= open_index or token.start >= close_index:
            continue
        if token.kind == "comment":
            continue
        char = text[token.start]
        if depth == 0 and key_token is None and value_start is None and token.kind == "string":
            if _next_code(tokens, token, close_index, text) == ":":
                key_token = token
                continue
        if token.kind == "punct" and char in "{[":
            if depth == 0 and key_token is not None and value_start is None:
                value_start = token.start
            depth += 1
            continue
        if token.kind == "punct" and char in "}]":
            depth -= 1
            if depth == 0 and key_token is not None and value_start is not None:
                members.append(_close_member(text, key_token, value_start, token.end, previous_comma))
                key_token = None
                value_start = None
            continue
        if depth > 0:
            continue
        if key_token is not None and value_start is None:
            if token.kind == "punct" and char == ":":
                continue
            if token.kind not in ("string", "word"):
                raise JsoncError(
                    f"the settings file has a key ({_quoted(text, key_token)}) with no value"
                )
            value_start = token.start
            members.append(_close_member(text, key_token, value_start, token.end, previous_comma))
            key_token = None
            value_start = None
            continue
        if token.kind == "punct" and char == ",":
            if key_token is not None:
                raise JsoncError(
                    f"the settings file has a key ({_quoted(text, key_token)}) with no value"
                )
            if members:
                members[-1].comma_start = token.start
            previous_comma = token.start
    return members


def _close_member(
    text: str, key_token: _Token, value_start: int, value_end: int, previous_comma: int | None
) -> _Member:
    return _Member(
        key=_quoted(text, key_token),
        start=key_token.start,
        end=value_end,
        value_start=value_start,
        value_end=value_end,
        previous_comma_start=previous_comma,
    )


def _quoted(text: str, token: _Token) -> str:
    try:
        return json.loads(text[token.start : token.end])
    except json.JSONDecodeError as error:
        raise JsoncError("the settings file has a key that is not a JSON string") from error


def _next_code(tokens: list[_Token], after: _Token, close_index: int, text: str) -> str:
    for token in tokens:
        if token.end <= after.end or token.start >= close_index or token.kind == "comment":
            continue
        return text[token.start]
    return ""


# --- Insert and cut ----------------------------------------------------------


def _insert_member(text: str, open_index: int, close_index: int, member_text: str) -> str:
    """Insert one ``key: value`` pair without disturbing anything already there."""
    members = _object_members(text, _tokenize(text), open_index, close_index)
    indent = _member_indent(text, members, open_index)
    if not members:
        outdent = _line_indent(text, close_index)
        return text[: open_index + 1] + f"\n{indent}{member_text}\n{outdent}" + text[open_index + 1 :]
    last = members[-1]
    after_comma = _comma_after(text, last, close_index)
    base = last.end if after_comma is None else after_comma
    trivia = _trivia_end(text, base, close_index)
    chunk = f"\n{indent}{member_text}"
    if after_comma is not None:
        return text[:trivia] + chunk + text[trivia:]
    return text[: last.end] + "," + text[last.end : trivia] + chunk + text[trivia:]


def _member_indent(text: str, members: list[_Member], open_index: int) -> str:
    """Indentation a new member of this object should use."""
    if members:
        sibling = _line_indent(text, members[0].start)
        if sibling:
            return sibling
    return _line_indent(text, open_index) + INDENT


def _indent_block(block: str, open_indent: str) -> str:
    """Re-indent a pretty-printed JSON block to sit under ``open_indent``.

    ``json.dumps(indent=2)`` already carries relative indentation, so every line
    after the first only needs the absolute indent of the key added to it.
    """
    lines = block.split("\n")
    return "\n".join([lines[0], *[open_indent + line for line in lines[1:]]])


def _trivia_end(text: str, base: int, close_index: int) -> int:
    """End of the comments on the previous member's own line.

    Inserting past them keeps a comment the user wrote about their own setting
    attached to it instead of making it look like it describes the new entry.
    """
    newline = text.find("\n", base, close_index)
    limit = close_index if newline < 0 else newline
    end = base
    for token in _tokenize(text[base:limit]):
        if token.kind != "comment":
            break
        end = base + token.end
    return end


def _comma_after(text: str, member: _Member, close_index: int) -> int | None:
    for token in _tokenize(text[member.end : close_index]):
        if token.kind == "comment":
            continue
        if token.kind == "punct" and text[member.end + token.start] == ",":
            return member.end + token.end
        return None
    return None


def _cut_member(text: str, member: _Member) -> str:
    """Remove one member plus the single comma that keeps the object valid.

    Whatever sits between the member and that comma stays: it is a comment the
    user wrote about something else.
    """
    start = _eat_back_to_line_start(text, member.start)
    if member.comma_start is not None:
        return text[:start] + text[member.end : member.comma_start] + text[member.comma_start + 1 :]
    if member.previous_comma_start is not None:
        comma = member.previous_comma_start
        return text[:comma] + text[comma + 1 : start] + text[member.end :]
    return text[:start] + text[member.end :]


def _eat_back_to_line_start(text: str, start: int) -> int:
    index = start
    while index > 0 and text[index - 1] in " \t":
        index -= 1
    if index > 0 and text[index - 1] == "\n":
        index -= 1
    return index


def _line_indent(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    prefix = text[start:offset]
    return prefix if prefix.strip() == "" else ""
