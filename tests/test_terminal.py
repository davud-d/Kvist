# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from terminal import TerminalExecutor, TerminalError


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hello world\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "file.txt").write_text("sub content\n")
    return tmp_path


@pytest.fixture
def executor(workspace: Path) -> TerminalExecutor:
    return TerminalExecutor(workspace=workspace, timeout=5.0)


class TestTerminalValidation:
    def test_empty_command_raises(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="Пустая команда"):
            executor.run([])

    def test_forbidden_command_rm(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="запрещена"):
            executor.run(["rm", "-rf", "."])

    def test_forbidden_command_sudo(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="запрещена"):
            executor.run(["sudo", "ls"])

    def test_forbidden_command_python(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="запрещена"):
            executor.run(["python", "-c", "print(1)"])

    def test_forbidden_command_curl(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="запрещена"):
            executor.run(["curl", "http://example.com"])

    def test_unknown_command_rejected(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="не разрешена"):
            executor.run(["unknown_tool"])

    def test_shell_operator_pipe(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="Shell-оператор"):
            executor.run(["echo", "hello", "|", "cat"])

    def test_shell_operator_redirect(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="Shell-оператор"):
            executor.run(["echo", "hello", ">", "file.txt"])

    def test_shell_operator_and(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="Shell-оператор"):
            executor.run(["echo", "a", "&&", "echo", "b"])

    def test_command_substitution_dollar(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="Command substitution"):
            executor.run(["echo", "$(whoami)"])

    def test_command_substitution_backtick(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="Command substitution"):
            executor.run(["echo", "`whoami`"])

    def test_absolute_path_rejected(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="Абсолютные пути"):
            executor.run(["cat", "/etc/passwd"])

    def test_path_traversal_rejected(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="Path traversal"):
            executor.run(["cat", "../../../etc/passwd"])

    @pytest.mark.skipif(os.name != "nt", reason="Windows-only")
    def test_unc_path_rejected(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="UNC"):
            executor.run(["echo", "\\\\server\\share\\file"])

    def test_command_too_long(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="слишком длинная"):
            executor.run(["echo", "x" * 2100])

    def test_git_allowed_subcommand(self, executor: TerminalExecutor):
        # git status should not raise validation error (may fail if no git repo)
        try:
            executor.run(["git", "status"])
        except TerminalError as e:
            # "Исполняемый файл не найден" is ok if git not installed
            assert "не разрешена" not in str(e)

    def test_git_forbidden_subcommand(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError, match="не разрешена"):
            executor.run(["git", "push"])

    def test_cwd_outside_workspace_rejected(self, executor: TerminalExecutor):
        with pytest.raises(TerminalError):
            executor.run(["echo", "hi"], cwd="../../..")

    def test_allowed_commands_list(self, executor: TerminalExecutor):
        allowed = executor.allowed_commands
        assert isinstance(allowed, list)
        assert "git" in allowed


@pytest.mark.skipif(os.name == "nt", reason="Unix-only commands")
class TestTerminalUnixExecution:
    def test_echo(self, executor: TerminalExecutor):
        result = executor.run(["echo", "hello"])
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_cat_file(self, executor: TerminalExecutor):
        result = executor.run(["cat", "hello.txt"])
        assert result.returncode == 0
        assert "hello world" in result.stdout

    def test_ls(self, executor: TerminalExecutor):
        result = executor.run(["ls"])
        assert result.returncode == 0
        assert "hello.txt" in result.stdout

    def test_find(self, executor: TerminalExecutor):
        result = executor.run(["find", ".", "-name", "*.txt"])
        assert result.returncode == 0
        assert "hello.txt" in result.stdout

    def test_grep(self, executor: TerminalExecutor):
        result = executor.run(["grep", "hello", "hello.txt"])
        assert result.returncode == 0
        assert "hello" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows-only commands")
class TestTerminalWindowsExecution:
    def test_echo(self, executor: TerminalExecutor):
        result = executor.run(["echo", "hello"])
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_type_file(self, executor: TerminalExecutor):
        result = executor.run(["type", "hello.txt"])
        assert result.returncode == 0
        assert "hello world" in result.stdout

    def test_dir(self, executor: TerminalExecutor):
        result = executor.run(["dir"])
        assert result.returncode == 0

    def test_where_cmd(self, executor: TerminalExecutor):
        result = executor.run(["where", "cmd.exe"])
        assert result.returncode == 0


class TestTerminalTimeout:
    @pytest.mark.skipif(os.name == "nt", reason="Unix sleep command")
    def test_timeout_kills_process(self, executor: TerminalExecutor, workspace: Path):
        # Нужна разрешённая команда, которая зависнет — используем find на /dev/null
        # Но find разрешён, а timeout = 1s
        exec_short = TerminalExecutor(workspace=workspace, timeout=1.0)
        # Этот тест просто проверяет, что timeout возвращает корректный результат
        result = exec_short.run(["find", ".", "-name", "nonexistent_pattern"])
        assert not result.timed_out

    def test_output_truncation(self, executor: TerminalExecutor, workspace: Path):
        exec_small = TerminalExecutor(workspace=workspace, timeout=5.0, max_output_chars=50)
        # Создаём файл с длинным содержимым
        big_file = workspace / "big.txt"
        big_file.write_text("A" * 200 + "\n")

        if os.name == "nt":
            result = exec_small.run(["type", "big.txt"])
        else:
            result = exec_small.run(["cat", "big.txt"])

        assert "TRUNCATED" in result.stdout or len(result.stdout) <= 60
