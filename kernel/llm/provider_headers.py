# -*- coding: utf-8 -*-
"""Extra HTTP headers that a provider's gateway demands on every request.

Some gateways route or cache by a header (OpenCode Go wants ``x-opencode-session``) and refuse a
request without it. The person can set such headers with ``/headers``, and Kvist sets one itself
when a gateway names the missing header in its refusal (see :func:`demanded_session_header`).
They live in the user config under ``provider_headers``, keyed by provider, and ride on each
request, so a change needs no restart and the main agent, helpers and classifier share them.

A value may contain ``{session}``: it becomes the id of the conversation that makes the request.
"""
from __future__ import annotations

import re

from kernel.config.user_config import (
    CUSTOM_PROVIDERS_KEY,
    _update_user_config,
    load_user_config,
)

PROVIDER_HEADERS_KEY = "provider_headers"
SESSION_PLACEHOLDER = "{session}"

_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
_FORBIDDEN_IN_VALUE = re.compile(r"[\x00-\x1f\x7f]")

# A gateway that cannot route a request says which header is missing. Only names that are an id of the
# conversation are filled in by Kvist: for any other header there is no value it could honestly invent.
_ID_HEADER = r"x-(?:[a-z0-9]+-)*(?:session|conversation|thread)[a-z0-9-]*"
_DEMANDED = re.compile(
    rf"(?:missing|requires?|required)\W+(?:the\W+)?(?:header\W+)?({_ID_HEADER})"
    rf"|({_ID_HEADER})\W+header\W+(?:is\W+)?(?:missing|required)",
    re.IGNORECASE,
)

USAGE = (
    "Usage: /headers | /headers set <Name> <value> | /headers remove <Name> | /headers clear. "
    "{session} in a value stands for this conversation's id. / "
    "Использование: /headers | /headers set <Имя> <значение> | /headers remove <Имя> | /headers clear. "
    "{session} в значении — id этого разговора."
)


def demanded_session_header(error: Exception) -> str | None:
    """The conversation-id header a refusal says is missing, or ``None`` when it names none."""
    match = _DEMANDED.search(str(error))
    if match is None:
        return None
    return (match.group(1) or match.group(2)).lower()


def configured_headers(provider: str) -> dict[str, str]:
    """Every header set for ``provider``: those of a custom provider's own entry, then ``/headers`` ones."""
    from kernel.llm.llm_clients import normalize_provider

    key = normalize_provider(provider)
    payload = load_user_config()
    headers: dict[str, str] = {}
    custom = payload.get(CUSTOM_PROVIDERS_KEY)
    if key.startswith("custom:") and isinstance(custom, dict):
        entry = custom.get(key.removeprefix("custom:"))
        if isinstance(entry, dict):
            headers.update(_clean(entry.get("headers")))
    stored = payload.get(PROVIDER_HEADERS_KEY)
    if isinstance(stored, dict):
        headers.update(_clean(stored.get(key)))
    return headers


def set_header(provider: str, name: str, value: str) -> None:
    """Remember ``name: value`` for ``provider``; a name already there (in any case) is replaced."""
    name, value = name.strip(), value.strip()
    if not _NAME.match(name):
        raise ValueError(f"Not a valid header name: {name!r} / Недопустимое имя заголовка: {name!r}")
    if not value or _FORBIDDEN_IN_VALUE.search(value):
        raise ValueError("A header value must be one non-empty line. / Значение заголовка — одна непустая строка.")

    def change(payload: dict[str, object]) -> dict[str, object]:
        headers = _own(payload, provider)
        for existing in [known for known in headers if known.lower() == name.lower()]:
            del headers[existing]
        headers[name] = value
        return payload

    _update_user_config(change)


def remove_header(provider: str, name: str) -> bool:
    """Forget a header of ``provider``; whether there was one."""
    removed = False

    def change(payload: dict[str, object]) -> dict[str, object]:
        nonlocal removed
        headers = _own(payload, provider)
        for existing in [known for known in headers if known.lower() == name.strip().lower()]:
            del headers[existing]
            removed = True
        return payload

    _update_user_config(change)
    return removed


def clear_headers(provider: str) -> int:
    """Forget every ``/headers`` header of ``provider``; how many there were."""
    count = 0

    def change(payload: dict[str, object]) -> dict[str, object]:
        nonlocal count
        headers = _own(payload, provider)
        count = len(headers)
        headers.clear()
        return payload

    _update_user_config(change)
    return count


def headers_command(provider: str, args: list[str]) -> tuple[bool, str]:
    """``/headers`` for the active provider; shared by every frontend. Values are never echoed, they may be secrets."""
    action = args[0].lower() if args else "list"
    if action == "list" and len(args) <= 1:
        return True, _listing(provider)
    if action == "set" and len(args) >= 3:
        try:
            set_header(provider, args[1], " ".join(args[2:]))
        except ValueError as error:
            return False, str(error)
        return True, f"Header {args[1]} saved for {provider}. / Заголовок {args[1]} сохранён для {provider}."
    if action == "remove" and len(args) == 2:
        if remove_header(provider, args[1]):
            return True, f"Header {args[1]} removed. / Заголовок {args[1]} удалён."
        return False, f"{provider} has no /headers header {args[1]}. / У {provider} нет заголовка {args[1]}."
    if action == "clear" and len(args) == 1:
        count = clear_headers(provider)
        return True, f"Removed {count} header(s). / Удалено заголовков: {count}."
    return False, USAGE


def _listing(provider: str) -> str:
    headers = configured_headers(provider)
    if not headers:
        return f"No extra headers for {provider}. / Для {provider} дополнительных заголовков нет."
    rows = [
        f"- {name}: {'the conversation id / id разговора' if value == SESSION_PLACEHOLDER else 'set / задан'}"
        for name, value in headers.items()
    ]
    return f"Extra headers for {provider} / Дополнительные заголовки {provider}:\n" + "\n".join(rows)


def _own(payload: dict[str, object], provider: str) -> dict[str, str]:
    """The ``/headers`` store of ``provider`` inside ``payload``, created on the way."""
    from kernel.llm.llm_clients import normalize_provider

    stored = payload.get(PROVIDER_HEADERS_KEY)
    if not isinstance(stored, dict):
        stored = {}
        payload[PROVIDER_HEADERS_KEY] = stored
    key = normalize_provider(provider)
    headers = stored.get(key)
    if not isinstance(headers, dict):
        headers = {}
        stored[key] = headers
    return headers


def _clean(headers: object) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    return {
        name.strip(): value.strip()
        for name, value in headers.items()
        if isinstance(name, str) and isinstance(value, str) and name.strip() and value.strip()
    }
