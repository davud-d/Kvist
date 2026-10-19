# -*- coding: utf-8 -*-
"""The privacy gate every piece of text passes before it is stored as memory.

A record outlives the conversation it came from and is shown to later tasks,
so the gate refuses rather than redacts: a record with a hole where the value
was would teach nothing, and a refusal tells the writer to describe where a
value comes from instead of the value. Rules cannot find every secret; when a
fragment only looks like one (a long random string) it is refused as well.

Health, intimate details and home addresses have no reliable textual form, so
the rules do not try; the prompts carry that principle.
"""
from __future__ import annotations

import re
from pathlib import Path

from kernel.change_journal import journal_path_violation
from kernel.memory.errors import MemoryPrivacyError, MemoryValidationError
from kernel.redaction import redact_text

_EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", re.UNICODE)
_HARMLESS_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "users.noreply.github.com", "anthropic.com")
_PHONE_RE = re.compile(r"(?<![\w+])\+\d[\d\s().-]{8,17}\d(?!\w)")
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_COORDINATES_RE = re.compile(r"(?<![\d.])-?\d{1,2}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}(?![\d.])")
_RANDOM_TOKEN_RE = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
_HEX_RE = re.compile(r"[0-9a-fA-F]+")
_KEY_HEADER_RE = re.compile(r"-----BEGIN [^-\n]*(?:PRIVATE KEY|KEY BLOCK)-----")


def check_text(text: str, field: str, *, flag_random_strings: bool = True) -> None:
    """Refuse text that holds a secret or personal data; the message names the kind, never the value.

    ``flag_random_strings`` is off for a skill's technical state, where cursors
    and cached encoded data are normal; a record meant for reading is never one.
    """
    kind = _sensitive_kind(text, flag_random_strings)
    if kind:
        raise MemoryPrivacyError(
            f"the {field} looks like it holds {kind}; memory never keeps such values. "
            "Describe where the value comes from (\"the key is read from OPENAI_API_KEY\"), not the value."
        )


def check_files(workspace: Path, files: list[str]) -> None:
    """The change journal's lexical gate: secret and .kvist paths are never named in memory either."""
    for path in files:
        problem = journal_path_violation(workspace, path)
        if problem:
            raise MemoryValidationError(f"file {path!r} cannot be referenced: {problem}")
        if not (workspace / path).exists():
            raise MemoryValidationError(f"file {path!r} does not exist in this workspace")


def _sensitive_kind(text: str, flag_random_strings: bool) -> str:
    # A key block cut off before its END line is not redacted, but it is still a key.
    if redact_text(text) != text or _KEY_HEADER_RE.search(text):
        return "a credential (a key, token, password or private key)"
    if any(not _harmless_email(match.group(1)) for match in _EMAIL_RE.finditer(text)):
        return "an e-mail address"
    if _PHONE_RE.search(text):
        return "a phone number"
    if any(_luhn_valid(re.sub(r"\D", "", match.group(0))) for match in _CARD_RE.finditer(text)):
        return "a payment card number"
    if any(_iban_valid(match.group(0)) for match in _IBAN_RE.finditer(text)):
        return "a bank account number"
    if _COORDINATES_RE.search(text):
        return "exact coordinates"
    if flag_random_strings and any(_looks_random(match.group(0)) for match in _RANDOM_TOKEN_RE.finditer(text)):
        return "a long random string that may be a secret"
    return ""


def _harmless_email(domain: str) -> bool:
    domain = domain.lower()
    return any(domain == known or domain.endswith("." + known) for known in _HARMLESS_EMAIL_DOMAINS)


def _luhn_valid(digits: str) -> bool:
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def _iban_valid(candidate: str) -> bool:
    rearranged = candidate[4:] + candidate[:4]
    number = "".join(str(int(char, 36)) for char in rearranged)
    return int(number) % 97 == 1


def _looks_random(token: str) -> bool:
    """Mixed-case letters with digits and no word shape; hex digests (commit ids, sha256) are fine."""
    if _HEX_RE.fullmatch(token):
        return False
    stripped = token.strip("=")
    if "/" in stripped or stripped.count("-") + stripped.count("_") > 3:
        return False
    has_upper = any(char.isupper() for char in stripped)
    has_lower = any(char.islower() for char in stripped)
    digits = sum(char.isdigit() for char in stripped)
    return has_upper and has_lower and digits >= 3
