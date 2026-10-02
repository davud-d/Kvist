# -*- coding: utf-8 -*-
"""Workspace-global write-ahead journal of the file content that existed before
the agent's first write to each path, so agent changes can be reverted.

The journal is a *safety* mechanism, not best-effort history: a reversible
agent-owned mutation may only proceed once its pre-change baseline is durably
recorded, so :func:`record_baseline` refuses (raises) on a corrupt journal and
the executor aborts the write before touching the file.

Provenance is tracked per path with three digests plus transaction identity:

* ``last_digest`` — hash of the content the agent last *successfully committed*
  (advanced only by :func:`promote_baseline` after the atomic write returns and
  only when the run/action identity of the pending write still matches).
* ``pending_digest`` (+ ``pending_run_id`` / ``pending_action_id``) — the
  in-flight write recorded write-ahead, bound to the exact action that created
  it so a different action can never finalise it.
* ``commit_unconfirmed`` — set when the filesystem write landed but journal
  promotion did not, marking a partial commit for conservative recovery.

Every filesystem access driven by a journal path goes through the fd-relative
primitives below, which bind validation and use to the same directory object
(closing the validate-then-use TOCTOU window) and enforce the same symlink,
hardlink/inode-containment, ``.kvist`` and secret-path policy as the executor.
"""
from __future__ import annotations

import difflib
import errno
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.durable_json import (
    JsonStorageCorruptError,
    delete_json_object,
    read_json_object,
    update_json_object,
)
from kernel.secret_paths import (
    UnsafeSecretPathError,
    count_workspace_inode_aliases,
    is_secret_basename,
    is_secret_dir_name,
)

_JOURNAL_VERSION = 1
_JOURNAL_RELATIVE_PATH = Path(".kvist") / "change_journal.json"

# fd-relative operations are only race-safe if every primitive we bind to a
# directory fd actually accepts one. os.replace lacks dir_fd support, so the
# atomic rename uses os.rename (equally atomic and overwriting on POSIX).
_HAS_DIR_FD = os.name != "nt" and {
    os.open, os.stat, os.unlink, os.rename
} <= os.supports_dir_fd


class JournalUnavailableError(RuntimeError):
    """The change journal cannot be read or written safely.

    Raised for a reversible mutation when the pre-change baseline cannot be
    durably recorded (corrupt journal), so the caller can refuse the mutation
    instead of silently losing the ability to revert it.
    """


@dataclass(slots=True)
class RevertedFile:
    path: str
    action: str  # "restored", "deleted", or "skipped"
    reason: str = ""


def journal_path(workspace: Path) -> Path:
    return workspace / _JOURNAL_RELATIVE_PATH


def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ── Journal document access ──────────────────────────────────────────────────


def journal_status(workspace: Path) -> str:
    """Classify the journal: ``absent`` / ``empty`` / ``valid`` / ``corrupt``."""
    path = journal_path(workspace)
    if not path.exists() and not path.with_suffix(path.suffix + ".bak").exists():
        return "absent"
    try:
        document = read_json_object(path, boundary=workspace, empty_as_missing=True)
    except JsonStorageCorruptError:
        return "corrupt"
    if not document:
        return "empty"
    baseline = document.get("baseline")
    if not isinstance(baseline, dict) or not baseline:
        return "empty"
    return "valid"


def load_baseline(workspace: Path) -> dict[str, dict[str, Any]]:
    """Return the per-path baseline, raising on corruption.

    Safety-sensitive callers (revert, reconciliation) must NOT mistake a corrupt
    journal for an empty one, so corruption propagates as
    :class:`JournalUnavailableError` rather than being swallowed.
    """
    try:
        document = read_json_object(
            journal_path(workspace), boundary=workspace, empty_as_missing=True
        )
    except JsonStorageCorruptError as error:
        raise JournalUnavailableError(
            "The change journal is corrupt and has no valid backup"
        ) from error
    baseline = document.get("baseline")
    if not isinstance(baseline, dict):
        return {}
    return {path: entry for path, entry in baseline.items() if isinstance(entry, dict)}


