"""Explicit, redacted Kvist configuration synchronization to remote bridges."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from kernel.user_config import load_user_config, save_user_config


_SECRET_WORDS = ("api_key", "apikey", "password", "secret", "token")
_LOCAL_ONLY = {"web_local", "chatgpt_oauth"}


def _is_secret(path: tuple[str, ...]) -> bool:
    return any(word in path[-1].casefold() for word in _SECRET_WORDS)


def _walk(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, (*path, str(key)))
    elif path:
        yield path, value


def sync_preview() -> dict[str, Any]:
    config = load_user_config()
    credentials = []
    for path, value in _walk(config):
        if path[0] in _LOCAL_ONLY:
            continue
        if _is_secret(path):
            credentials.append(
                {
                    "id": ".".join(path),
                    "label": " / ".join(part.replace("_", " ").title() for part in path),
                    "present": isinstance(value, str) and bool(value.strip()),
                }
            )
    return {"general": True, "credentials": credentials}


def build_sync_payload(selected: list[str]) -> dict[str, Any]:
    config = load_user_config()
    selected_set = set(selected)
    general: dict[str, Any] = {}
    secrets: dict[str, Any] = {}
    for path, value in _walk(config):
        if path[0] in _LOCAL_ONLY:
            continue
        target = secrets if _is_secret(path) else general
        if target is secrets and ".".join(path) not in selected_set:
            continue
        _set_path(target, path, deepcopy(value))
    return {"general": general, "secrets": secrets}


def apply_sync_payload(payload: dict[str, Any]) -> dict[str, Any]:
    general = payload.get("general")
    secrets = payload.get("secrets")
    if not isinstance(general, dict) or not isinstance(secrets, dict):
        raise ValueError("Configuration sync payload is invalid.")
    current = load_user_config()
    _merge(current, general)
    _merge(current, secrets)
    save_user_config(current)
    return {"ok": True, "credential_fields": sum(1 for _ in _walk(secrets))}


def _set_path(target: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cursor = target
    for part in path[:-1]:
        child = cursor.setdefault(part, {})
        if not isinstance(child, dict):
            child = {}
            cursor[part] = child
        cursor = child
    cursor[path[-1]] = value


def _merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if key in _LOCAL_ONLY:
            continue
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = deepcopy(value)
