# -*- coding: utf-8 -*-
"""Project-scoped command permission policy (allow / deny / ask).

The policy lives in ``<workspace>/.kvist/permissions.json``:

    {
        "allow": ["git status", "python *"],
      "deny":  ["curl *"],
      "ask":   ["npm run"],
      "host":  ["git push *"]
    }

Pattern semantics: a pattern is a sequence of whitespace-separated tokens
matched against the command prefix, one token at a time, with shell-style
globbing per token (``fnmatch``). A trailing ``*`` matches any remaining
arguments (zero or more); a pattern without it still matches commands that
have extra arguments after the matched prefix, so ``git push`` covers
``git push origin main``.

``allow`` and ``host`` patterns must start with the command's literal name: a
bare ``*`` (or ``py*``) never matches, so one typo cannot switch approvals off
for everything. ``deny`` and ``ask`` keep full globbing, since a broader match
there only asks or refuses more.

``host`` patterns name commands that always run outside the sandbox, with the
person's real HOME and no file masking, without the per-run host dialog. They
also count as ``allow``: the person wrote the rule, so the command needs no
second approval (an intrinsic DANGEROUS command still asks).

Resolution order (enforced by the terminal layer, not here): project ``deny``
→ built-in hard blocks → intrinsic ``DANGEROUS`` → project ``ask`` →
executable-origin/fallback approval → eligible ``allow`` → default risk.
``deny``, hard blocks, and per-execution danger can never be overridden by
``allow``.

The model cannot edit this file: SafeExecutor forbids every access to the
internal ``.kvist`` directory.
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from kernel.session.durable_json import (
    JsonStorageCorruptError,
    JsonStorageError,
    read_json_object,
    update_json_object,
)

POLICY_RELATIVE_PATH = ".kvist/permissions.json"
RULE_LISTS = ("allow", "deny", "ask", "host")
_STORED_LISTS = (*RULE_LISTS, "web_domains")
_WILDCARD_CHARS = "*?["


@dataclass(frozen=True, slots=True)
class PermissionPolicy:
    """Immutable snapshot of the project's allow/deny/ask rules."""

    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()
    host: tuple[str, ...] = ()
    # Hosts the person chose "always allow" for in the fetch_url prompt.
    web_domains: tuple[str, ...] = ()

    def decide(self, command: list[str]) -> str | None:
        """Return "deny" | "ask" | "allow" | None for a single command.

        deny wins over ask, ask wins over allow: an explicit request for
        confirmation must not be silenced by a broader allow pattern.
        """
        if any(_matches(pattern, command) for pattern in self.deny):
            return "deny"
        if any(_matches(pattern, command) for pattern in self.ask):
            return "ask"
        if any(grants(pattern, command) for pattern in (*self.allow, *self.host)):
            return "allow"
        return None

    def runs_on_host(self, command: list[str]) -> bool:
        return any(grants(pattern, command) for pattern in self.host)


def _matches(pattern: str, command: list[str]) -> bool:
    tokens = pattern.split()
    if not tokens or not command:
        return False
    for index, pattern_token in enumerate(tokens):
        if pattern_token == "*" and index == len(tokens) - 1:
            return True
        if index >= len(command):
            return False
        if not fnmatch(command[index], pattern_token):
            return False
    return True


def grants(pattern: str, command: list[str]) -> bool:
    """``_matches`` for the lists that lift a check: the command name must be written out."""
    return pattern_is_specific(pattern) and _matches(pattern, command)


def pattern_is_specific(pattern: str) -> bool:
    tokens = pattern.split()
    return bool(tokens) and not any(char in tokens[0] for char in _WILDCARD_CHARS)


def policy_file_path(workspace: Path) -> Path:
    return workspace / POLICY_RELATIVE_PATH


