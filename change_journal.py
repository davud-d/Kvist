# -*- coding: utf-8 -*-
"""Per-workspace record of the file content that existed before the agent's
first write to each path, so the workspace can be restored on request.

Only the pre-change ("baseline") content is kept, not every intermediate
edit: a full revert only ever needs to know what a file looked like before
the agent touched it, and re-recording every write would make the journal
grow without bound over a long session.
"""
from __future__ import annotations

import difflib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from durable_json import JsonStorageCorruptError, delete_json_object, read_json_object, update_json_object

_JOURNAL_VERSION = 1
_JOURNAL_RELATIVE_PATH = Path(".kvist") / "change_journal.json"


@dataclass(slots=True)
class RevertedFile:
    path: str
    action: str  # "restored" or "deleted"


def journal_path(workspace: Path) -> Path:
    return workspace / _JOURNAL_RELATIVE_PATH


def record_baseline(
    workspace: Path,
    *,
    relative_path: str,
    existed: bool,
    content: str | None,
) -> None:
    """Record the pre-change state of ``relative_path`` the first time it is
    touched since the journal was last cleared. Later writes to the same
    path are no-ops here — the baseline must stay the earliest known state.
    """

    def _record(document: dict[str, Any]) -> dict[str, Any]:
        baseline = document.get("baseline")
        if not isinstance(baseline, dict):
            baseline = {}
        if relative_path not in baseline:
            baseline[relative_path] = {
                "existed": existed,
                "content": content,
                "recorded_at": time.time(),
            }
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
    return baseline if isinstance(baseline, dict) else {}


def clear_journal(workspace: Path) -> None:
    delete_json_object(journal_path(workspace), boundary=workspace)


def build_diff_report(workspace: Path) -> str:
    """Human-readable unified diff between the recorded baseline and the
    current on-disk content, for every path touched since the last clear.
    """
    baseline = load_baseline(workspace)
    if not baseline:
        return "No tracked changes since the last session reset."

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
        sections.append("".join(diff_lines))

    if not sections:
        return "No net changes remain since the last session reset."
    return "\n".join(sections)


def revert_workspace(workspace: Path) -> list[RevertedFile]:
    """Restore every tracked path to its recorded baseline and clear the
    journal. Files that did not exist before the session are deleted.
    """
    baseline = load_baseline(workspace)
    reverted: list[RevertedFile] = []
    for relative_path in sorted(baseline):
        entry = baseline[relative_path]
        target = (workspace / relative_path).resolve()
        target.relative_to(workspace)  # refuse to touch anything outside the workspace

        if entry.get("existed"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(entry.get("content") or "", encoding="utf-8")
            reverted.append(RevertedFile(relative_path, "restored"))
        elif target.is_file():
            target.unlink()
            reverted.append(RevertedFile(relative_path, "deleted"))

    clear_journal(workspace)
    return reverted
