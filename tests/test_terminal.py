from __future__ import annotations

import pytest

from terminal import TerminalError, TerminalExecutor


def test_terminal_allows_common_read_only_command(tmp_path):
    executor = TerminalExecutor(tmp_path, timeout=2)
    result = executor.run(["python", "--version"])

    assert result.returncode == 0
    assert "Python" in (result.stdout + result.stderr)


@pytest.mark.parametrize(
    "command,error",
    [
        ([], "Empty command"),
        (["rm", "-rf", "x"], "forbidden"),
        (["git", "reset", "--hard"], "Subcommand"),
        (["python", "-c", "print(1)"], "Inline mode"),
        (["echo", "$(whoami)"], "Command substitution"),
        (["cat", "../secret"], "Path traversal"),
        (["cat", "/etc/passwd"], "Absolute paths"),
        (["sed", "-i", "s/a/b/", "file"], "In-place"),
    ],
)
def test_terminal_validation_rejects_dangerous_inputs(tmp_path, command, error):
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(TerminalError, match=error):
        executor._validate(command)


def test_terminal_restricted_profile_blocks_dev_tools(tmp_path):
    executor = TerminalExecutor(tmp_path, profile="restricted")
    with pytest.raises(TerminalError, match="forbidden"):
        executor._validate(["python", "--version"])


def test_terminal_resolves_cwd_inside_workspace_only(tmp_path):
    (tmp_path / "sub").mkdir()
    executor = TerminalExecutor(tmp_path)

    assert executor._resolve_cwd("sub") == (tmp_path / "sub").resolve()
    with pytest.raises(TerminalError, match="outside workspace|Absolute paths"):
        executor._resolve_cwd("../outside")


def test_terminal_clamps_timeout_and_truncates_output(tmp_path):
    executor = TerminalExecutor(tmp_path, timeout=99, max_output_chars=1000)
    assert executor.timeout == 30.0
    assert executor._clamp_timeout(0.1) == 1.0
    assert executor._clamp_timeout(99) == 30.0
    assert "[TRUNCATED" in executor._truncate("x" * 1001)