def load_policy(workspace: Path) -> tuple[PermissionPolicy, str]:
    """Load the policy; a missing file means an empty policy.

    Returns ``(policy, note)``: ``note`` is non-empty when the file exists
    but is unusable — the policy is then empty (fail closed to the default
    risk gradation) and the note must be surfaced to the user.
    """
    try:
        path = _validated_policy_path(workspace)
    except ValueError as error:
        return PermissionPolicy(), f"Cannot use {POLICY_RELATIVE_PATH}: {error}"
    try:
        data = read_json_object(
            path,
            boundary=workspace.resolve(),
            empty_as_missing=True,
        )
    except JsonStorageCorruptError as error:
        return PermissionPolicy(), f"Invalid JSON in {POLICY_RELATIVE_PATH}: {error}"
    except JsonStorageError as error:
        return PermissionPolicy(), f"Cannot read {POLICY_RELATIVE_PATH}: {error}"

    parsed: dict[str, tuple[str, ...]] = {}
    for key in _STORED_LISTS:
        rules, error = _parse_rule_list(data.get(key, []), key)
        if error:
            return PermissionPolicy(), f"Invalid {POLICY_RELATIVE_PATH}: {error}"
        parsed[key] = rules

    return PermissionPolicy(**parsed), ""


def _parse_rule_list(value: object, key: str) -> tuple[tuple[str, ...], str]:
    if not isinstance(value, list):
        return (), f"field '{key}' must be an array of non-empty strings"
    if any(not isinstance(item, str) or not item.strip() for item in value):
        return (), f"field '{key}' must contain only non-empty strings"
    return tuple(item.strip() for item in value), ""


def append_allow_rule(workspace: Path, pattern: str) -> None:
    """Persist an allow pattern ("always allow in this repo")."""
    _append_rule(workspace, "allow", pattern)


def append_rule(workspace: Path, key: str, pattern: str) -> None:
    """Persist ``pattern`` in one of the rule lists (``/permissions``)."""
    if key not in RULE_LISTS:
        raise ValueError(f"unknown rule list '{key}'")
    _append_rule(workspace, key, pattern)


def remove_rule(workspace: Path, pattern: str, only: str | None = None) -> list[str]:
    """Drop ``pattern`` from every list holding it (or just ``only``); returns the lists it was in."""
    path = _validated_policy_path(workspace)
    removed: list[str] = []

    def drop(loaded: dict[str, object]) -> dict[str, object]:
        data = _normalized(loaded)
        for key in (only,) if only else RULE_LISTS:
            target = data[key]
            assert isinstance(target, list)
            if pattern in target:
                target.remove(pattern)
                removed.append(key)
        return data

    _update_policy(workspace, path, drop)
    return removed


def append_web_domain(workspace: Path, host: str) -> None:
    """Persist a host the person chose "always allow" for in the fetch_url prompt."""
    _append_rule(workspace, "web_domains", host)


def _append_rule(workspace: Path, key: str, value: str) -> None:
    path = _validated_policy_path(workspace)

    def add(loaded: dict[str, object]) -> dict[str, object]:
        data = _normalized(loaded)
        target = data[key]
        assert isinstance(target, list)
        if value not in target:
            target.append(value)
        return data

    _update_policy(workspace, path, add)


def _normalized(loaded: dict[str, object]) -> dict[str, object]:
    data: dict[str, object] = dict(loaded)
    for list_key in _STORED_LISTS:
        rules, error = _parse_rule_list(loaded.get(list_key, []), list_key)
        if error:
            raise ValueError(error)
        data[list_key] = list(rules)
    return data


def _update_policy(workspace: Path, path: Path, change) -> None:
    try:
        update_json_object(
            path,
            change,
            boundary=workspace.resolve(),
            empty_as_missing=True,
        )
    except JsonStorageError as error:
        raise ValueError(f"Cannot update {POLICY_RELATIVE_PATH} safely") from error


def _validated_policy_path(workspace: Path) -> Path:
    root = workspace.resolve()
    path = policy_file_path(root)
    current = root
    for part in Path(POLICY_RELATIVE_PATH).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symbolic links are forbidden in {POLICY_RELATIVE_PATH}")
    try:
        path.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as error:
        raise ValueError(f"{POLICY_RELATIVE_PATH} resolves outside the workspace") from error
    return path
