# -*- coding: utf-8 -*-
"""Local bug-report bundler for beta testers.

Assembles one plain-text file describing the running agent (version,
environment, session config) plus the active session's last events, scrubs it
for credentials, and saves it under ``~/.kvist/bug-reports/``. The report is
never sent anywhere: it stays on disk until the user shares it themselves.

Security is the point of this module, not an afterthought: it never reads
``.env``, process environment variables, or the key-bearing user config file
(``~/.kvist/config.json``) — only the few config fields the active
``AgentConfig`` already exposes (provider/model names, role, effort, plan
state), which carry no secrets. Every tool-output field pulled from the
session journal is capped at ``MAX_EVENT_FIELD_CHARS`` characters and the
fully assembled report is run through :func:`redact_text` before it ever
touches disk.
"""
from __future__ import annotations

import json
import platform
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from agent_logging import get_journal_path
from durable_json import JsonStorageError, append_private_bytes
from redaction import REDACTED, redact_text, redact_value
from session_events import read_history
from version import kvist_version

# The last 50 events are enough to see what the agent was doing without
# turning the report itself into a second copy of the whole journal.
MAX_EVENTS = 50
# Per-string cap applied to each field of a journal event before it is
# rendered, so one runaway command output cannot dominate the report.
MAX_EVENT_FIELD_CHARS = 300
_REPORTS_DIRNAME = "bug-reports"

# --- Bug-report-only redaction --------------------------------------------
#
# A second pass, layered on top of the shared redact_text(), for shapes that
# are specific to what a beta tester's report tends to contain (a custom
# provider's own env var name, a token pasted bare into a command's output)
# but that redact_text() cannot know by name without becoming too aggressive
# for every other caller. This pass exists only here; redaction.py is
# untouched.
#
# 1. Any identifier containing "key"/"token"/"secret"/"pass" (any case, any
#    position in the name) assigned a value of 12+ characters via `=` or `:`.
_BUG_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b[A-Za-z][A-Za-z0-9_]*(?:key|token|secret|pass)[A-Za-z0-9_]*"
    r"\s*[:=]\s*(?P<value>[^\s,;\"']{12,})"
)
# 2. A bare run of letters/digits/_/- of 32+ characters, mixing letters and
#    digits. Excluded: a pure-hex run up to 40 characters (a git commit
#    hash) and a token that is itself one segment of a file path.
_BUG_BARE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])")
_HEX_DIGITS_RE = re.compile(r"\A[0-9a-fA-F]+\Z")


def _replace_assignment_value(match: re.Match[str]) -> str:
    prefix_len = match.start("value") - match.start()
    return match.group(0)[:prefix_len] + REDACTED


def _is_git_commit_hash(token: str) -> bool:
    return len(token) <= 40 and _HEX_DIGITS_RE.fullmatch(token) is not None


def _is_path_component(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return before in "/\\" or after in "/\\"


def _is_mixed_alnum(token: str) -> bool:
    return any(character.isalpha() for character in token) and any(
        character.isdigit() for character in token
    )


def _redact_bug_report_extra(text: str) -> str:
    """Bug-report-only pass: named-secret assignments, then bare tokens."""
    redacted = _BUG_SENSITIVE_ASSIGNMENT_RE.sub(_replace_assignment_value, text)

    def _replace_bare_token(match: re.Match[str]) -> str:
        token = match.group(0)
        if _is_git_commit_hash(token):
            return token
        if _is_path_component(redacted, match.start(), match.end()):
            return token
        if not _is_mixed_alnum(token):
            return token
        return REDACTED

    return _BUG_BARE_TOKEN_RE.sub(_replace_bare_token, redacted)


def _node_version() -> str:
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "not found"
    return result.stdout.strip() or "not found"


def _environment_lines() -> list[str]:
    return [
        f"Kvist version: {kvist_version()}",
        f"OS: {platform.platform()}",
        f"Python: {platform.python_version()}",
        f"Node (TS TUI): {_node_version()}",
    ]


def _agent_lines(agent, *, frontend: str, routed_role: str | None) -> list[str]:
    config = agent.config
    return [
        f"Frontend: {frontend}",
        f"Provider: {config.provider}",
        f"Model (fast): {config.fast_model}",
        f"Model (smart): {config.smart_model}",
        f"Role: {routed_role or 'auto'}",
        f"Effort: {config.reasoning_effort}",
        f"Session: {agent.current_session}",
    ]


def _render_event(entry: dict[str, Any]) -> str:
    """One capped, secret-scrubbed line for a session-journal event."""
    safe_event = redact_value(entry.get("event"), max_str_len=MAX_EVENT_FIELD_CHARS)
    return json.dumps(
        {"seq": entry.get("seq"), "turn": entry.get("turn"), "event": safe_event},
        ensure_ascii=False,
    )


def _recent_events(agent) -> list[dict[str, Any]]:
    try:
        journal_path = get_journal_path(agent, agent.current_session)
        history = read_history(journal_path, agent.executor.workspace, limit=MAX_EVENTS)
    except (JsonStorageError, OSError, ValueError):
        return []
    return history["events"]


def build_bug_report(
    agent,
    *,
    frontend: str,
    routed_role: str | None = None,
    user_text: str = "",
) -> str:
    """Assemble the full report text, already scrubbed for credentials."""
    sections: list[str] = []
    stripped_text = user_text.strip()
    if stripped_text:
        sections.append("== User report ==\n" + stripped_text)

    sections.append("== Environment ==\n" + "\n".join(_environment_lines()))
    sections.append(
        "== Agent state ==\n"
        + "\n".join(_agent_lines(agent, frontend=frontend, routed_role=routed_role))
    )

    events = _recent_events(agent)
    event_lines = [_render_event(entry) for entry in events]
    sections.append(
        f"== Last {len(event_lines)} session events (session {agent.current_session}) ==\n"
        + ("\n".join(event_lines) if event_lines else "(no events recorded)")
    )

    report = "\n\n".join(sections) + "\n"
    # Final pass over the whole assembled text: the environment/agent-state
    # lines above are not built through redact_value, so this is the one place
    # that is guaranteed to see every character that reaches the file. The
    # bug-report-only pass runs second, on top of the shared filter, to catch
    # what redact_text() does not know by name.
    return _redact_bug_report_extra(redact_text(report))


def _report_filename(session_name: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_session = session_name or "none"
    return f"bug-{stamp}-{safe_session}.txt"


def save_bug_report(
    agent,
    *,
    frontend: str,
    routed_role: str | None = None,
    user_text: str = "",
    home: Path | None = None,
) -> Path:
    """Build the report and save it under ``~/.kvist/bug-reports/``; return its path."""
    report = build_bug_report(
        agent, frontend=frontend, routed_role=routed_role, user_text=user_text
    )
    home = (home or Path.home()).resolve(strict=True)
    path = home / ".kvist" / _REPORTS_DIRNAME / _report_filename(agent.current_session)
    append_private_bytes(path, report.encode("utf-8"), boundary=home)
    return path
