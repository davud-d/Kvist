# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from kernel.bestof import worktrees
from kernel.bestof.worktrees import BestOfRefused, GitError

CYRILLIC = re.compile("[а-яА-Я]")


@pytest.fixture(autouse=True)
def _git_isolated_from_the_user(tmp_path, monkeypatch):
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("NAME", "EMAIL"):
        for who in ("AUTHOR", "COMMITTER"):
            monkeypatch.delenv(f"GIT_{who}_{name}", raising=False)


def git(cwd: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if check and done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {done.stderr}")
    return done.stdout


def lines(count: int = 40) -> str:
    return "".join(f"line{number}\n" for number in range(1, count + 1))


def replace_line(path: Path, number: int, text: str) -> None:
    content = path.read_text(encoding="utf-8").splitlines(keepends=True)
    content[number - 1] = text + "\n"
    path.write_text("".join(content), encoding="utf-8")


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Ann User")
    git(root, "config", "user.email", "ann@example.test")
    (root / "a.txt").write_text(lines(), encoding="utf-8")
    (root / "b.txt").write_text(lines(), encoding="utf-8")
    (root / "data.bin").write_bytes(b"\x00\x01\x02" * 100)
    (root / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    return root


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def repo_state(repo: Path) -> dict:
    """Everything accept() must leave alone on a conflict: HEAD, the index bytes, tracked file bytes."""
    return {
        "head": head(repo),
        "index": (repo / ".git" / "index").read_bytes(),
        "files": {
            path.name: path.read_bytes() for path in repo.iterdir() if path.is_file()
        },
    }


def finished(run, executor_id: str, edit) -> str:
    """Let an executor change files in its workspace and record the result."""
    edit(worktrees.executor_workspace(run, executor_id))
    return worktrees.record_result(run, executor_id)


def assert_bilingual(message: str) -> None:
    assert " / " in message
    assert CYRILLIC.search(message)
    assert re.search("[A-Za-z]", message)


# ------------------------------------------------------------------ preflight

def test_git_does_not_inherit_the_cores_stdin_unless_it_is_given_input(repo, monkeypatch):
    # On Windows a child that shares the pipe another thread is blocked reading waits for that read; the
    # first `git rev-parse` of a turn then stalled the whole agent. Only a real feed may reach git's stdin.
    seen = []
    real_run = subprocess.run

    def spy(*args, **kwargs):
        seen.append(kwargs)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(worktrees.subprocess, "run", spy)
    worktrees._git(repo, "rev-parse", "HEAD")
    worktrees._git(repo, "hash-object", "--stdin", stdin=b"x\n")

    assert seen[0].get("stdin") is subprocess.DEVNULL and "input" not in seen[0]
    assert seen[1].get("input") == b"x\n" and "stdin" not in seen[1]


def test_preflight_refuses_when_git_is_not_installed(repo, monkeypatch):
    monkeypatch.setattr(worktrees.shutil, "which", lambda name: None)
    with pytest.raises(BestOfRefused) as refused:
        worktrees.preflight(repo)
    assert "git" in str(refused.value)
    assert_bilingual(str(refused.value))


def test_preflight_refuses_a_folder_that_is_not_a_repository(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(BestOfRefused) as refused:
        worktrees.preflight(plain)
    assert "git-репозитории" in str(refused.value)
    assert_bilingual(str(refused.value))


def test_preflight_refuses_a_repository_without_commits(tmp_path):
    empty = tmp_path / "fresh"
    empty.mkdir()
    git(empty, "init", "-q", "-b", "main")
    with pytest.raises(BestOfRefused) as refused:
        worktrees.preflight(empty)
    assert "commit" in str(refused.value)
    assert_bilingual(str(refused.value))


def test_preflight_refuses_a_detached_head(repo):
    git(repo, "checkout", "-q", "--detach")
    with pytest.raises(BestOfRefused) as refused:
        worktrees.preflight(repo)
    assert "detached" in str(refused.value)
    assert_bilingual(str(refused.value))


def test_preflight_refuses_while_a_merge_is_in_progress(repo):
    git(repo, "checkout", "-q", "-b", "other")
    replace_line(repo / "a.txt", 3, "other side")
    git(repo, "commit", "-q", "-am", "other")
    git(repo, "checkout", "-q", "main")
    replace_line(repo / "a.txt", 3, "main side")
    git(repo, "commit", "-q", "-am", "main")
    git(repo, "merge", "other", check=False)
    assert (repo / ".git" / "MERGE_HEAD").exists()
    with pytest.raises(BestOfRefused) as refused:
        worktrees.preflight(repo)
    assert "merge" in str(refused.value)
    assert_bilingual(str(refused.value))


@pytest.mark.parametrize(
    ("marker", "name"),
    [("rebase-merge", "rebase"), ("rebase-apply", "rebase"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert")],
)
def test_preflight_refuses_the_other_unfinished_operations(repo, marker, name):
    target = repo / ".git" / marker
    if marker.endswith("HEAD"):
        target.write_text(head(repo) + "\n", encoding="utf-8")
    else:
        target.mkdir()
    with pytest.raises(BestOfRefused) as refused:
        worktrees.preflight(repo)
    assert name in str(refused.value)
    assert_bilingual(str(refused.value))


def test_preflight_reports_the_branch_head_and_workspace_inside_the_repository(repo):
    (repo / "sub" / "proj").mkdir(parents=True)
    found = worktrees.preflight(repo / "sub" / "proj")
    assert found.repo_root == repo.resolve()
    assert found.workspace_rel == Path("sub/proj")
    assert found.branch == "main"
    assert found.head == head(repo)


# ------------------------------------------------------------------ snapshot and create_run

def test_snapshot_holds_tracked_edits_and_new_files_but_not_ignored_or_kvist(repo):
    replace_line(repo / "a.txt", 2, "edited by the user")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    (repo / "ignored.log").write_text("noise\n", encoding="utf-8")
    (repo / ".kvist").mkdir()
    (repo / ".kvist" / "state.txt").write_text("state\n", encoding="utf-8")
    run = worktrees.create_run(repo, "r1", ["e1"])
    names = set(git(repo, "ls-tree", "-r", "--name-only", run.snapshot).split("\n")) - {""}
    assert {"a.txt", "new.txt"} <= names
    assert "ignored.log" not in names
    assert not any(name.startswith(".kvist") for name in names)
    assert "edited by the user" in git(repo, "show", f"{run.snapshot}:a.txt")
    assert git(repo, "rev-parse", f"{run.snapshot}^").strip() == run.head


def test_snapshot_leaves_the_users_index_and_files_untouched(repo):
    replace_line(repo / "a.txt", 2, "edited by the user")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    watched = [repo / "a.txt", repo / "b.txt", repo / "new.txt", repo / "data.bin", repo / ".gitignore"]
    index_before = (repo / ".git" / "index").read_bytes()
    times_before = [path.stat().st_mtime_ns for path in watched]
    content_before = [path.read_bytes() for path in watched]
    worktrees.create_run(repo, "r1", ["e1", "e2"])
    assert (repo / ".git" / "index").read_bytes() == index_before
    assert [path.stat().st_mtime_ns for path in watched] == times_before
    assert [path.read_bytes() for path in watched] == content_before
    assert git(repo, "status", "--porcelain", "--untracked-files=no").split() == ["M", "a.txt"]


def test_snapshot_of_a_workspace_subfolder_still_leaves_out_its_own_kvist(repo):
    project = repo / "sub" / "proj"
    (project / ".kvist").mkdir(parents=True)
    (project / ".kvist" / "log.txt").write_text("log\n", encoding="utf-8")
    (project / "code.py").write_text("print(1)\n", encoding="utf-8")
    run = worktrees.create_run(project, "r1", ["e1"])
    names = set(git(repo, "ls-tree", "-r", "--name-only", run.snapshot).split("\n"))
    assert "sub/proj/code.py" in names
    assert "sub/proj/.kvist/log.txt" not in names


def test_create_run_makes_worktrees_branches_and_run_json(repo):
    replace_line(repo / "a.txt", 2, "edited by the user")
    run = worktrees.create_run(repo, "r1", ["e1", "e2"])
    for executor_id in ("e1", "e2"):
        path = repo.resolve() / ".kvist" / "bestof" / "r1" / executor_id
        assert run.worktree(executor_id) == path
        assert path.is_dir()
        assert git(repo, "rev-parse", "--verify", f"refs/heads/kvist/bestof/r1/{executor_id}").strip() == run.snapshot
        assert git(path, "rev-parse", "--abbrev-ref", "HEAD").strip() == f"kvist/bestof/r1/{executor_id}"
        assert "edited by the user" in (path / "a.txt").read_text(encoding="utf-8")
    assert git(repo, "worktree", "list").count("\n") == 3
    saved = json.loads((repo / ".kvist" / "bestof" / "r1" / "run.json").read_text(encoding="utf-8"))
    assert saved["state"] == "running"
    assert saved["snapshot"] == run.snapshot
    assert saved["owner"]["pid"] == os.getpid()
    assert [item["id"] for item in saved["executors"]] == ["e1", "e2"]
    assert not list((repo / ".kvist" / "bestof" / "r1").glob(".run-*"))


def test_create_run_gives_a_subfolder_workspace_its_own_folder_in_the_worktree(repo):
    project = repo / "sub" / "proj"
    project.mkdir(parents=True)
    (project / "code.py").write_text("print(1)\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "project")
    run = worktrees.create_run(project, "r1", ["e1"])
    folder = worktrees.executor_workspace(run, "e1")
    assert folder == repo.resolve() / ".kvist" / "bestof" / "r1" / "e1" / "sub" / "proj"
    assert (folder / "code.py").read_text(encoding="utf-8") == "print(1)\n"


def test_create_run_makes_the_folder_of_a_workspace_that_git_does_not_track(repo):
    project = repo / "empty-project"
    project.mkdir()
    run = worktrees.create_run(project, "r1", ["e1"])
    assert worktrees.executor_workspace(run, "e1").is_dir()


def test_create_run_cleans_up_when_a_worktree_cannot_be_made(repo):
    git(repo, "branch", "kvist/bestof/r1/e2")
    with pytest.raises(GitError):
        worktrees.create_run(repo, "r1", ["e1", "e2"])
    assert not (repo / ".kvist" / "bestof" / "r1").exists()
    assert git(repo, "worktree", "list").count("\n") == 1
    assert "kvist/bestof/r1/e1" not in git(repo, "branch", "--list")


def test_create_run_refuses_an_existing_run_and_bad_ids(repo):
    worktrees.create_run(repo, "r1", ["e1"])
    with pytest.raises(BestOfRefused) as refused:
        worktrees.create_run(repo, "r1", ["e1"])
    assert_bilingual(str(refused.value))
    for bad in ("../x", "a b", "", "x.lock"):
        with pytest.raises(ValueError):
            worktrees.create_run(repo, bad, ["e1"])
    with pytest.raises(ValueError):
        worktrees.create_run(repo, "r2", ["e1", "e1"])


def test_runs_created_from_several_threads_do_not_collide(repo):
    failures: list[BaseException] = []

    def make(number: int) -> None:
        try:
            worktrees.create_run(repo, f"r{number}", ["e1", "e2"])
        except BaseException as error:  # reported to the main thread below
            failures.append(error)

    threads = [threading.Thread(target=make, args=(number,)) for number in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert git(repo, "worktree", "list").count("\n") == 9


def test_load_run_reads_back_what_create_run_wrote(repo):
    run = worktrees.create_run(repo, "r1", ["e1", "e2"])
    loaded = worktrees.load_run(repo, "r1")
    assert loaded == run
    with pytest.raises(BestOfRefused):
        worktrees.load_run(repo, "nope")


# ------------------------------------------------------------------ results and summaries

def test_record_result_and_summary_count_files_and_lines(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])

    def edit(folder: Path) -> None:
        replace_line(folder / "a.txt", 3, "changed")  # +1 -1
        with (folder / "a.txt").open("a", encoding="utf-8") as handle:
            handle.write("tail1\ntail2\n")  # +2
        (folder / "c.txt").write_text("x\ny\nz\n", encoding="utf-8")  # +3
        (folder / "b.txt").unlink()  # -40
        (folder / "data.bin").write_bytes(b"\xff\x00" * 50)  # binary: 0/0

    result = finished(run, "e1", edit)
    assert git(repo, "rev-parse", "refs/heads/kvist/bestof/r1/e1").strip() == result
    assert git(repo, "rev-parse", f"{result}^").strip() == run.snapshot
    found = worktrees.summary(run, "e1")
    counts = {item.path: (item.added, item.removed) for item in found.files}
    assert counts == {"a.txt": (3, 1), "b.txt": (0, 40), "c.txt": (3, 0), "data.bin": (0, 0)}
    assert (found.added, found.removed) == (6, 41)
    assert "c.txt" in found.stat and "4 files changed" in found.stat
    assert not found.empty
    assert run.executors["e1"].result == result
    assert worktrees.load_run(repo, "r1").executors["e1"].status == "done"


def test_an_executor_that_changed_nothing_has_an_empty_summary(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    (worktrees.executor_workspace(run, "e1") / ".kvist").mkdir()
    (worktrees.executor_workspace(run, "e1") / ".kvist" / "session.json").write_text("{}", encoding="utf-8")
    worktrees.record_result(run, "e1")
    found = worktrees.summary(run, "e1")
    assert found.empty
    assert (found.added, found.removed, found.stat) == (0, 0, "")
    assert worktrees.full_diff(run, "e1") == ""


def test_summary_and_diff_need_a_recorded_result(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    with pytest.raises(BestOfRefused) as refused:
        worktrees.summary(run, "e1")
    assert_bilingual(str(refused.value))
    with pytest.raises(BestOfRefused):
        worktrees.full_diff(run, "e1")
    with pytest.raises(BestOfRefused):
        worktrees.accept(run, "e1", "message")
    with pytest.raises(ValueError):
        worktrees.record_result(run, "unknown")


def test_full_diff_can_be_limited_to_a_path_and_is_cut_with_a_marker(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])

    def edit(folder: Path) -> None:
        (folder / "a.txt").write_text("".join(f"new{number}\n" for number in range(200)), encoding="utf-8")
        replace_line(folder / "b.txt", 1, "only b")

    finished(run, "e1", edit)
    only_b = worktrees.full_diff(run, "e1", path="b.txt")
    assert "only b" in only_b and "a.txt" not in only_b
    whole = worktrees.full_diff(run, "e1")
    assert "new199" in whole and "truncated" not in whole
    cut = worktrees.full_diff(run, "e1", limit=100)
    assert cut.startswith(whole[:100])
    assert "truncated" in cut and "обрезан" in cut
    with pytest.raises(ValueError):
        worktrees.full_diff(run, "e1", limit=0)


# ------------------------------------------------------------------ accept

def test_accept_on_a_clean_tree_adds_one_commit_with_only_the_winners_files(repo):
    old_head = head(repo)
    run = worktrees.create_run(repo, "r1", ["e1", "e2"])
    finished(run, "e2", lambda folder: (folder / "loser.txt").write_text("loser\n", encoding="utf-8"))

    def edit(folder: Path) -> None:
        replace_line(folder / "a.txt", 5, "winner line")
        (folder / "c.txt").write_text("brand new\n", encoding="utf-8")
        (folder / "b.txt").unlink()

    finished(run, "e1", edit)
    outcome = worktrees.accept(run, "e1", "Take the winner\n\nBody.")
    assert outcome.accepted and outcome.conflict_paths == ()
    assert git(repo, "rev-list", "--count", "main").strip() == "2"
    assert head(repo) == outcome.commit
    assert git(repo, "rev-parse", "HEAD^").strip() == old_head
    changed = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").split()
    assert sorted(changed) == ["a.txt", "b.txt", "c.txt"]
    assert git(repo, "log", "-1", "--format=%an <%ae>|%cn|%B", "HEAD").startswith("Ann User <ann@example.test>|Ann User|Take the winner")
    assert git(repo, "status", "--porcelain", "--untracked-files=no") == ""
    assert not (repo / "b.txt").exists() and not (repo / "loser.txt").exists()
    assert "winner line" in (repo / "a.txt").read_text(encoding="utf-8")
    for executor_id in ("e1", "e2"):
        assert not run.worktree(executor_id).exists()
    assert git(repo, "branch", "--list", "kvist/*").strip() == ""
    assert git(repo, "worktree", "list").count("\n") == 1
    assert run.state == "accepted" and run.accepted_commit == outcome.commit
    assert worktrees.load_run(repo, "r1").accepted_commit == outcome.commit
    with pytest.raises(BestOfRefused):
        worktrees.accept(run, "e1", "again")


def test_accept_keeps_the_users_uncommitted_edits_in_other_files_uncommitted(repo):
    replace_line(repo / "b.txt", 10, "users own edit")
    (repo / "scratch.txt").write_text("mine\n", encoding="utf-8")
    run = worktrees.create_run(repo, "r1", ["e1"])
    finished(run, "e1", lambda folder: replace_line(folder / "a.txt", 5, "winner line"))
    outcome = worktrees.accept(run, "e1", "winner")
    assert outcome.accepted
    assert git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").split() == ["a.txt"]
    assert "users own edit" not in git(repo, "show", "HEAD:b.txt")
    assert "users own edit" in (repo / "b.txt").read_text(encoding="utf-8")
    assert (repo / "scratch.txt").read_text(encoding="utf-8") == "mine\n"
    assert git(repo, "status", "--porcelain", "--untracked-files=all").splitlines() == [
        " M b.txt", "?? scratch.txt",
    ]


def test_accept_in_the_same_file_commits_only_the_winners_part(repo):
    replace_line(repo / "a.txt", 2, "users edit near the top")
    run = worktrees.create_run(repo, "r1", ["e1"])
    finished(run, "e1", lambda folder: replace_line(folder / "a.txt", 38, "winner edit near the bottom"))
    outcome = worktrees.accept(run, "e1", "winner")
    assert outcome.accepted
    committed = git(repo, "show", "HEAD:a.txt").splitlines()
    assert committed[1] == "line2" and committed[37] == "winner edit near the bottom"
    on_disk = (repo / "a.txt").read_text(encoding="utf-8").splitlines()
    assert on_disk[1] == "users edit near the top" and on_disk[37] == "winner edit near the bottom"
    unstaged = git(repo, "diff")
    assert "users edit near the top" in unstaged and "winner edit" not in unstaged
    assert git(repo, "diff", "--cached") == ""


def test_accept_conflicts_when_the_user_changed_a_file_of_the_change_after_the_start(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    finished(run, "e1", lambda folder: replace_line(folder / "a.txt", 5, "winner line"))
    replace_line(repo / "a.txt", 30, "edited after the start")
    before = repo_state(repo)
    outcome = worktrees.accept(run, "e1", "winner")
    assert not outcome.accepted
    assert outcome.conflict_paths == ("a.txt",)
    assert "a.txt" in outcome.conflict_text
    assert_bilingual(outcome.conflict_text)
    assert repo_state(repo) == before
    assert run.state == "running" and run.worktree("e1").is_dir()
    assert git(repo, "rev-parse", "--verify", "refs/heads/kvist/bestof/r1/e1")


def test_accept_conflicts_when_the_user_deleted_a_file_of_the_change(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    finished(run, "e1", lambda folder: replace_line(folder / "a.txt", 5, "winner line"))
    (repo / "a.txt").unlink()
    before = repo_state(repo)
    outcome = worktrees.accept(run, "e1", "winner")
    assert outcome.conflict_paths == ("a.txt",)
    assert repo_state(repo) == before


def test_accept_conflicts_when_the_user_made_a_file_the_winner_also_added(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    finished(run, "e1", lambda folder: (folder / "c.txt").write_text("winner\n", encoding="utf-8"))
    (repo / "c.txt").write_text("user\n", encoding="utf-8")
    before = repo_state(repo)
    outcome = worktrees.accept(run, "e1", "winner")
    assert outcome.conflict_paths == ("c.txt",)
    assert repo_state(repo) == before


def test_a_touched_but_unchanged_file_is_not_a_conflict(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    finished(run, "e1", lambda folder: replace_line(folder / "a.txt", 5, "winner line"))
    os.utime(repo / "a.txt", (1, 1))
    assert worktrees.accept(run, "e1", "winner").accepted


def test_accept_conflicts_when_the_winner_overlaps_uncommitted_lines_of_the_user(repo):
    replace_line(repo / "a.txt", 5, "users uncommitted line")
    run = worktrees.create_run(repo, "r1", ["e1"])
    finished(run, "e1", lambda folder: replace_line(folder / "a.txt", 6, "winner line"))
    before = repo_state(repo)
    outcome = worktrees.accept(run, "e1", "winner")
    assert not outcome.accepted
    assert outcome.conflict_paths == ("a.txt",)
    assert "patch" in outcome.conflict_text
    assert_bilingual(outcome.conflict_text)
    assert repo_state(repo) == before
    assert run.state == "running"


def test_accept_lays_the_change_over_a_head_that_moved_during_the_run(repo):
    replace_line(repo / "b.txt", 10, "users edit")
    run = worktrees.create_run(repo, "r1", ["e1"])
    finished(run, "e1", lambda folder: replace_line(folder / "a.txt", 5, "winner line"))
    git(repo, "commit", "-q", "-am", "user commits their own work meanwhile")
    moved = head(repo)
    outcome = worktrees.accept(run, "e1", "winner")
    assert outcome.accepted
    assert git(repo, "rev-parse", "HEAD^").strip() == moved
    assert git(repo, "rev-list", "--count", "main").strip() == "3"
    assert git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").split() == ["a.txt"]
    assert "users edit" in git(repo, "show", "HEAD:b.txt")
    assert git(repo, "status", "--porcelain", "--untracked-files=no") == ""


def test_accept_goes_to_the_branch_the_user_is_on_now(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    finished(run, "e1", lambda folder: replace_line(folder / "a.txt", 5, "winner line"))
    git(repo, "checkout", "-q", "-b", "feature")
    outcome = worktrees.accept(run, "e1", "winner")
    assert outcome.accepted
    assert git(repo, "rev-parse", "feature").strip() == outcome.commit
    assert git(repo, "rev-list", "--count", "main").strip() == "1"


def test_accept_conflicts_on_a_detached_head(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    finished(run, "e1", lambda folder: replace_line(folder / "a.txt", 5, "winner line"))
    git(repo, "checkout", "-q", "--detach")
    before = repo_state(repo)
    outcome = worktrees.accept(run, "e1", "winner")
    assert not outcome.accepted and "detached" in outcome.conflict_text
    assert repo_state(repo) == before


def test_accept_refuses_without_a_git_identity_and_changes_nothing(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    finished(run, "e1", lambda folder: replace_line(folder / "a.txt", 5, "winner line"))
    git(repo, "config", "--unset", "user.name")
    git(repo, "config", "--unset", "user.email")
    before = repo_state(repo)
    with pytest.raises(BestOfRefused) as refused:
        worktrees.accept(run, "e1", "winner")
    assert "user.name" in str(refused.value)
    assert_bilingual(str(refused.value))
    assert repo_state(repo) == before
    assert run.state == "running" and run.worktree("e1").is_dir()


def test_accept_stops_when_signing_is_required_and_fails(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    finished(run, "e1", lambda folder: replace_line(folder / "a.txt", 5, "winner line"))
    git(repo, "config", "commit.gpgsign", "true")
    git(repo, "config", "gpg.program", "false")
    before = repo_state(repo)
    with pytest.raises(GitError):
        worktrees.accept(run, "e1", "winner")
    assert repo_state(repo) == before
    assert run.state == "running"


def test_accept_takes_binary_files(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    changed = bytes(range(256)) * 20
    added = b"\x00\xff\x00" * 500

    def edit(folder: Path) -> None:
        (folder / "data.bin").write_bytes(changed)
        (folder / "added.bin").write_bytes(added)

    finished(run, "e1", edit)
    assert worktrees.accept(run, "e1", "binary winner").accepted
    assert (repo / "data.bin").read_bytes() == changed
    assert (repo / "added.bin").read_bytes() == added
    shown = subprocess.run(["git", "show", "HEAD:data.bin"], cwd=repo, capture_output=True, check=True).stdout
    assert shown == changed
    assert git(repo, "status", "--porcelain", "--untracked-files=no") == ""


def test_accept_handles_unusual_file_names(repo):
    # Windows refuses `*` in a file name.
    names = ["we ird[1].txt" if os.name == "nt" else "we ird[1]*.txt", "naïve-имя.txt", "-dash.txt"]
    run = worktrees.create_run(repo, "r1", ["e1"])

    def edit(folder: Path) -> None:
        for name in names:
            (folder / name).write_text(name + "\n", encoding="utf-8")

    finished(run, "e1", edit)
    assert worktrees.accept(run, "e1", "names").accepted
    for name in names:
        assert (repo / name).read_text(encoding="utf-8") == name + "\n"
    committed = git(repo, "-c", "core.quotepath=off", "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD")
    assert sorted(committed.split("\0")[:-1]) == sorted(names)


def test_a_winner_path_with_glob_characters_does_not_match_its_neighbours(repo):
    (repo / "x[12].txt").write_text(lines(), encoding="utf-8")
    (repo / "x1.txt").write_text(lines(), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "similar names")
    run = worktrees.create_run(repo, "r1", ["e1"])
    finished(run, "e1", lambda folder: replace_line(folder / "x[12].txt", 5, "winner line"))
    replace_line(repo / "x1.txt", 5, "users edit of a different file")
    outcome = worktrees.accept(run, "e1", "winner")
    assert outcome.accepted
    assert git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").split() == ["x[12].txt"]
    assert " M x1.txt" in git(repo, "status", "--porcelain", "--untracked-files=no")


def test_accept_of_a_change_in_a_subfolder_workspace(repo):
    project = repo / "sub" / "proj"
    project.mkdir(parents=True)
    (project / "code.py").write_text(lines(), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "project")
    run = worktrees.create_run(project, "r1", ["e1"])
    finished(run, "e1", lambda folder: replace_line(folder / "code.py", 4, "winner"))
    assert worktrees.accept(run, "e1", "winner").accepted
    assert git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").split() == ["sub/proj/code.py"]
    assert "winner" in (project / "code.py").read_text(encoding="utf-8")


def test_accept_of_a_rename_is_a_deletion_plus_an_addition_with_the_right_content(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    original = (repo / "b.txt").read_text(encoding="utf-8")

    def edit(folder: Path) -> None:
        (folder / "b.txt").rename(folder / "renamed.txt")

    finished(run, "e1", edit)
    found = worktrees.summary(run, "e1")
    assert {item.path: (item.added, item.removed) for item in found.files} == {"b.txt": (0, 40), "renamed.txt": (40, 0)}
    assert worktrees.accept(run, "e1", "rename").accepted
    changes = git(repo, "diff-tree", "--no-commit-id", "--name-status", "-r", "--no-renames", "HEAD").split()
    assert changes == ["D", "b.txt", "A", "renamed.txt"]
    assert not (repo / "b.txt").exists()
    assert (repo / "renamed.txt").read_text(encoding="utf-8") == original
    assert git(repo, "status", "--porcelain", "--untracked-files=no") == ""


def test_accept_that_cannot_sync_the_files_is_still_recorded_as_accepted(repo, monkeypatch):
    run = worktrees.create_run(repo, "r1", ["e1"])
    finished(run, "e1", lambda folder: replace_line(folder / "a.txt", 5, "winner line"))
    winner = run.executors["e1"].result

    def broken(*args, **kwargs):
        raise GitError("simulated: index is locked")

    monkeypatch.setattr(worktrees, "_sync_files", broken)
    with pytest.raises(GitError) as failed:
        worktrees.accept(run, "e1", "winner")
    commit = head(repo)
    assert git(repo, "rev-list", "--count", "main").strip() == "2"
    assert commit in str(failed.value) and winner in str(failed.value)
    assert "git --literal-pathspecs restore" in str(failed.value) and "a.txt" in str(failed.value)
    assert "Выполни" in str(failed.value)
    saved = worktrees.load_run(repo, "r1")
    assert saved.state == "accepted" and saved.accepted_commit == commit
    assert run.worktree("e1").is_dir()  # kept: the printed commands need the winner's commit
    with pytest.raises(BestOfRefused):
        worktrees.accept(run, "e1", "again")


requires_posix = pytest.mark.skipif(os.name != "posix", reason="the hook is a shell script")


@requires_posix
def test_service_git_operations_do_not_run_the_users_hooks(repo, tmp_path):
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f'#!/bin/sh\ntouch "{marker}"\n', encoding="utf-8")
    hook.chmod(0o755)
    git(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "control"))
    assert marker.exists()  # the hook does fire for a plain git worktree add
    marker.unlink()
    run = worktrees.create_run(repo, "r1", ["e1", "e2"])
    finished(run, "e1", lambda folder: replace_line(folder / "a.txt", 5, "winner line"))
    assert worktrees.accept(run, "e1", "winner").accepted
    assert not marker.exists()


def test_the_inherited_pathspec_environment_does_not_leak_into_snapshots(repo, monkeypatch):
    (repo / ".kvist").mkdir()
    (repo / ".kvist" / "state.txt").write_text("state\n", encoding="utf-8")
    for name in ("GIT_LITERAL_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS"):
        monkeypatch.setenv(name, "1")
    run = worktrees.create_run(repo, "r1", ["e1"])
    names = set(git(repo, "ls-tree", "-r", "--name-only", run.snapshot).split("\n")) - {""}
    assert names == {"a.txt", "b.txt", "data.bin", ".gitignore"}


def test_the_users_git_does_not_see_the_nested_worktrees(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    finished(run, "e1", lambda folder: (folder / "c.txt").write_text("winner\n", encoding="utf-8"))
    assert ".kvist" not in git(repo, "status", "--porcelain", "--untracked-files=all")
    assert (repo / ".kvist" / "bestof" / ".gitignore").read_text(encoding="utf-8") == "*\n"
    git(repo, "add", "-A")
    try:
        assert git(repo, "diff", "--cached", "--name-only") == ""
    finally:
        git(repo, "reset", "-q")  # the repository is the test's own and had a clean index
    worktrees.create_run(repo, "r2", ["e1"])
    assert (repo / ".kvist" / "bestof" / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_accept_refuses_an_executor_that_changed_nothing(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    worktrees.record_result(run, "e1")
    with pytest.raises(BestOfRefused) as refused:
        worktrees.accept(run, "e1", "nothing")
    assert_bilingual(str(refused.value))
    assert git(repo, "rev-list", "--count", "main").strip() == "1"


# ------------------------------------------------------------------ discard, mark, recover

def test_discard_removes_the_whole_run_and_leaves_the_repository_as_it_was(repo):
    replace_line(repo / "a.txt", 2, "users edit")
    run = worktrees.create_run(repo, "r1", ["e1", "e2"])
    finished(run, "e1", lambda folder: replace_line(folder / "b.txt", 5, "winner line"))
    before = repo_state(repo)
    worktrees.discard(run)
    assert not (repo / ".kvist" / "bestof" / "r1").exists()
    assert git(repo, "branch", "--list", "kvist/*").strip() == ""
    assert git(repo, "worktree", "list").count("\n") == 1
    assert repo_state(repo) == before
    assert git(repo, "status", "--porcelain", "--untracked-files=no").split() == ["M", "a.txt"]


def test_a_cancelled_run_stays_on_disk_until_it_is_discarded(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    worktrees.mark(run, "cancelled")
    assert worktrees.load_run(repo, "r1").state == "cancelled"
    assert run.worktree("e1").is_dir()
    assert [item.run_id for item in worktrees.recover(repo)] == ["r1"]
    worktrees.discard(run)
    assert worktrees.recover(repo) == []
    with pytest.raises(ValueError):
        worktrees.mark(run, "accepted")


def _write_owner(repo: Path, run_id: str, pid: int, start: int | None) -> None:
    run_file = repo / ".kvist" / "bestof" / run_id / "run.json"
    data = json.loads(run_file.read_text(encoding="utf-8"))
    data["owner"] = {"pid": pid, "start": start}
    run_file.write_text(json.dumps(data), encoding="utf-8")


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def test_recover_marks_a_run_whose_owner_died_as_interrupted(repo):
    worktrees.create_run(repo, "r1", ["e1"])
    _write_owner(repo, "r1", _dead_pid(), None)
    found = worktrees.recover(repo)
    assert [(item.run_id, item.state) for item in found] == [("r1", "interrupted")]
    assert worktrees.load_run(repo, "r1").state == "interrupted"
    assert found[0].worktree("e1").is_dir()


def test_recover_leaves_a_run_of_a_live_owner_alone(repo):
    worktrees.create_run(repo, "r1", ["e1"])
    assert worktrees.recover(repo) == []
    assert worktrees.load_run(repo, "r1").state == "running"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="process start times come from /proc")
def test_recover_does_not_mistake_a_reused_pid_for_the_owner(repo):
    worktrees.create_run(repo, "r1", ["e1"])
    real_start = worktrees._process_start_time(os.getpid())
    assert real_start is not None
    _write_owner(repo, "r1", os.getpid(), real_start + 12345)
    assert [item.state for item in worktrees.recover(repo)] == ["interrupted"]


def test_recover_keeps_a_run_that_waits_for_a_decision(repo):
    run = worktrees.create_run(repo, "r1", ["e1"])
    worktrees.mark(run, "pending")
    _write_owner(repo, "r1", _dead_pid(), None)
    found = worktrees.recover(repo)
    assert [(item.run_id, item.state) for item in found] == [("r1", "pending")]


def test_recover_marks_an_executor_with_a_deleted_worktree_lost_and_prunes_git(repo):
    run = worktrees.create_run(repo, "r1", ["e1", "e2"])
    worktrees.mark(run, "pending")
    gone = run.worktree("e1")
    import shutil
    shutil.rmtree(gone)
    # git prints forward slashes on Windows.
    assert gone.as_posix() in git(repo, "worktree", "list", "--porcelain")
    found = worktrees.recover(repo)
    assert len(found) == 1
    assert {key: info.status for key, info in found[0].executors.items()} == {"e1": "lost", "e2": "ready"}
    assert worktrees.load_run(repo, "r1").executors["e1"].status == "lost"
    assert str(gone) not in git(repo, "worktree", "list", "--porcelain")
    assert run.worktree("e2").as_posix() in git(repo, "worktree", "list", "--porcelain")


def test_recover_skips_an_unreadable_run_file_and_logs_it(repo, caplog):
    worktrees.create_run(repo, "good", ["e1"])
    broken = repo / ".kvist" / "bestof" / "broken"
    broken.mkdir()
    (broken / "run.json").write_text("{not json", encoding="utf-8")
    _write_owner(repo, "good", _dead_pid(), None)
    with caplog.at_level("WARNING"):
        found = worktrees.recover(repo)
    assert [item.run_id for item in found] == ["good"]
    assert "unreadable best-of run" in caplog.text


def test_recover_in_a_folder_without_git_finds_nothing(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert worktrees.recover(plain) == []


# ------------------------------------------------- notebooks and test-run artifacts

def _changed_paths(run, executor_id: str) -> list[str]:
    return sorted(change.path for change in worktrees.summary(run, executor_id).files)


@pytest.mark.parametrize("tracked", [True, False])
def test_the_result_leaves_out_kvists_notebooks_whether_the_snapshot_holds_them_or_not(repo, tracked):
    (repo / "KVIST.md").write_text("notes\n", encoding="utf-8")
    if tracked:
        (repo / "KVIST-CHAT.md").write_text("chat\n", encoding="utf-8")
    run = worktrees.create_run(repo, "r1", ["e1"])

    def edit(folder: Path) -> None:
        (folder / "KVIST.md").write_text("rewritten by the executor\n", encoding="utf-8")
        (folder / "KVIST-CHAT.md").write_text("so was this\n", encoding="utf-8")
        (folder / "real.txt").write_text("the work\n", encoding="utf-8")

    finished(run, "e1", edit)

    assert _changed_paths(run, "e1") == ["real.txt"]
    assert "KVIST" not in worktrees.full_diff(run, "e1")


def test_the_result_leaves_out_the_notebooks_of_a_workspace_subfolder(repo):
    (repo / "sub").mkdir()
    (repo / "sub" / "KVIST.md").write_text("notes\n", encoding="utf-8")
    (repo / "sub" / "code.py").write_text("x = 1\n", encoding="utf-8")
    run = worktrees.create_run(repo / "sub", "r1", ["e1"])

    def edit(folder: Path) -> None:
        (folder / "KVIST.md").write_text("rewritten\n", encoding="utf-8")
        (folder / "code.py").write_text("x = 2\n", encoding="utf-8")

    finished(run, "e1", edit)

    assert _changed_paths(run, "e1") == ["sub/code.py"]


def test_a_test_run_in_the_worktree_adds_no_files_to_the_result(repo):
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 1) == 2\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "calc")
    run = worktrees.create_run(repo, "r1", ["e1"])
    folder = worktrees.executor_workspace(run, "e1")

    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=folder,
                          capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(folder)})
    assert done.returncode == 0, done.stdout + done.stderr
    # The same run with the cache plugin on, and a compiled module of a subfolder, as a real run leaves them.
    (folder / ".pytest_cache").mkdir(exist_ok=True)
    (folder / ".pytest_cache" / "v").write_text("cache\n", encoding="utf-8")
    assert (folder / "__pycache__").is_dir()

    worktrees.record_result(run, "e1")

    assert worktrees.summary(run, "e1").empty


def test_accept_leaves_the_users_own_notebook_alone(repo):
    (repo / "KVIST.md").write_text("the user's notes\n", encoding="utf-8")
    run = worktrees.create_run(repo, "r1", ["e1"])

    def edit(folder: Path) -> None:
        (folder / "KVIST.md").write_text("executor rewrote it\n", encoding="utf-8")
        (folder / "real.txt").write_text("the work\n", encoding="utf-8")

    finished(run, "e1", edit)
    outcome = worktrees.accept(run, "e1", "Take it")

    assert outcome.accepted
    assert (repo / "KVIST.md").read_text(encoding="utf-8") == "the user's notes\n"
    assert git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").split() == ["real.txt"]
    assert "KVIST.md" in git(repo, "status", "--porcelain")  # still the user's untracked file, not committed
