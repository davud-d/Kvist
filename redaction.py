"""Central secret redaction for persistence, diagnostics, UI, and model input."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from typing import Any

REDACTED = "[REDACTED]"

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_AUTH_HEADER_RE = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization)\s*[:=]\s*"
    r"(?:(?:basic|bearer|token)\s+)?[A-Za-z0-9._~+/=-]{8,}"
)
_COOKIE_HEADER_RE = re.compile(
    r"(?i)\b(?:cookie|set-cookie)\s*:\s*"
    r"[^ \t\r\n\"'{}=;]+=[^ \t\r\n\"'{};]+"
    r"(?:\s*;\s*[^ \t\r\n\"'{};]+)*"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_KNOWN_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:"
    r"sk-(?:proj-|svcacct-|ant-|or-v1-)?[A-Za-z0-9_-]{16,}"
    r"|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|gsk_[A-Za-z0-9_-]{16,}"
    r"|hf_[A-Za-z0-9]{20,}"
    r"|key-[A-Za-z0-9_-]{20,}"
    r"|AIza[A-Za-z0-9_-]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|gh[opusr]_[A-Za-z0-9]{20,}"
    r"|glpat-[A-Za-z0-9_-]{16,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|(?:AKIA|ASIA)[0-9A-Z]{16}"
    r"|npm_[A-Za-z0-9]{20,}"
    r"|pypi-[A-Za-z0-9_-]{20,}"
    r")(?=$|[^A-Za-z0-9_-])",
)
_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
)
_URL_PASSWORD_RE = re.compile(
    r"(?P<prefix>[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:)"
    r"[^@\s/]+(?=@)"
)
_SENSITIVE_KEY_SUFFIXES = (
    "apikey",
    "accesskey",
    "accesskeyid",
    "secretaccesskey",
    "accesstoken",
    "authtoken",
    "refreshtoken",
    "clientsecret",
    "privatekey",
    "password",
    "passwd",
    "credential",
    "credentials",
    "secret",
    "token",
    "proxyauthorization",
    "authorization",
    "setcookie",
    "cookie",
)
_ASSIGNMENT_KEY_RE = re.compile(
    r"[A-Za-z0-9_.-]+",
    re.ASCII,
)
_ASSIGNMENT_TRIGGER_FRAGMENTS = (
    "auth",
    "cookie",
    "credential",
    "key",
    "passwd",
    "password",
    "secret",
    "token",
)
_KNOWN_TOKEN_PREFIXES = (
    "sk-",
    "sk_",
    "rk_",
    "gsk_",
    "hf_",
    "key-",
    "AIza",
    "github_pat_",
    "gho_",
    "ghp_",
    "ghr_",
    "ghs_",
    "ghu_",
    "glpat-",
    "xoxb-",
    "xoxa-",
    "xoxp-",
    "xoxr-",
    "xoxs-",
    "AKIA",
    "ASIA",
    "npm_",
    "pypi-",
)

_DANGEROUS_UNICODE_FORMAT_CONTROLS = frozenset({
    "\u061c",  # Arabic letter mark
    "\u200b",  # zero-width space
    "\u200e",  # left-to-right mark
    "\u200f",  # right-to-left mark
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",  # bidi embeddings/overrides
    "\u2060", "\u2061", "\u2062", "\u2063", "\u2064",  # invisible operators/joiner
    "\u2066", "\u2067", "\u2068", "\u2069",  # bidi isolates
    "\ufeff",  # byte-order mark / zero-width no-break space
})


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    return any(normalized.endswith(suffix) for suffix in _SENSITIVE_KEY_SUFFIXES)


_NON_SECRET_SCALARS = {
    "false",
    "none",
    "null",
    "true",
    "undefined",
}
_PLACEHOLDER_VALUES = {
    "api_key",
    "apikey",
    "auth_token",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
    "your_api_key",
    "your_password",
    "your_secret",
    "your_token",
}


def _looks_like_placeholder(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in _PLACEHOLDER_VALUES:
        return True
    return (
        normalized.startswith(("${", "{{", "<"))
        or normalized.endswith(("}}", ">"))
        or normalized.startswith(("example_", "placeholder_"))
    )


def _looks_like_unquoted_secret(
    value: str,
    *,
    key: str,
    separator: str,
) -> bool:
    stripped = value.strip()
    lowered = stripped.casefold()
    normalized_key = key.strip("\"'").casefold()
    if stripped == REDACTED:
        return False
    if lowered == normalized_key:
        return False
    if lowered in _NON_SECRET_SCALARS or _looks_like_placeholder(stripped):
        return False
    if stripped.isdecimal():
        return False
    if stripped.startswith(("$", "%", "{", "<")):
        return False
    if any(character in stripped for character in "()[]{}"):
        return False
    if "." in stripped and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", stripped):
        return False
    if (
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stripped)
        and any(character.isspace() for character in separator)
        and (
            len(stripped) < 16
            or "_" in stripped
        )
    ):
        return False
    return len(stripped) >= 8


def _redact_sensitive_assignments(text: str) -> str:
    """Redact assignment/CLI values without full-text backtracking regexes."""
    replacements: list[tuple[int, int]] = []
    covered_until = 0
    text_length = len(text)

    for key_start, key_end in _iter_sensitive_assignment_keys(text):
        if key_start < covered_until:
            continue
        key = text[key_start:key_end]

        cursor = key_end
        if cursor < text_length and text[cursor] in "\"'":
            cursor += 1
        separator_start = cursor
        while cursor < text_length and text[cursor].isspace():
            cursor += 1

        if cursor < text_length and text[cursor] in ":=":
            cursor += 1
            while cursor < text_length and text[cursor].isspace():
                cursor += 1
            value_start = cursor
            value_end = _assignment_value_end(text, value_start)
            if value_end is not None:
                raw_value = text[value_start:value_end]
                if raw_value[:1] in "\"'":
                    value = raw_value[1:-1]
                    if value != REDACTED and not _looks_like_placeholder(value):
                        replacements.append((value_start + 1, value_end - 1))
                        covered_until = value_end
                elif _looks_like_unquoted_secret(
                    raw_value,
                    key=key,
                    separator=text[separator_start:value_start],
                ):
                    replacements.append((value_start, value_end))
                    covered_until = value_end
            continue

        dash_count = len(key) - len(key.lstrip("-"))
        if (
            dash_count < 1
            or (key_start > 0 and not text[key_start - 1].isspace())
            or cursor >= text_length
            or cursor <= separator_start
        ):
            continue
        value_start = cursor
        while cursor < text_length and not text[cursor].isspace():
            cursor += 1
        if cursor > value_start:
            replacements.append((value_start, cursor))
            covered_until = cursor

    if not replacements:
        return text
    pieces: list[str] = []
    cursor = 0
    for start, end in replacements:
        if start < cursor:
            continue
        pieces.extend((text[cursor:start], REDACTED))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _iter_sensitive_assignment_keys(
    text: str,
) -> Iterator[tuple[int, int]]:
    """Yield sensitive ASCII key tokens, skipping unrelated lines cheaply."""
    line_start = 0
    text_length = len(text)
    while line_start < text_length:
        newline = text.find("\n", line_start)
        line_end = text_length if newline < 0 else newline + 1
        folded_line = text[line_start:line_end].casefold()
        if any(
            fragment in folded_line
            for fragment in _ASSIGNMENT_TRIGGER_FRAGMENTS
        ):
            for match in _ASSIGNMENT_KEY_RE.finditer(
                text,
                line_start,
                line_end,
            ):
                if _is_sensitive_key(match.group(0)):
                    yield match.span()
        line_start = line_end


def _assignment_value_end(text: str, start: int) -> int | None:
    if start >= len(text):
        return None
    quote = text[start] if text[start] in "\"'" else ""
    if quote:
        cursor = start + 1
        while cursor < len(text) and text[cursor] not in "\r\n\"'":
            cursor += 1
        if (
            cursor >= len(text)
            or text[cursor] != quote
            or cursor - (start + 1) < 4
        ):
            return None
        return cursor + 1

    cursor = start
    while (
        cursor < len(text)
        and not text[cursor].isspace()
        and text[cursor] not in ",;}]&"
    ):
        cursor += 1
    return cursor if cursor - start >= 4 else None


def redact_text(text: str) -> str:
    """Return ``text`` with common credential forms replaced."""
    folded = text.casefold()
    has_assignment_name = any(
        fragment in folded
        for fragment in _ASSIGNMENT_TRIGGER_FRAGMENTS
    )
    has_known_token = any(prefix in text for prefix in _KNOWN_TOKEN_PREFIXES)

    redacted = text
    if "private key" in folded:
        redacted = _PRIVATE_KEY_RE.sub(REDACTED, redacted)
    if "authorization" in folded:
        redacted = _AUTH_HEADER_RE.sub(
            f"Authorization: {REDACTED}",
            redacted,
        )
    if "cookie" in folded:
        redacted = _COOKIE_HEADER_RE.sub(f"Cookie: {REDACTED}", redacted)
    if "bearer" in folded:
        redacted = _BEARER_RE.sub(f"Bearer {REDACTED}", redacted)
    if "://" in text and ":" in text and "@" in text:
        redacted = _URL_PASSWORD_RE.sub(
            lambda match: match.group("prefix") + REDACTED,
            redacted,
        )
    if has_known_token:
        redacted = _KNOWN_TOKEN_RE.sub(REDACTED, redacted)
    if "eyJ" in text and text.count(".") >= 2:
        redacted = _JWT_RE.sub(REDACTED, redacted)
    if has_assignment_name:
        redacted = _redact_sensitive_assignments(redacted)
    return redacted


def escape_terminal_controls(
    text: str,
    *,
    preserve_ansi_escape: bool = False,
) -> str:
    """Make terminal motion and Unicode formatting controls visible."""
    escaped: list[str] = []
    for character in text:
        if character in {"\n", "\t"}:
            escaped.append(character)
            continue
        if preserve_ansi_escape and character == "\x1b":
            escaped.append(character)
            continue
        if (
            unicodedata.category(character) != "Cc"
            and character not in _DANGEROUS_UNICODE_FORMAT_CONTROLS
        ):
            escaped.append(character)
            continue
        codepoint = ord(character)
        if codepoint <= 0xFF:
            escaped.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            escaped.append(f"\\u{codepoint:04x}")
        else:
            escaped.append(f"\\U{codepoint:08x}")
    return "".join(escaped)


def sanitize_display_text(text: str) -> str:
    """Redact credentials and neutralize terminal presentation controls."""
    return escape_terminal_controls(redact_text(text))


def redact_value(value: Any, max_str_len: int | None = None) -> Any:
    """Recursively redact a JSON-like value, including sensitive dict keys."""
    return _redact_value(value, max_str_len, set())


def _redact_value(
    value: Any,
    max_str_len: int | None,
    active_containers: set[int],
) -> Any:
    if isinstance(value, str):
        sanitized = redact_text(value)
        if max_str_len is not None and len(sanitized) > max_str_len:
            return sanitized[:max_str_len] + "...[TRUNCATED]"
        return sanitized
    if isinstance(value, bytes):
        return "[BINARY DATA]"
    if isinstance(value, dict):
        identity = id(value)
        if identity in active_containers:
            return "[CYCLIC DATA]"
        active_containers.add(identity)
        try:
            result: dict[Any, Any] = {}
            for key, item in value.items():
                safe_key = redact_text(key) if isinstance(key, str) else key
                if safe_key in result and safe_key != key:
                    suffix = 2
                    candidate = f"{safe_key}#{suffix}"
                    while candidate in result:
                        suffix += 1
                        candidate = f"{safe_key}#{suffix}"
                    safe_key = candidate
                result[safe_key] = (
                    REDACTED
                    if isinstance(key, str) and _is_sensitive_key(key)
                    else _redact_value(item, max_str_len, active_containers)
                )
            return result
        finally:
            active_containers.remove(identity)
    if isinstance(value, (list, tuple, set)):
        identity = id(value)
        if identity in active_containers:
            return "[CYCLIC DATA]"
        active_containers.add(identity)
        try:
            items = list(value)
            return [
                _redact_value(item, max_str_len, active_containers)
                for item in items
            ]
        finally:
            active_containers.remove(identity)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    try:
        return redact_text(str(value))
    except Exception:
        return f"[{type(value).__name__} DATA]"
