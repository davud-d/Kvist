# -*- coding: utf-8 -*-
"""Git side of best-of mode: one worktree and branch per executor, and taking the winner's work into the user's branch.

Layout, all under the repository root that contains the workspace::

    .kvist/bestof/<run_id>/run.json          state of the run (atomic writes)
    .kvist/bestof/<run_id>/<executor_id>/    the executor's worktree, branch kvist/bestof/<run_id>/<executor_id>

Every executor starts from a snapshot commit S (HEAD plus the user's uncommitted and untracked work, without
``.kvist``) that is built in a temporary index, so the user's index and files are never touched before accept().
Accepting replays the winner's change S..W on top of the user's *current* HEAD as one new commit and then syncs only
the files of that change, which keeps the user's other uncommitted edits uncommitted.

Nothing here talks to a model or emits events; git runs as a subprocess without a shell.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Sequence

_logger = logging.getLogger(__name__)

RUN_FILE = "run.json"
BRANCH_PREFIX = "kvist/bestof"
DEFAULT_DIFF_LIMIT = 200_000
GIT_TIMEOUT = 600
_CHUNK = 400  # paths per git command line: far below any ARG_MAX, large enough to need few calls

# Run states. Only "running" is changed by recover(); "pending" means the executors are done and a human decides.
STATE_RUNNING = "running"
STATE_PENDING = "pending"
STATE_CANCELLED = "cancelled"
STATE_INTERRUPTED = "interrupted"
STATE_ACCEPTED = "accepted"
_MARKABLE = (STATE_RUNNING, STATE_PENDING, STATE_CANCELLED)
_WAITING = (STATE_PENDING, STATE_CANCELLED, STATE_INTERRUPTED)

EXECUTOR_READY = "ready"
EXECUTOR_DONE = "done"
EXECUTOR_LOST = "lost"

# Service commits (the snapshot, the executors' results) never reach the user's branch, so they carry a fixed author.
_SERVICE_IDENTITY = {
    "GIT_AUTHOR_NAME": "Kvist best-of",
    "GIT_AUTHOR_EMAIL": "bestof@kvist.invalid",
    "GIT_COMMITTER_NAME": "Kvist best-of",
    "GIT_COMMITTER_EMAIL": "bestof@kvist.invalid",
}
# A glob pattern, because the workspace may be a subfolder whose own .kvist must stay out as well.
_EXCLUDE_KVIST = ":(exclude,glob)**/.kvist/**"
_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
# Variables that move the repository or change what a pathspec means; each call that needs one sets it itself.
_INHERITED_GIT_STATE = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS",
)
_IN_PROGRESS = (
    ("MERGE_HEAD", "merge"),
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
)


def _both(english: str, russian: str) -> str:
    return f"{english} / {russian}"


class BestOfRefused(Exception):
    """Best-of cannot start or continue; the message is one line in English and Russian."""


class GitError(RuntimeError):
    """A git command failed unexpectedly. Before accept() moves the branch it leaves the user's repository as it was."""


class _Conflict(Exception):
    """Internal: the winner's work cannot be taken now; accept() turns it into an AcceptResult."""

    def __init__(self, paths: Sequence[str], text: str) -> None:
        super().__init__(text)
        self.paths = tuple(paths)
        self.text = text


@dataclass(slots=True)
class Preflight:
    repo_root: Path
    workspace_rel: Path
    branch: str
    head: str


@dataclass(slots=True)
class ExecutorInfo:
    executor_id: str
    status: str = EXECUTOR_READY
    result: str | None = None  # commit W recorded by record_result()


@dataclass(slots=True)
class RunInfo:
    run_id: str
    repo_root: Path
    workspace_rel: Path
    branch: str  # the user's branch when the run started
    head: str  # the user's HEAD when the run started
    snapshot: str  # commit S
    state: str
    owner_pid: int
    owner_start: int | None
    created: float
    executors: dict[str, ExecutorInfo] = field(default_factory=dict)
    accepted_commit: str | None = None

    @property
    def run_dir(self) -> Path:
        return self.repo_root / ".kvist" / "bestof" / self.run_id

    def worktree(self, executor_id: str) -> Path:
        return self.run_dir / executor_id

    def executor_branch(self, executor_id: str) -> str:
        return f"{BRANCH_PREFIX}/{self.run_id}/{executor_id}"


