from __future__ import annotations

import json

import pytest

from tests.conftest import requires_posix

from change_journal import (
    build_diff_report,
    clear_journal,
    content_digest,
    journal_path,
    load_baseline,
    record_baseline,
    revert_workspace,
)


def _record_write(workspace, relative_path, *, old_content, new_content, session="default"):
    """Mirror the executor's write-ahead call: record, then mutate."""
    existed = (workspace / relative_path).is_file()
    record_baseline(
        workspace,
        relative_path=relative_path,
        existed=existed,
        content=old_content if existed else None,
        session=session,
        new_digest=content_digest(new_content),
    )
    target = workspace / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_content, encoding="utf-8")


def test_baseline_keeps_first_state_and_refreshes_last_digest(tmp_path):
    (tmp_path / "app.py").write_text("original\n", encoding="utf-8")
    _record_write(tmp_path, "app.py", old_content="original\n", new_content="v1\n")
    _record_write(tmp_path, "app.py", old_content="v1\n", new_content="v2\n")

    baseline = load_baseline(tmp_path)

    assert baseline["app.py"]["content"] == "original\n"
    assert baseline["app.py"]["existed"] is True
    assert baseline["app.py"]["last_digest"] == content_digest("v2\n")
    assert baseline["app.py"]["session"] == "default"


def test_revert_restores_modified_and_deletes_created_files(tmp_path):
    (tmp_path / "kept.py").write_text("before\n", encoding="utf-8")
    _record_write(tmp_path, "kept.py", old_content="before\n", new_content="after\n")
    _record_write(tmp_path, "made.py", old_content=None, new_content="new file\n")

    reverted = revert_workspace(tmp_path)

    actions = {item.path: item.action for item in reverted}
    assert actions == {"kept.py": "restored", "made.py": "deleted"}
    assert (tmp_path / "kept.py").read_text(encoding="utf-8") == "before\n"
    assert not (tmp_path / "made.py").exists()
    assert load_baseline(tmp_path) == {}


def test_revert_skips_files_changed_outside_agent_writes(tmp_path):
    (tmp_path / "user.py").write_text("before\n", encoding="utf-8")
    _record_write(tmp_path, "user.py", old_content="before\n", new_content="agent\n")
    # The user (or a shell command) edits the file after the agent's write.
    (tmp_path / "user.py").write_text("user edit\n", encoding="utf-8")

    reverted = revert_workspace(tmp_path)

    assert [(item.path, item.action) for item in reverted] == [("user.py", "skipped")]
    assert (tmp_path / "user.py").read_text(encoding="utf-8") == "user edit\n"
    # The baseline survives for a later attempt.
    assert load_baseline(tmp_path)["user.py"]["content"] == "before\n"


def test_revert_restores_file_deleted_after_agent_write(tmp_path):
    (tmp_path / "gone.py").write_text("before\n", encoding="utf-8")
    _record_write(tmp_path, "gone.py", old_content="before\n", new_content="agent\n")
    (tmp_path / "gone.py").unlink()

    reverted = revert_workspace(tmp_path)

    assert [(item.path, item.action) for item in reverted] == [("gone.py", "restored")]
    assert (tmp_path / "gone.py").read_text(encoding="utf-8") == "before\n"


def test_revert_is_idempotent_after_interruption(tmp_path):
    (tmp_path / "a.py").write_text("base-a\n", encoding="utf-8")
    _record_write(tmp_path, "a.py", old_content="base-a\n", new_content="agent-a\n")
    _record_write(tmp_path, "b.py", old_content=None, new_content="agent-b\n")

    # Simulate a crash mid-revert: one file already back at its baseline, the
    # journal untouched. Re-running must finish the rest without damage.
    (tmp_path / "a.py").write_text("base-a\n", encoding="utf-8")
    reverted = revert_workspace(tmp_path)

    assert [(item.path, item.action) for item in reverted] == [("b.py", "deleted")]
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "base-a\n"
    assert load_baseline(tmp_path) == {}


def test_stale_write_ahead_record_is_harmless(tmp_path):
    # Write-ahead ordering: the baseline lands, then the mutation fails.
    (tmp_path / "same.py").write_text("untouched\n", encoding="utf-8")
    record_baseline(
        tmp_path,
        relative_path="same.py",
        existed=True,
        content="untouched\n",
        session="default",
        new_digest=content_digest("never written\n"),
    )

    reverted = revert_workspace(tmp_path)

    assert reverted == []
    assert (tmp_path / "same.py").read_text(encoding="utf-8") == "untouched\n"
    assert load_baseline(tmp_path) == {}


def test_entries_without_digest_never_overwrite_existing_content(tmp_path):
    # A journal written by an older Kvist has no last_digest; content that
    # cannot be attributed to the agent is preserved, not overwritten.
    (tmp_path / "legacy.py").write_text("current\n", encoding="utf-8")
    record_baseline(
        tmp_path,
        relative_path="legacy.py",
        existed=True,
        content="old baseline\n",
    )

    reverted = revert_workspace(tmp_path)

    assert [(item.path, item.action) for item in reverted] == [("legacy.py", "skipped")]
    assert (tmp_path / "legacy.py").read_text(encoding="utf-8") == "current\n"