def record_baseline(
    workspace: Path,
    *,
    relative_path: str,
    existed: bool,
    content: str | None,
    session: str | None = None,
    pending_digest: str | None = None,
    run_id: str | None = None,
    action_id: str | None = None,
) -> None:
    """Write-ahead record of the pre-change state of ``relative_path``.

    Called BEFORE the irreversible mutation. Stores the baseline on first touch
    and (re)binds the in-flight ``pending_digest`` to the ``run_id``/``action_id``
    of the exact action. Raises :class:`JournalUnavailableError` if the record
    cannot be durably written, so the caller can refuse the mutation.
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
                "session": session,
            }
            baseline[relative_path] = entry
        if pending_digest is not None:
            entry["pending_digest"] = pending_digest
            entry["pending_run_id"] = run_id
            entry["pending_action_id"] = action_id
            entry.pop("commit_unconfirmed", None)
        return {"version": _JOURNAL_VERSION, "baseline": baseline}

    try:
        update_json_object(
            journal_path(workspace), _record, boundary=workspace, empty_as_missing=True
        )
    except JsonStorageCorruptError as error:
        raise JournalUnavailableError(
            "Cannot record write-ahead recovery state: the change journal is corrupt"
        ) from error


def promote_baseline(
    workspace: Path,
    relative_path: str,
    expected_pending_digest: str,
    *,
    run_id: str | None = None,
    action_id: str | None = None,
) -> bool:
    """Compare-and-promote: ``last_digest = pending_digest`` iff the pending
    write still matches the completing transaction (digest AND run/action id).

    Binds promotion to one transaction so a stale or interleaved pending write
    from a different action can never be promoted. Returns whether the promotion
    happened; raises :class:`JournalUnavailableError` on corruption.
    """
    promoted = False

    def _promote(document: dict[str, Any]) -> dict[str, Any]:
        nonlocal promoted
        baseline = document.get("baseline")
        if not isinstance(baseline, dict):
            baseline = {}
        entry = baseline.get(relative_path)
        if (
            isinstance(entry, dict)
            and entry.get("pending_digest") == expected_pending_digest
            and entry.get("pending_run_id") == run_id
            and entry.get("pending_action_id") == action_id
        ):
            entry["last_digest"] = expected_pending_digest
            entry.pop("commit_unconfirmed", None)
            promoted = True
        return {"version": _JOURNAL_VERSION, "baseline": baseline}

    try:
        update_json_object(
            journal_path(workspace), _promote, boundary=workspace, empty_as_missing=True
        )
    except JsonStorageCorruptError as error:
        raise JournalUnavailableError(
            "Cannot promote the write-ahead journal: it is corrupt"
        ) from error
    return promoted


def mark_commit_unconfirmed(workspace: Path, relative_path: str) -> None:
    """Flag a path whose filesystem write landed but journal promotion did not.

    Best-effort diagnostic marker for a partial commit; recovery already treats
    unpromoted content as unowned, this only makes the state visible.
    """

    def _mark(document: dict[str, Any]) -> dict[str, Any]:
        baseline = document.get("baseline")
        if not isinstance(baseline, dict):
            baseline = {}
        entry = baseline.get(relative_path)
        if isinstance(entry, dict):
            entry["commit_unconfirmed"] = True
        return {"version": _JOURNAL_VERSION, "baseline": baseline}

    try:
        update_json_object(
            journal_path(workspace), _mark, boundary=workspace, empty_as_missing=True
        )
    except JsonStorageCorruptError:
        pass


def clear_journal(workspace: Path) -> None:
    delete_json_object(journal_path(workspace), boundary=workspace)


# ── TOCTOU-safe workspace filesystem primitives ──────────────────────────────


def journal_path_violation(workspace: Path, relative_path: str) -> str | None:
    """Cheap lexical/secret pre-check for a journal path (no fs binding).

    Rejects boundary escapes, ``.kvist`` (case-insensitively) and secret paths.
    Symlink/hardlink/identity enforcement is bound to the actual operation by
    the fd-relative primitives below, so this is only the first gate.
    """
    candidate = Path(relative_path)
    if candidate.is_absolute() or not candidate.parts:
        return "path is not workspace-relative"
    if any(part in ("..", ".") for part in candidate.parts):
        return "path traverses outside the workspace"
    if any(
        part.casefold() == ".kvist" or is_secret_dir_name(part)
        for part in candidate.parts
    ):
        return "path names protected internal or secret storage"
    if is_secret_basename(candidate.name):
        return "path names a protected secret file"
    return None


class JournalPathBindingError(RuntimeError):
    def __init__(self, relative_path: str, reason: str = "cannot be bound safely") -> None:
        super().__init__(f"Journal path {relative_path!r} {reason}")
        self.relative_path = relative_path
        self.reason = reason


class _SafeChild:
    """An open handle to a validated workspace parent directory + final name.

    On POSIX every directory component is opened with ``O_NOFOLLOW|O_DIRECTORY``
    relative to the previous one, so a component swapped to a symlink after the
    lexical check fails the open — validation and use refer to the same objects.
    """

    __slots__ = ("workspace", "relative_path", "dir_fd", "name", "_parent_path")

    def __init__(self, workspace: Path, relative_path: str) -> None:
        self.workspace = workspace
        self.relative_path = relative_path
        self.dir_fd: int | None = None
        parts = Path(relative_path).parts
        self.name = parts[-1]
        self._parent_path = workspace.joinpath(*parts[:-1]) if len(parts) > 1 else workspace

    def __enter__(self) -> _SafeChild:
        if not _HAS_DIR_FD:
            return self
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        root = os.open(self.workspace, flags)
        fd = root
        try:
            for part in Path(self.relative_path).parts[:-1]:
                nxt = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = nxt
        except OSError:
            os.close(fd)
            raise JournalPathBindingError(self.relative_path)
        self.dir_fd = fd
        return self

    def __exit__(self, *exc: object) -> None:
        if self.dir_fd is not None:
            os.close(self.dir_fd)
            self.dir_fd = None

    def _final_info(self) -> os.stat_result | None:
        if _HAS_DIR_FD:
            try:
                return os.stat(self.name, dir_fd=self.dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                return None
        try:
            return (self._parent_path / self.name).lstat()
        except FileNotFoundError:
            return None

    def _reject_unsafe(self, info: os.stat_result) -> None:
        if stat.S_ISLNK(info.st_mode):
            raise JournalPathBindingError(self.relative_path, "symbolic link")
        if not stat.S_ISREG(info.st_mode):
            raise JournalPathBindingError(self.relative_path, "not a regular file")
        # Hardlink/inode containment, matching the executor's policy: a
        # multiply-linked inode is safe only if every alias lives in the
        # workspace. Aliases inside ``.kvist`` are not counted, so containment
        # there stays unproven and the target is rejected.
        if info.st_nlink > 1:
            try:
                aliases = count_workspace_inode_aliases(
                    self.workspace, (info.st_dev, info.st_ino)
                )
            except UnsafeSecretPathError:
                raise JournalPathBindingError(self.relative_path, "unprovable hard links")
            if aliases < info.st_nlink:
                raise JournalPathBindingError(self.relative_path, "unsafe hard links")

    def read_bytes(self) -> bytes | None:
        info = self._final_info()
        if info is None:
            return None
        self._reject_unsafe(info)
        if _HAS_DIR_FD:
            fd = os.open(self.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.dir_fd)
            with os.fdopen(fd, "rb", closefd=True) as handle:
                return handle.read()
        return (self._parent_path / self.name).read_bytes()

    def is_regular_file(self) -> bool:
        info = self._final_info()
        return info is not None and stat.S_ISREG(info.st_mode)

    def atomic_write_text(self, text: str) -> None:
        encoded = text.encode("utf-8")
        existing = self._final_info()
        if existing is not None:
            self._reject_unsafe(existing)
        mode = stat.S_IMODE(existing.st_mode) if existing is not None else 0o644
        temp_name = f".{self.name}.{os.getpid()}.revert.tmp"
        if _HAS_DIR_FD:
            fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode,
                dir_fd=self.dir_fd,
            )
            replaced = False
            try:
                os.write(fd, encoded)
                os.fsync(fd)
                os.close(fd)
                fd = -1
                os.rename(temp_name, self.name, src_dir_fd=self.dir_fd, dst_dir_fd=self.dir_fd)
                replaced = True
                _fsync_dir_fd(self.dir_fd)
            finally:
                if fd >= 0:
                    os.close(fd)
                if not replaced:
                    try:
                        os.unlink(temp_name, dir_fd=self.dir_fd)
                    except OSError:
                        pass
            return
        target = self._parent_path / self.name
        tmp = self._parent_path / temp_name
        tmp.write_bytes(encoded)
        try:
            os.replace(tmp, target)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    def unlink(self) -> None:
        info = self._final_info()
        if info is None:
            return
        self._reject_unsafe(info)
        if _HAS_DIR_FD:
            os.unlink(self.name, dir_fd=self.dir_fd)
        else:
            (self._parent_path / self.name).unlink()


def _fsync_dir_fd(dir_fd: int | None) -> None:
    if dir_fd is None:
        return
    try:
        os.fsync(dir_fd)
    except OSError as error:
        if error.errno not in {errno.EBADF, errno.EINVAL, errno.ENOTSUP}:
            raise


# ── Reporting, revert, reconciliation ────────────────────────────────────────


def _entry_label(relative_path: str, entry: dict[str, Any]) -> str:
    session = entry.get("session")
    if isinstance(session, str) and session:
        return f"{relative_path} (session: {session})"
    return relative_path


def build_diff_report(workspace: Path) -> str:
    """Unified diff between baseline and current content for every tracked path.

    Reports corruption explicitly rather than pretending the journal is empty,
    and reads through the TOCTOU-safe primitive so a tampered/raced path can
    neither disclose content outside the workspace nor follow a symlink.
    """
    try:
        baseline = load_baseline(workspace)
    except JournalUnavailableError:
        return (
            "The change journal is corrupt and cannot be read. Its history and "
            "revert data are unavailable until the journal is reset "
            "(remove .kvist/change_journal.json to start tracking afresh)."
        )
    if not baseline:
        return "No tracked agent changes — the change journal is empty."

    sections: list[str] = []
    for relative_path in sorted(baseline):
        if journal_path_violation(workspace, relative_path) is not None:
            continue
        entry = baseline[relative_path]
        before = entry.get("content") or "" if entry.get("existed") else ""
        try:
            with _SafeChild(workspace, relative_path) as child:
                raw = child.read_bytes()
        except JournalPathBindingError:
            continue
        after = raw.decode("utf-8", errors="replace") if raw is not None else ""

        if before == after:
            continue

        from_label = relative_path if entry.get("existed") else "/dev/null"
        to_label = relative_path if raw is not None else "/dev/null"
        diff_lines = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=from_label,
            tofile=to_label,
        )
        note = " [filesystem write unconfirmed in journal]" if entry.get("commit_unconfirmed") else ""
        sections.append(f"# {_entry_label(relative_path, entry)}{note}\n" + "".join(diff_lines))

    if not sections:
        return "No net agent changes remain — every tracked file matches its baseline."
    return "\n".join(sections)


def revert_workspace(workspace: Path) -> list[RevertedFile]:
    """Restore tracked paths to their recorded baselines.

    Only content whose hash matches the agent's last *promoted* write
    (``last_digest``) is overwritten or deleted; anything else — later user
    edits, user deletions of pre-existing files, and unconfirmed partial commits
    — is skipped and keeps its baseline. Restores/deletes go through the
    TOCTOU-safe primitive, and a corrupt journal raises rather than silently
    doing nothing.
    """
    baseline = load_baseline(workspace)
    reverted: list[RevertedFile] = []
    remaining: dict[str, dict[str, Any]] = {}

    def keep(rel: str, entry: dict[str, Any], reason: str) -> None:
        remaining[rel] = entry
        reverted.append(RevertedFile(rel, "skipped", reason))

    for relative_path in sorted(baseline):
        entry = baseline[relative_path]
        violation = journal_path_violation(workspace, relative_path)
        if violation is not None:
            keep(relative_path, entry, violation)
            continue
        try:
            with _SafeChild(workspace, relative_path) as child:
                if child._final_info() is not None and not child.is_regular_file():
                    keep(relative_path, entry, "target is not a regular file")
                    continue
                current_bytes = child.read_bytes()
                outcome = _plan_revert(entry, current_bytes)
                if outcome == "noop":
                    continue
                if outcome.startswith("skip:"):
                    keep(relative_path, entry, outcome[len("skip:"):])
                    continue
                if outcome == "restore":
                    child.atomic_write_text(entry.get("content") or "")
                    reverted.append(RevertedFile(relative_path, "restored"))
                elif outcome == "delete":
                    child.unlink()
                    reverted.append(RevertedFile(relative_path, "deleted"))
        except JournalPathBindingError as error:
            keep(relative_path, entry, error.reason)

    if remaining:
        update_json_object(
            journal_path(workspace),
            lambda _existing: {"version": _JOURNAL_VERSION, "baseline": remaining},
            boundary=workspace,
        )
    else:
        clear_journal(workspace)
    return reverted


def _plan_revert(entry: dict[str, Any], current_bytes: bytes | None) -> str:
    existed = bool(entry.get("existed"))
    last_digest = entry.get("last_digest")
    current_digest = (
        hashlib.sha256(current_bytes).hexdigest() if current_bytes is not None else None
    )
    baseline_digest = content_digest(entry.get("content") or "") if existed else None

    if current_digest == baseline_digest:
        return "noop"
    agent_owned = (
        isinstance(last_digest, str)
        and current_digest is not None
        and current_digest == last_digest
    )
    if current_bytes is None:
        return "skip:file was deleted outside the agent's tracked writes"
    if not agent_owned:
        if entry.get("commit_unconfirmed"):
            return "skip:filesystem write was never confirmed in the journal"
        return "skip:changed outside the agent's tracked writes"
    return "restore" if existed else "delete"


def classify_pending_write(
    workspace: Path,
    relative_path: str,
    pre_digest: str | None,
    intended_digest: str,
    *,
    run_id: str | None = None,
    action_id: str | None = None,
) -> str:
    """Reconcile a crash-interrupted mutation against the current workspace.

    Returns ``agent_committed`` (intended content present AND promoted for this
    exact transaction), ``desired_present`` (intended content present but not
    provably agent-authored), ``not_executed`` (pre-content still present), or
    ``ambiguous``. Intent never implies authorship.
    """
    if journal_path_violation(workspace, relative_path) is not None:
        return "ambiguous"
    try:
        with _SafeChild(workspace, relative_path) as child:
            current_bytes = child.read_bytes()
    except JournalPathBindingError:
        return "ambiguous"
    current_digest = (
        hashlib.sha256(current_bytes).hexdigest() if current_bytes is not None else None
    )
    if current_digest == intended_digest:
        try:
            entry = load_baseline(workspace).get(relative_path)
        except JournalUnavailableError:
            return "ambiguous"
        if isinstance(entry, dict) and entry.get("last_digest") == intended_digest and (
            entry.get("pending_run_id") in (None, run_id)
            and entry.get("pending_action_id") in (None, action_id)
        ):
            return "agent_committed"
        return "desired_present"
    if current_digest == pre_digest:
        return "not_executed"
    return "ambiguous"
