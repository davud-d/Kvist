# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

import pytest

from kernel.context.system_context import scratchpad_context
from kernel.sandbox.executor import ExecutorError, SafeExecutor
from kernel.sandbox.sandbox import SandboxMode, wrap_command_for_sandbox
from kernel.sandbox.scratchpad import Scratchpad, ScratchpadFull
from kernel.tests.conftest import requires_posix


@pytest.fixture
def executor(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    found = SafeExecutor(workspace)
    yield found
    found.scratchpad.clear()


def test_the_path_is_named_before_anything_exists():
    scratch = Scratchpad()
    assert not scratch.path.exists()
    assert scratch.ensure() == scratch.path
    assert oct(scratch.path.stat().st_mode & 0o777) == "0o700"
    scratch.clear()
    assert not scratch.path.exists()


def test_two_pads_never_share_a_directory():
    assert Scratchpad().path != Scratchpad().path


def test_a_write_over_the_limit_is_refused_and_an_overwrite_counts_only_the_difference():
    scratch = Scratchpad(limit_bytes=100)
    scratch.ensure()
    (scratch.path / "a").write_bytes(b"x" * 90)
    with pytest.raises(ScratchpadFull):
        scratch.check_room(20)
    scratch.check_room(95, replaced_bytes=90)
    scratch.clear()


@requires_posix
def test_files_written_through_the_executor_land_outside_the_workspace(executor):
    target = executor.scratchpad.path / "try" / "t.py"
    result = executor._write_file(str(target), "print(1)\n")
    assert result.ok and result.data["bytes_written"] == 9
    executor._append_file(str(target), "print(2)\n")
    assert target.read_text() == "print(1)\nprint(2)\n"
    assert not list(executor.workspace.iterdir())
    assert "print(2)" in executor._read_file(str(target)).data["content"]


@requires_posix
def test_a_write_can_not_leave_through_a_symlink(executor, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    pad = executor.scratchpad.ensure()
    (pad / "link").symlink_to(outside)
    with pytest.raises(ExecutorError):
        executor._write_file(str(pad / "link" / "x.txt"), "no")
    assert not (outside / "x.txt").exists()


@requires_posix
def test_a_planted_final_symlink_is_not_followed(executor, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    pad = executor.scratchpad.ensure()
    (pad / "t.py").symlink_to(victim)
    with pytest.raises(ExecutorError):
        executor._write_file(str(pad / "t.py"), "overwritten")
    assert victim.read_text() == "keep"


@requires_posix
def test_the_executor_refuses_a_write_past_the_limit(executor):
    executor.scratchpad.limit_bytes = 10
    with pytest.raises(ExecutorError, match="at most"):
        executor._write_file(str(executor.scratchpad.path / "big"), "x" * 11)


@requires_posix
def test_an_env_file_stays_off_limits_there_too(executor):
    with pytest.raises(ExecutorError):
        executor._write_file(str(executor.scratchpad.path / ".env"), "KEY=1")


def test_a_path_next_to_the_scratchpad_is_not_in_it(executor):
    sibling = executor.scratchpad.path.parent / (executor.scratchpad.path.name + "-other") / "x"
    assert executor._scratch_target(str(sibling)) is None
    assert executor._scratch_target("relative.py") is None


@requires_posix
def test_helpers_get_the_main_agents_pad(executor):
    helper = SafeExecutor(executor.workspace, scratchpad=executor.scratchpad)
    helper._write_file(str(executor.scratchpad.path / "shared.txt"), "hi")
    assert (executor.scratchpad.path / "shared.txt").read_text() == "hi"


def test_bubblewrap_mounts_the_pad_writable_at_its_own_path(tmp_path):
    workspace = tmp_path / "w"
    workspace.mkdir()
    pad = tmp_path / "pad"
    pad.mkdir()
    argv = wrap_command_for_sandbox(
        ["python3", "t.py"], SandboxMode.BUBBLEWRAP, workspace, workspace,
        bwrap_path="/usr/bin/bwrap", network_allowed=False, scratch=pad,
    )
    at = argv.index("--bind", argv.index("--tmpfs"))
    assert argv[at : at + 3] == ["--bind", str(workspace.resolve()), str(workspace.resolve())]
    assert ["--bind", str(pad), str(pad)] == argv[argv.index(str(pad)) - 1 : argv.index(str(pad)) + 2]
    assert argv[argv.index("KVIST_SCRATCH") + 1] == str(pad)


def test_the_prompt_line_gives_the_path_and_the_limit():
    scratch = Scratchpad()
    line = scratchpad_context(scratch)
    assert str(scratch.path) in line and "500 MB" in line
    assert "\n" not in line


def test_the_windows_branch_writes_by_path_and_refuses_a_foreign_parent(executor, tmp_path):
    target = executor.scratchpad.path / "sub" / "t.py"
    executor._write_scratch_by_path(target, b"a", append=False)
    executor._write_scratch_by_path(target, b"b", append=True)
    assert target.read_bytes() == b"ab"
    with pytest.raises(ExecutorError):
        executor._write_scratch_by_path(tmp_path / "elsewhere" / "x", b"no", append=False)


def test_a_new_session_and_a_switch_empty_the_pad(agent_factory):
    agent = agent_factory()
    pad = agent.executor.scratchpad
    pad.ensure()
    (pad.path / "t.py").write_text("print(1)")
    agent.new_session("second")
    assert not pad.path.exists()
    pad.ensure()
    agent.switch_session(agent.current_session)  # same session: nothing to do
    assert pad.path.exists()
    pad.clear()