def test_sessions_share_one_workspace_journal_with_attribution(tmp_path):
    (tmp_path / "shared.py").write_text("origin\n", encoding="utf-8")
    _record_write(
        tmp_path, "shared.py", old_content="origin\n", new_content="from-a\n", session="alpha"
    )
    _record_write(
        tmp_path, "shared.py", old_content="from-a\n", new_content="from-b\n", session="beta"
    )

    baseline = load_baseline(tmp_path)
    report = build_diff_report(tmp_path)

    # The baseline is the pre-agent state; the first-touch session is kept
    # for attribution and the digest follows the latest agent write.
    assert baseline["shared.py"]["session"] == "alpha"
    assert baseline["shared.py"]["content"] == "origin\n"
    assert baseline["shared.py"]["last_digest"] == content_digest("from-b\n")
    assert "session: alpha" in report

    reverted = revert_workspace(tmp_path)
    assert [(item.path, item.action) for item in reverted] == [("shared.py", "restored")]
    assert (tmp_path / "shared.py").read_text(encoding="utf-8") == "origin\n"


def test_revert_refuses_tampered_journal_paths(tmp_path):
    journal = journal_path(tmp_path)
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(
        json.dumps(
            {
                "version": 1,
                "baseline": {
                    "../outside.py": {"existed": False, "content": None},
                    ".kvist/sessions/default.json": {"existed": False, "content": None},
                    ".env": {"existed": False, "content": None},
                    ".ssh/config": {"existed": False, "content": None},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path.parent / "outside.py").write_text("outside\n", encoding="utf-8")

    reverted = revert_workspace(tmp_path)

    assert all(item.action == "skipped" for item in reverted)
    assert (tmp_path.parent / "outside.py").read_text(encoding="utf-8") == "outside\n"
    # Nothing safe remained, so the tampered entries stay put (not silently
    # cleared) and no protected path was created or removed.
    assert set(load_baseline(tmp_path)) == {
        "../outside.py",
        ".kvist/sessions/default.json",
        ".env",
        ".ssh/config",
    }


@requires_posix
def test_revert_refuses_paths_through_symlinks(tmp_path):
    outside = tmp_path.parent / "escape-target"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside)
    record_baseline(
        tmp_path,
        relative_path="link/file.py",
        existed=True,
        content="baseline\n",
        new_digest=content_digest("agent\n"),
    )
    (outside / "file.py").write_text("agent\n", encoding="utf-8")

    reverted = revert_workspace(tmp_path)

    assert [(item.path, item.action) for item in reverted] == [
        ("link/file.py", "skipped")
    ]
    assert (outside / "file.py").read_text(encoding="utf-8") == "agent\n"


def test_corrupt_journal_never_blocks_writes_and_reads_empty(tmp_path):
    journal = journal_path(tmp_path)
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("{broken", encoding="utf-8")
    journal.with_suffix(".json.bak").write_text("{broken", encoding="utf-8")

    record_baseline(
        tmp_path,
        relative_path="any.py",
        existed=False,
        content=None,
        new_digest=content_digest("x"),
    )

    assert load_baseline(tmp_path) == {}
    assert revert_workspace(tmp_path) == []


def test_clear_journal_removes_primary_and_backup(tmp_path):
    _record_write(tmp_path, "temp.py", old_content=None, new_content="x\n")
    _record_write(tmp_path, "temp.py", old_content="x\n", new_content="y\n")
    assert journal_path(tmp_path).is_file()

    clear_journal(tmp_path)

    assert not journal_path(tmp_path).exists()
    assert not journal_path(tmp_path).with_suffix(".json.bak").exists()
    assert load_baseline(tmp_path) == {}


def test_diff_report_lists_changes_and_net_noop(tmp_path):
    (tmp_path / "mod.py").write_text("one\n", encoding="utf-8")
    _record_write(tmp_path, "mod.py", old_content="one\n", new_content="two\n")

    report = build_diff_report(tmp_path)
    assert "-one" in report and "+two" in report

    (tmp_path / "mod.py").write_text("one\n", encoding="utf-8")
    assert "No net agent changes remain" in build_diff_report(tmp_path)

    clear_journal(tmp_path)
    assert "journal is empty" in build_diff_report(tmp_path)


@pytest.mark.offline_smoke
def test_executor_records_baseline_before_mutating(agent_factory, monkeypatch):
    """Crash between journal write and file mutation must lose nothing: the
    baseline is durably recorded before the file changes (write-ahead)."""
    agent = agent_factory()
    workspace = agent.executor.workspace
    (workspace / "code.py").write_text("precious\n", encoding="utf-8")

    def crash_replace(self, target, content, expected):
        raise OSError("simulated crash during atomic replace")

    monkeypatch.setattr(
        type(agent.executor), "_atomic_replace_text", crash_replace
    )
    from tests.conftest import make_command

    feedback = agent._execute_command(
        make_command("write_file", {"path": "code.py", "content": "overwrite\n"})
    )

    assert feedback["ok"] is False
    baseline = load_baseline(workspace)
    assert baseline["code.py"]["content"] == "precious\n"
    assert (workspace / "code.py").read_text(encoding="utf-8") == "precious\n"
    # The stale record is spent on revert without touching the file.
    assert revert_workspace(workspace) == []


@pytest.mark.offline_smoke
def test_executor_write_journals_session_and_digest(agent_factory):
    agent = agent_factory()
    agent.executor.set_active_session("feature-x")
    from tests.conftest import make_command

    feedback = agent._execute_command(
        make_command("write_file", {"path": "made.txt", "content": "hello\n"})
    )

    assert feedback["ok"] is True
    baseline = load_baseline(agent.executor.workspace)
    assert baseline["made.txt"]["existed"] is False
    assert baseline["made.txt"]["session"] == "feature-x"
    assert baseline["made.txt"]["last_digest"] == content_digest("hello\n")