@dataclass(slots=True)
class FileChange:
    path: str
    added: int
    removed: int  # binary files count 0/0


@dataclass(slots=True)
class DiffSummary:
    files: tuple[FileChange, ...]
    added: int
    removed: int
    stat: str  # `git diff --stat` text

    @property
    def empty(self) -> bool:
        return not self.files


@dataclass(slots=True)
class AcceptResult:
    commit: str | None = None
    conflict_paths: tuple[str, ...] = ()
    conflict_text: str = ""

    @property
    def accepted(self) -> bool:
        return self.commit is not None


# ---------------------------------------------------------------- git plumbing

def _git_environment(extra: dict[str, str] | None) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key not in _INHERITED_GIT_STATE}
    environment["LC_ALL"] = "C"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    if extra:
        environment.update(extra)
    return environment


_NO_HOOKS: list[Path] = []


def _no_hooks_dir() -> Path:
    """An empty folder for core.hooksPath: service git operations must not run the user's hooks (/dev/null is not portable)."""
    with _LOCKS_GUARD:
        if not _NO_HOOKS or not _NO_HOOKS[0].is_dir():
            folder = Path(tempfile.mkdtemp(prefix="kvist-nohooks-"))
            atexit.register(shutil.rmtree, folder, ignore_errors=True)
            _NO_HOOKS[:] = [folder]
        return _NO_HOOKS[0]


