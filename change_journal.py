# -*- coding: utf-8 -*-
"""Workspace-global record of the file content that existed before the agent's
first write to each path, so agent changes can be reverted on request.

The journal belongs to the workspace, not to a named session: every session
shares one working tree, so workspace rollback is workspace-scoped. Each entry
carries the session that first touched the path purely as attribution for the
/changes report.

Only the pre-change ("baseline") content is kept, not every intermediate
edit: a full revert only ever needs to know what a file looked like before
the agent touched it. Every write additionally refreshes ``last_digest`` — a
hash of the content the agent last wrote — so revert can tell agent-owned
content apart from later edits made by the user or by shell commands, and
refuses to destroy the latter.
"""
from __future__ import annotations

import difflib
import hashlib
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from durable_json import (
    JsonStorageCorruptError,
    delete_json_object,
    read_json_object,
    update_json_object,
)
from secret_paths import is_secret_basename, is_secret_dir_name

_JOURNAL_VERSION = 1
_JOURNAL_RELATIVE_PATH = Path(".kvist") / "change_journal.json"


@dataclass(slots=True)
class RevertedFile:
    path: str
    action: str  # "restored", "deleted", or "skipped"
    reason: str = ""


def journal_path(workspace: Path) -> Path:
    return workspace / _JOURNAL_RELATIVE_PATH


def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def record_baseline(
    workspace: Path,
    *,
    relative_path: str,
    existed: bool,
    content: str | None,
    session: str | None = None,
    new_digest: str | None = None,
) -> None:
    """Record the pre-change state of ``relative_path`` the first time it is
    touched since the journal was last cleared. Later writes keep the earliest
    baseline and only refresh ``last_digest`` with the newest agent content.
    """

    def _record(document: dict[str, Any]) -> dict[str, Any]:
        baseline = document.get("baseline")
        if not isinstance(baseline, dict):
            baseline = {}
        entry = baseline.get(relative_path)
        if not isinstance(entry, dict):
            entry = {
                "existed": existed,
                "content": content,
                "recorded_at": time.time(),
                "session": session,
            }
            baseline[relative_path] = entry
        if new_digest is not None:
            entry["last_digest"] = new_digest
        return {"version": _JOURNAL_VERSION, "baseline": baseline}

    try:
        update_json_object(
            journal_path(workspace),
            _record,
            boundary=workspace,
            empty_as_missing=True,
        )
    except JsonStorageCorruptError:
        # History is best-effort: a damaged journal must never block the
        # write the agent actually asked for.
        pass


def load_baseline(workspace: Path) -> dict[str, dict[str, Any]]:
    try:
        document = read_json_object(
            journal_path(workspace),
            boundary=workspace,
            empty_as_missing=True,
        )
    except JsonStorageCorruptError:
        return {}
    baseline = document.get("baseline")
    if not isinstance(baseline, dict):
        return {}
    return {
        path: entry
        for path, entry in baseline.items()
        if isinstance(entry, dict)
    }


def clear_journal(workspace: Path) -> None:
    delete_json_object(journal_path(workspace), boundary=workspace)


def _entry_label(relative_path: str, entry: dict[str, Any]) -> str:
    session = entry.get("session")
    if isinstance(session, str) and session:
        return f"{relative_path} (session: {session})"
    return relative_path


def build_diff_report(workspace: Path) -> str:
    """Human-readable unified diff between the recorded baseline and the
    current on-disk content, for every path touched since the last clear.
    """
    baseline = load_baseline(workspace)
    if not baseline:
        return "No tracked agent changes — the change journal is empty."

    sections: list[str] = []
    for relative_path in sorted(baseline):
        entry = baseline[relative_path]
        before = entry.get("content") or "" if entry.get("existed") else ""
        target = workspace / relative_path
        after = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""

        if before == after:
            continue

        from_label = relative_path if entry.get("existed") else "/dev/null"
        to_label = relative_path if target.is_file() else "/dev/null"
        diff_lines = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=from_label,
            tofile=to_label,
        )
        sections.append(
            f"# {_entry_label(relative_path, entry)}\n" + "".join(diff_lines)
        )

    if not sections:
        return "No net agent changes remain — every tracked file matches its baseline."
    return "\n".join(sections)


def _unsafe_revert_reason(workspace: Path, relative_path: str) -> str | None:
    """Reject journal paths that revert must never write through.

    The executor never journals these, so a violation means the journal file
    itself was tampered with — refuse rather than trust it.
    """
    candidate = Path(relative_path)
    if candidate.is_absolute() or not candidate.parts:
        return "path is not workspace-relative"
    if any(part in ("..", ".") for part in candidate.parts):
        return "path traverses outside the workspace"
    if any(part == ".kvist" or is_secret_dir_name(part) for part in candidate.parts):
        return "path names protected internal or secret storage"
    if is_secret_basename(candidate.name):
        return "path names a protected secret file"

    current = workspace
    for part in candidate.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            return "path cannot be inspected safely"
        if stat.S_ISLNK(info.st_mode):
            return "path contains a symbolic link"
    return None


def _read_current_bytes(target: Path) -> bytes | None:
    try:
        return target.read_bytes()
    except FileNotFoundError:
        return None
    except (OSError, IsADirectoryError):
        return None


def revert_workspace(workspace: Path) -> list[RevertedFile]:
    """Restore tracked paths to their recorded baselines.

    Only content the agent itself last wrote (verified via ``last_digest``)
    is overwritten or deleted; files changed afterwards by the user or by
    shell commands are reported as skipped and their baselines stay in the
    journal for a later attempt. The journal keeps every skipped entry, so
    an interrupted revert can simply be re-run.
    """
    baseline = load_baseline(workspace)
    reverted: list[RevertedFile] = []
    remaining: dict[str, dict[str, Any]] = {}
    for relative_path in sorted(baseline):
        entry = baseline[relative_path]
        unsafe_reason = _unsafe_revert_reason(workspace, relative_path)
        if unsafe_reason is not None:
            remaining[relative_path] = entry
            reverted.append(RevertedFile(relative_path, "skipped", unsafe_reason))
            continue

        target = workspace / relative_path
        if target.exists() and not target.is_file():
            remaining[relative_path] = entry
            reverted.append(
                RevertedFile(relative_path, "skipped", "target is not a regular file")
            )
            continue
        existed = bool(entry.get("existed"))
        baseline_bytes = (
            (entry.get("content") or "").encode("utf-8") if existed else None
        )
        current_bytes = _read_current_bytes(target)

        if current_bytes == baseline_bytes:
            # Already at the baseline (including "never existed, still absent");
            # the entry is spent.
            continue

        last_digest = entry.get("last_digest")
        agent_owned = (
            current_bytes is not None
            and isinstance(last_digest, str)
            and hashlib.sha256(current_bytes).hexdigest() == last_digest
        )
        # A missing file cannot lose data by being recreated, so a restore is
        # always safe there even without a digest match.
        if not agent_owned and current_bytes is not None:
            remaining[relative_path] = entry
            reverted.append(
                RevertedFile(
                    relative_path,
                    "skipped",
                    "changed outside the agent's tracked writes",
                )
            )
            continue

        if existed:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(entry.get("content") or "", encoding="utf-8")
            reverted.append(RevertedFile(relative_path, "restored"))
        else:
            target.unlink()
            reverted.append(RevertedFile(relative_path, "deleted"))

    if remaining:
        update_json_object(
            journal_path(workspace),
            lambda _existing: {"version": _JOURNAL_VERSION, "baseline": remaining},
            boundary=workspace,
        )
    else:
        clear_journal(workspace)
    return reverted