def _git(
    cwd: Path,
    *args: str,
    env: dict[str, str] | None = None,
    stdin: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    command = ["git", "-c", "core.quotepath=off", "-c", f"core.hooksPath={_no_hooks_dir()}", *args]
    try:
        done = subprocess.run(
            command, cwd=cwd, env=_git_environment(env), input=stdin,
            capture_output=True, timeout=GIT_TIMEOUT, check=False,
        )
    except FileNotFoundError as error:
        if shutil.which("git") is None:
            raise BestOfRefused(_NO_GIT) from error
        raise
    except subprocess.TimeoutExpired as error:
        raise GitError(f"git {args[0]} timed out after {GIT_TIMEOUT} s / git {args[0]} не ответил за {GIT_TIMEOUT} с") from error
    if check and done.returncode != 0:
        raise GitError(f"git {' '.join(args[:2])} failed: {_message(done.stderr)}")
    return done


def _message(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").strip()


def _out(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return _message(_git(cwd, *args, env=env).stdout)


def _paths(raw: bytes) -> list[str]:
    """Names from NUL-separated git output, byte-exact (a name need not be valid UTF-8)."""
    return [os.fsdecode(part) for part in raw.split(b"\0") if part]


def _nul_joined(paths: Sequence[str]) -> bytes:
    return b"".join(os.fsencode(path) + b"\0" for path in paths)


def _chunks(items: Sequence[str]) -> Iterator[Sequence[str]]:
    for start in range(0, len(items), _CHUNK):
        yield items[start:start + _CHUNK]


_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _repo_lock(repo_root: Path) -> threading.RLock:
    """One lock per repository: concurrent `git worktree add` races on .git/worktrees, and run.json has one writer."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(repo_root, threading.RLock())


# ---------------------------------------------------------------- preflight

_NO_GIT = _both(
    "Best-of needs git, and it is not installed",
    "best-of требует git, а он не установлен",
)
_NOT_REPO = _both(
    "Best-of works only inside a git repository",
    "best-of работает только в git-репозитории",
)
_NO_COMMITS = _both(
    "The repository has no commits yet; make the first commit before using best-of",
    "В репозитории ещё нет ни одного коммита; сделай первый коммит перед best-of",
)
_DETACHED = _both(
    "HEAD is detached; switch to a branch first",
    "HEAD откреплён; сначала перейди на ветку",
)


def _in_progress_message(operation: str) -> str:
    return _both(
        f"A {operation} is in progress; finish or abort it first",
        f"Сейчас идёт {operation}; сначала заверши или отмени его",
    )


def _find_repo_root(workspace: Path) -> Path | None:
    if shutil.which("git") is None or not workspace.is_dir():
        return None
    done = _git(workspace, "rev-parse", "--show-toplevel", check=False)
    if done.returncode != 0:
        return None
    return Path(_message(done.stdout)).resolve()


def _operation_in_progress(repo_root: Path) -> str | None:
    git_dir = Path(_out(repo_root, "rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = repo_root / git_dir
    for marker, operation in _IN_PROGRESS:
        if (git_dir / marker).exists():
            return operation
    return None


def preflight(workspace: Path) -> Preflight:
    """Check that best-of can run in this workspace; raises BestOfRefused with a bilingual reason."""
    if shutil.which("git") is None:
        raise BestOfRefused(_NO_GIT)
    workspace = Path(workspace)
    repo_root = _find_repo_root(workspace)
    if repo_root is None:
        raise BestOfRefused(_NOT_REPO)
    try:
        workspace_rel = workspace.resolve().relative_to(repo_root)
    except ValueError as error:
        raise BestOfRefused(_NOT_REPO) from error
    head = _git(repo_root, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False)
    if head.returncode != 0:
        raise BestOfRefused(_NO_COMMITS)
    branch = _git(repo_root, "symbolic-ref", "-q", "--short", "HEAD", check=False)
    if branch.returncode != 0:
        raise BestOfRefused(_DETACHED)
    operation = _operation_in_progress(repo_root)
    if operation is not None:
        raise BestOfRefused(_in_progress_message(operation))
    return Preflight(repo_root, workspace_rel, _message(branch.stdout), _message(head.stdout))


# ---------------------------------------------------------------- run.json

def _process_start_time(pid: int) -> int | None:
    """Start time of a process in clock ticks since boot (Linux), so a reused pid is not mistaken for the owner."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        return int(raw.rsplit(") ", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def _windows_pid_exists(pid: int) -> bool:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: the process exists, it is another user's
    code = ctypes.c_ulong()
    known = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    return bool(known) and code.value == 259  # STILL_ACTIVE


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _owner_alive(run: RunInfo) -> bool:
    if not _pid_exists(run.owner_pid):
        return False
    if run.owner_start is None:
        return True
    return _process_start_time(run.owner_pid) == run.owner_start


def _write_run(run: RunInfo) -> None:
    payload = {
        "version": 1,
        "run_id": run.run_id,
        "workspace_rel": run.workspace_rel.as_posix(),
        "branch": run.branch,
        "head": run.head,
        "snapshot": run.snapshot,
        "state": run.state,
        "owner": {"pid": run.owner_pid, "start": run.owner_start},
        "created": run.created,
        "accepted_commit": run.accepted_commit,
        "executors": [
            {"id": info.executor_id, "status": info.status, "result": info.result}
            for info in run.executors.values()
        ],
    }
    with _repo_lock(run.repo_root):
        run.run_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=run.run_dir, prefix=".run-", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, run.run_dir / RUN_FILE)
        except OSError:
            Path(temporary).unlink(missing_ok=True)
            raise


def _read_run(run_file: Path, repo_root: Path) -> RunInfo:
    try:
        data = json.loads(run_file.read_text(encoding="utf-8"))
        executors = {
            item["id"]: ExecutorInfo(item["id"], item["status"], item["result"]) for item in data["executors"]
        }
        return RunInfo(
            run_id=data["run_id"],
            repo_root=repo_root,
            workspace_rel=Path(data["workspace_rel"]),
            branch=data["branch"],
            head=data["head"],
            snapshot=data["snapshot"],
            state=data["state"],
            owner_pid=int(data["owner"]["pid"]),
            owner_start=data["owner"]["start"],
            created=float(data["created"]),
            executors=executors,
            accepted_commit=data["accepted_commit"],
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise BestOfRefused(_both(
            f"The best-of state in {run_file} cannot be read: {error}",
            f"Состояние best-of в {run_file} не читается: {error}",
        )) from error


def load_run(workspace: Path, run_id: str) -> RunInfo:
    repo_root = _find_repo_root(Path(workspace))
    if repo_root is None:
        raise BestOfRefused(_NOT_REPO)
    _check_id(run_id)
    run_file = repo_root / ".kvist" / "bestof" / run_id / RUN_FILE
    if not run_file.is_file():
        raise BestOfRefused(_both(f"No best-of run {run_id}", f"Нет прогона best-of {run_id}"))
    return _read_run(run_file, repo_root)


def mark(run: RunInfo, state: str) -> None:
    """Set running / pending / cancelled. A cancelled run stays on disk for review; only discard() removes it."""
    if state not in _MARKABLE:
        raise ValueError(f"state must be one of {_MARKABLE}, not {state!r}")
    if run.state == STATE_ACCEPTED:
        raise BestOfRefused(_both("This run is already accepted", "Этот прогон уже принят"))
    with _repo_lock(run.repo_root):
        run.state = state
        _write_run(run)


# ---------------------------------------------------------------- creating a run

def _check_id(value: str) -> None:
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError(f"id {value!r} must be 1-64 characters of A-Z a-z 0-9 _ - and start with a letter or digit")


def _service_commit(cwd: Path, tree: str, parent: str, message: str) -> str:
    return _out(cwd, "commit-tree", tree, "-p", parent, "-m", message, env=_SERVICE_IDENTITY)


def _make_snapshot(repo_root: Path, head: str) -> str:
    with tempfile.TemporaryDirectory(prefix="kvist-bestof-") as folder:
        env = {"GIT_INDEX_FILE": str(Path(folder) / "index")}
        _git(repo_root, "read-tree", head, env=env)
        _git(repo_root, "add", "-A", "--", ".", _EXCLUDE_KVIST, env=env)
        tree = _out(repo_root, "write-tree", env=env)
    return _service_commit(repo_root, tree, head, "Kvist best-of snapshot")


def _branch_exists(repo_root: Path, branch: str) -> bool:
    return _git(repo_root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False).returncode == 0


def _remove_run_branches_and_worktrees(run: RunInfo) -> None:
    """Idempotent: a half-created or half-removed run is cleaned the same way as a complete one."""
    for executor_id in run.executors:
        path = run.worktree(executor_id)
        _git(run.repo_root, "worktree", "remove", "--force", str(path), check=False)
        if path.exists():
            shutil.rmtree(path)
    _git(run.repo_root, "worktree", "prune")
    for executor_id in run.executors:
        branch = run.executor_branch(executor_id)
        if _branch_exists(run.repo_root, branch):
            _git(run.repo_root, "update-ref", "-d", f"refs/heads/{branch}")


def _hide_from_git(bestof_dir: Path) -> None:
    """Keep the nested worktrees out of the user's `git status` and `git add -A`, touching only Kvist's own folder."""
    ignore = bestof_dir / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")


def create_run(workspace: Path, run_id: str, executor_ids: Sequence[str]) -> RunInfo:
    """Snapshot the user's work, give every executor a worktree and branch started from it, and write run.json."""
    _check_id(run_id)
    if not executor_ids or len(set(executor_ids)) != len(executor_ids):
        raise ValueError("executor_ids must be a non-empty list without repeats")
    for executor_id in executor_ids:
        _check_id(executor_id)
    start = preflight(Path(workspace))
    pid = os.getpid()
    with _repo_lock(start.repo_root):
        run = RunInfo(
            run_id=run_id, repo_root=start.repo_root, workspace_rel=start.workspace_rel, branch=start.branch,
            head=start.head, snapshot="", state=STATE_RUNNING, owner_pid=pid,
            owner_start=_process_start_time(pid), created=time.time(),
        )
        if run.run_dir.exists():
            raise BestOfRefused(_both(f"Best-of run {run_id} already exists", f"Прогон best-of {run_id} уже существует"))
        created = False
        try:
            run.snapshot = _make_snapshot(start.repo_root, start.head)
            run.run_dir.mkdir(parents=True)
            _hide_from_git(run.run_dir.parent)
            for executor_id in executor_ids:
                run.executors[executor_id] = ExecutorInfo(executor_id)
                _git(start.repo_root, "worktree", "add", "-b", run.executor_branch(executor_id),
                     str(run.worktree(executor_id)), run.snapshot)
                executor_workspace(run, executor_id).mkdir(parents=True, exist_ok=True)
            _write_run(run)
            created = True
        finally:
            if not created:
                _remove_run_branches_and_worktrees(run)
                shutil.rmtree(run.run_dir, ignore_errors=True)
    return run


def _executor(run: RunInfo, executor_id: str) -> ExecutorInfo:
    try:
        return run.executors[executor_id]
    except KeyError:
        raise ValueError(f"run {run.run_id} has no executor {executor_id!r}") from None


def executor_workspace(run: RunInfo, executor_id: str) -> Path:
    """The folder an executor works in: its worktree, plus the workspace's path inside the repository."""
    _executor(run, executor_id)
    return run.worktree(executor_id) / run.workspace_rel


# ---------------------------------------------------------------- results

_NO_RESULT = _both(
    "This executor has no recorded result yet",
    "У этого исполнителя ещё нет записанного результата",
)


def record_result(run: RunInfo, executor_id: str) -> str:
    """Commit everything the executor left in its worktree (nothing is fine) and return that commit W."""
    info = _executor(run, executor_id)
    worktree = run.worktree(executor_id)
    if not worktree.is_dir():
        raise BestOfRefused(_both(
            f"The worktree of executor {executor_id} is gone", f"Worktree исполнителя {executor_id} пропал",
        ))
    branch = run.executor_branch(executor_id)
    _git(worktree, "add", "-A", "--", ".", _EXCLUDE_KVIST)
    tree = _out(worktree, "write-tree")
    tip = _out(worktree, "rev-parse", "HEAD")
    result = _service_commit(worktree, tree, tip, f"Kvist best-of result of {executor_id}")
    _git(worktree, "update-ref", f"refs/heads/{branch}", result, tip)
    with _repo_lock(run.repo_root):
        info.result = result
        info.status = EXECUTOR_DONE
        _write_run(run)
    return result


def _recorded(run: RunInfo, executor_id: str) -> str:
    result = _executor(run, executor_id).result
    if result is None:
        raise BestOfRefused(_NO_RESULT)
    return result


def summary(run: RunInfo, executor_id: str) -> DiffSummary:
    result = _recorded(run, executor_id)
    base = ["diff", "--no-renames", "--no-ext-diff"]
    numstat = _git(run.repo_root, *base, "--numstat", "-z", run.snapshot, result).stdout
    stat = _out(run.repo_root, *base, "--stat=200", run.snapshot, result)
    files: list[FileChange] = []
    for entry in _paths(numstat):
        added, removed, path = entry.split("\t", 2)
        files.append(FileChange(path, 0 if added == "-" else int(added), 0 if removed == "-" else int(removed)))
    return DiffSummary(
        tuple(files), sum(item.added for item in files), sum(item.removed for item in files), stat,
    )


def full_diff(run: RunInfo, executor_id: str, path: str | None = None, limit: int = DEFAULT_DIFF_LIMIT) -> str:
    """The unified diff S..W, optionally of one path, cut at `limit` characters with an explicit marker."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    result = _recorded(run, executor_id)
    args = ["diff", "--no-renames", "--no-ext-diff", "--no-color", run.snapshot, result]
    if path is not None:
        args += ["--", path]
    raw = _git(run.repo_root, *args, env={"GIT_LITERAL_PATHSPECS": "1"}).stdout
    text = raw.decode("utf-8", errors="replace")
    if len(text) <= limit:
        return text
    rest = len(text) - limit
    return text[:limit] + _both(
        f"\n... [diff truncated: {rest} more characters]\n",
        f"\n... [diff обрезан: ещё {rest} символов]\n",
    )


# ---------------------------------------------------------------- accepting

def _current_branch_ref(repo_root: Path) -> str:
    done = _git(repo_root, "symbolic-ref", "-q", "HEAD", check=False)
    if done.returncode != 0:
        raise _Conflict((), _DETACHED)
    return _message(done.stdout)


def _require_user_identity(repo_root: Path) -> None:
    """The accepted commit is the user's own, so a missing identity is refused rather than invented."""
    for field_name, key in (("NAME", "user.name"), ("EMAIL", "user.email")):
        if all(os.environ.get(f"GIT_{who}_{field_name}") for who in ("AUTHOR", "COMMITTER")):
            continue
        if not _message(_git(repo_root, "config", "--get", key, check=False).stdout):
            raise BestOfRefused(_both(
                f"git has no {key}, so the commit cannot be made in your name; set it with git config {key} ...",
                f"В git не задан {key}, поэтому коммит от твоего имени сделать нельзя; задай его через git config {key} ...",
            ))


def _changes(repo_root: Path, snapshot: str, result: str) -> list[tuple[str, str]]:
    raw = _git(repo_root, "diff", "--name-status", "-z", "--no-renames", "--no-ext-diff", snapshot, result).stdout
    tokens = _paths(raw)
    return list(zip(tokens[0::2], tokens[1::2], strict=True))


def _paths_changed_since_snapshot(repo_root: Path, snapshot: str, changes: Sequence[tuple[str, str]]) -> list[str]:
    """Paths of the winner's change that the user's working tree no longer has as S had them."""
    changed = [path for status, path in changes if status == "A" and os.path.lexists(repo_root / path)]
    known = [path for status, path in changes if status != "A"]
    if not known:
        return changed
    # A sparse index holding only these entries, so that git compares just their content (an index with no stat data
    # makes diff re-hash everything it holds) and the user's own index is not even read.
    with tempfile.TemporaryDirectory(prefix="kvist-bestof-") as folder:
        env = {"GIT_INDEX_FILE": str(Path(folder) / "index"), "GIT_LITERAL_PATHSPECS": "1"}
        listing = b"".join(
            _git(repo_root, "ls-tree", "-r", "-z", snapshot, "--", *chunk, env=env).stdout for chunk in _chunks(known)
        )
        _git(repo_root, "update-index", "--add", "-z", "--index-info", env=env, stdin=listing)
        done = _git(repo_root, "-c", "diff.autoRefreshIndex=true", "diff", "--name-only", "-z", "--no-renames",
                    "--no-ext-diff", env=env)
    return changed + _paths(done.stdout)


_PATCH_ERROR = (
    re.compile(r"^error: patch failed: (.+):\d+$", re.MULTILINE),
    re.compile(r"^error: (.+): patch does not apply$", re.MULTILINE),
    re.compile(r"^error: (.+): does not exist in index$", re.MULTILINE),
)


def _apply_to_head(repo_root: Path, head: str, snapshot: str, result: str, paths: Sequence[str]) -> str:
    """Tree of the user's HEAD with the winner's change S..W laid over it; the working tree is not used."""
    # diff-tree, not diff: no textconv or external driver may rewrite a patch that git apply has to read back.
    patch = _git(
        repo_root, "diff-tree", "-p", "-r", "--binary", "--no-renames", "--no-ext-diff", "-U3",
        "--src-prefix=a/", "--dst-prefix=b/", snapshot, result,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="kvist-bestof-") as folder:
        env = {"GIT_INDEX_FILE": str(Path(folder) / "index")}
        _git(repo_root, "read-tree", head, env=env)
        done = _git(repo_root, "apply", "--cached", "-", env=env, stdin=patch, check=False)
        if done.returncode != 0:
            text = _message(done.stderr)
            named = [path for pattern in _PATCH_ERROR for path in pattern.findall(text)]
            raise _Conflict(sorted(set(named)) or paths, _both(
                "The winner's changes do not apply on top of your current commit and uncommitted work. git says:",
                "Правки победителя не ложатся на твой текущий коммит и незакоммиченную работу. Ответ git:",
            ) + "\n" + text)
        return _out(repo_root, "write-tree", env=env)


def _sync_files(repo_root: Path, commit: str, result: str, paths: Sequence[str]) -> None:
    """Index <- the accepted commit, working tree <- the winner's version, for the winner's paths only."""
    env = {"GIT_LITERAL_PATHSPECS": "1"}
    from_file = ["--pathspec-from-file=-", "--pathspec-file-nul"]
    stdin = _nul_joined(paths)
    # Working tree first: a file the winner deleted must still be in the index for its pathspec to match.
    _git(repo_root, "restore", f"--source={result}", "--worktree", *from_file, env=env, stdin=stdin)
    _git(repo_root, "restore", f"--source={commit}", "--staged", *from_file, env=env, stdin=stdin)


def _accept_locked(run: RunInfo, result: str, message: str) -> tuple[str, list[str]]:
    """Everything up to and including the branch update; returns the new commit and the winner's paths."""
    root = run.repo_root
    _require_user_identity(root)
    branch_ref = _current_branch_ref(root)
    operation = _operation_in_progress(root)
    if operation is not None:
        raise _Conflict((), _in_progress_message(operation))
    head = _out(root, "rev-parse", "HEAD")
    changes = _changes(root, run.snapshot, result)
    if not changes:
        raise BestOfRefused(_both("The winner made no changes, there is nothing to accept",
                                  "Победитель ничего не изменил, принимать нечего"))
    paths = [path for _, path in changes]
    moved = _paths_changed_since_snapshot(root, run.snapshot, changes)
    if moved:
        raise _Conflict(moved, _both(
            "You changed these files after the run started: ", "Ты изменил эти файлы после старта прогона: ",
        ) + ", ".join(moved))
    tree = _apply_to_head(root, head, run.snapshot, result, paths)
    signing = _message(_git(root, "config", "--type=bool", "--get", "commit.gpgsign", check=False).stdout) == "true"
    commit = _out(
        root, "commit-tree", tree, "-p", head, "-m", message, *(["-S"] if signing else []),
    )
    updated = _git(root, "update-ref", "-m", f"kvist best-of: accept {run.run_id}", branch_ref, commit, head, check=False)
    if updated.returncode != 0:
        raise _Conflict((), _both(
            "Your branch moved while the change was being prepared; try again",
            "Твоя ветка сдвинулась, пока готовился коммит; повтори принятие",
        ) + "\n" + _message(updated.stderr))
    return commit, paths


def _unsynced_error(commit: str, result: str, paths: Sequence[str], cause: GitError) -> GitError:
    """The branch already holds the commit, so say exactly how to finish the file sync by hand."""
    listed = shlex.join(paths)
    steps = (
        f"git --literal-pathspecs restore --source={result} --worktree -- {listed}\n"
        f"git --literal-pathspecs restore --source={commit} --staged -- {listed}"
    )
    return GitError(_both(
        f"The change is committed as {commit}, but your files could not be brought in line with it ({cause}). "
        f"Run these two commands in the repository; the executor's worktree and branch were kept for this:\n{steps}",
        f"Изменение закоммичено как {commit}, но привести твои файлы в соответствие не удалось ({cause}). "
        f"Выполни в репозитории эти две команды; worktree и ветка исполнителя оставлены для этого:\n{steps}",
    ))


def accept(run: RunInfo, executor_id: str, message: str) -> AcceptResult:
    """Commit the winner's change onto the user's current branch. On any conflict nothing in the repository changes.

    Once the branch has moved the run is recorded as accepted; if syncing the files then fails, GitError says how to finish.
    """
    result = _recorded(run, executor_id)
    if run.state == STATE_ACCEPTED:
        raise BestOfRefused(_both("This run is already accepted", "Этот прогон уже принят"))
    with _repo_lock(run.repo_root):
        try:
            commit, paths = _accept_locked(run, result, message)
        except _Conflict as conflict:
            return AcceptResult(conflict_paths=conflict.paths, conflict_text=conflict.text)
        run.state = STATE_ACCEPTED
        run.accepted_commit = commit
        _write_run(run)
        try:
            _sync_files(run.repo_root, commit, result, paths)
        except GitError as error:
            raise _unsynced_error(commit, result, paths, error) from error
        _remove_run_branches_and_worktrees(run)
    return AcceptResult(commit=commit)


# ---------------------------------------------------------------- discarding and recovering

def discard(run: RunInfo) -> None:
    """Remove the worktrees, branches and the run folder; the user's repository is not touched."""
    with _repo_lock(run.repo_root):
        _remove_run_branches_and_worktrees(run)
        if run.run_dir.exists():
            shutil.rmtree(run.run_dir)


def recover(workspace: Path) -> list[RunInfo]:
    """Re-read the runs left on disk, fix what a crash broke, and return those awaiting a decision or a look."""
    repo_root = _find_repo_root(Path(workspace))
    if repo_root is None:
        return []
    waiting: list[RunInfo] = []
    with _repo_lock(repo_root):
        for run_file in sorted((repo_root / ".kvist" / "bestof").glob(f"*/{RUN_FILE}")):
            try:
                run = _read_run(run_file, repo_root)
            except BestOfRefused as refused:
                _logger.warning("skipping unreadable best-of run: %s", refused)
                continue
            if _repair(run):
                _write_run(run)
            if run.state in _WAITING:
                waiting.append(run)
        _git(repo_root, "worktree", "prune")
    return waiting


def _repair(run: RunInfo) -> bool:
    changed = False
    if run.state == STATE_RUNNING and not _owner_alive(run):
        run.state = STATE_INTERRUPTED
        changed = True
    if run.state == STATE_ACCEPTED:
        return changed
    for info in run.executors.values():
        if info.status != EXECUTOR_LOST and not run.worktree(info.executor_id).is_dir():
            info.status = EXECUTOR_LOST
            changed = True
    return changed
