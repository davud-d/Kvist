# -*- coding: utf-8 -*-
"""Stage 1.1.1 regression suite.

Covers the two confirmed CRITICAL execution-boundary defects fixed in this
stage:

CRITICAL-1: secret-named symlinks were skipped during protected-path
enumeration, so a workspace layout such as ``.env -> secret.txt`` leaked
content through the Bubblewrap sandbox despite the mask.

CRITICAL-2: a workspace symlink whose final target was merely "somewhere
under a trusted system root" was treated as a trusted execution origin,
letting an alias such as ``.venv/bin/cat -> /usr/bin/bash`` execute Bash
under a ``cat *`` project-allow rule with no approval.

Tests use the real ``TerminalExecutor`` / ``secret_paths`` / ``sandbox``
production paths wherever practical; strong-sandbox cases skip when
Bubblewrap is unavailable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


from kernel.tests.conftest import SYSTEM_BASH, SYSTEM_SH, requires_posix

from kernel.sandbox import terminal as terminal_module
from kernel.sandbox.sandbox import resolve_bubblewrap_path, SandboxMode
from kernel.sandbox.secret_paths import UnsafeSecretPathError, enumerate_secret_targets
from kernel.sandbox.terminal import ApprovalRequiredError, HardDenyError, TerminalExecutor

pytestmark = pytest.mark.slow

BWRAP = resolve_bubblewrap_path()
requires_bwrap = pytest.mark.skipif(BWRAP is None, reason="bubblewrap not installed")

SECRET = "KVIST_TEST_SECRET_7F13C"


def _write_policy(workspace: Path, allow) -> None:
    kv = workspace / ".kvist"
    kv.mkdir(exist_ok=True)
    (kv / "permissions.json").write_text(json.dumps({"allow": list(allow)}), encoding="utf-8")


def _write_policy_ask(workspace: Path, ask) -> None:
    kv = workspace / ".kvist"
    kv.mkdir(exist_ok=True)
    (kv / "permissions.json").write_text(json.dumps({"ask": list(ask)}), encoding="utf-8")


def _bwrap_executor(workspace: Path, monkeypatch, allow=("*",), timeout=10) -> TerminalExecutor:
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "auto")
    _write_policy(workspace, allow)
    ex = TerminalExecutor(workspace, timeout=timeout)
    assert ex.sandbox_mode is SandboxMode.BUBBLEWRAP
    return ex


def _glob_reader(ws: Path, pattern_literal: str) -> None:
    """Write a reader that globs for the secret path without literal argv text.

    ``pattern_literal`` is the on-disk name (e.g. ``.env``); the source builds
    it via string concatenation so the argv-level lexical secret-token guard
    (a separate, coarser defence-in-depth check) never fires, isolating the
    sandbox mask as the thing under test.
    """
    head, _, rest = pattern_literal.partition(".")
    (ws / "reader.py").write_text(
        "import glob\n"
        f"name = {head!r} + '.' + {rest!r}\n"
        "for f in sorted(glob.glob(name + '/**', recursive=True)) + sorted(glob.glob(name)):\n"
        "    try:\n"
        "        if __import__('os').path.isfile(f):\n"
        "            print(open(f, errors='replace').read())\n"
        "    except OSError:\n"
        "        pass\n",
        encoding="utf-8",
    )


# ───────────────────────── CRITICAL-1: secret symlinks ───────────────────────


@requires_bwrap
def test_env_symlink_to_secret_file_not_readable(tmp_path, monkeypatch):
    (tmp_path / "secret.txt").write_text(f"{SECRET}\n", encoding="utf-8")
    (tmp_path / ".env").symlink_to(tmp_path / "secret.txt")
    _glob_reader(tmp_path, ".env")
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(["python3", "reader.py"])
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


@requires_bwrap
def test_env_symlink_to_secret_in_subdir_not_readable(tmp_path, monkeypatch):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "secret.txt").write_text(f"{SECRET}\n", encoding="utf-8")
    (tmp_path / ".env").symlink_to(sub / "secret.txt")
    _glob_reader(tmp_path, ".env")
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(["python3", "reader.py"])
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


@requires_bwrap
def test_env_symlink_chain_not_readable(tmp_path, monkeypatch):
    (tmp_path / "secret.txt").write_text(f"{SECRET}\n", encoding="utf-8")
    (tmp_path / "link-b").symlink_to("secret.txt")
    (tmp_path / "link-a").symlink_to("link-b")
    (tmp_path / ".env").symlink_to("link-a")
    _glob_reader(tmp_path, ".env")
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(["python3", "reader.py"])
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


@requires_bwrap
def test_ssh_symlink_to_directory_not_readable(tmp_path, monkeypatch):
    real = tmp_path / "real-ssh"
    real.mkdir()
    (real / "config").write_text(f"{SECRET}\n", encoding="utf-8")
    (tmp_path / ".ssh").symlink_to(real)
    (tmp_path / "reader.py").write_text(
        "import glob\n"
        "for f in glob.glob('.ssh/**', recursive=True):\n"
        "    try:\n"
        "        if __import__('os').path.isfile(f):\n"
        "            print(open(f, errors='replace').read())\n"
        "    except OSError:\n"
        "        pass\n",
        encoding="utf-8",
    )
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(["python3", "reader.py"])
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


@requires_bwrap
def test_aws_symlink_to_directory_not_readable(tmp_path, monkeypatch):
    real = tmp_path / "real-aws"
    real.mkdir()
    (real / "credentials-file").write_text(f"{SECRET}\n", encoding="utf-8")
    (tmp_path / ".aws").symlink_to(real)
    (tmp_path / "reader.py").write_text(
        "import glob\n"
        "for f in glob.glob('.aws/**', recursive=True):\n"
        "    try:\n"
        "        if __import__('os').path.isfile(f):\n"
        "            print(open(f, errors='replace').read())\n"
        "    except OSError:\n"
        "        pass\n",
        encoding="utf-8",
    )
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(["python3", "reader.py"])
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


def test_dangling_secret_symlink_enumerates_safely(tmp_path):
    (tmp_path / ".env").symlink_to(tmp_path / "does-not-exist-yet")
    files, dirs = enumerate_secret_targets(tmp_path)
    assert files == []
    assert dirs == []


@requires_bwrap
def test_dangling_secret_symlink_runs_without_leak(tmp_path, monkeypatch):
    (tmp_path / ".env").symlink_to(tmp_path / "does-not-exist-yet")
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(["true"] if os.name != "nt" else ["cmd", "/c", "exit", "0"])
    assert result.returncode == 0


def test_secret_symlink_loop_raises_unsafe(tmp_path):
    (tmp_path / "a").symlink_to("b")
    (tmp_path / "b").symlink_to("a")
    (tmp_path / ".env").symlink_to("a")
    with pytest.raises(UnsafeSecretPathError):
        enumerate_secret_targets(tmp_path)


@requires_posix
def test_secret_symlink_loop_is_hard_denied_end_to_end(tmp_path, monkeypatch):
    (tmp_path / "a").symlink_to("b")
    (tmp_path / "b").symlink_to("a")
    (tmp_path / ".env").symlink_to("a")
    _write_policy(tmp_path, ["*"])
    ex = TerminalExecutor(tmp_path, timeout=5)
    with pytest.raises(HardDenyError, match="cannot build a complete secret mask"):
        ex.run(["true"] if os.name != "nt" else ["cmd", "/c", "exit", "0"])


@requires_bwrap
def test_secret_symlink_target_outside_workspace_not_readable(tmp_path, monkeypatch, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.txt").write_text(f"{SECRET}\n", encoding="utf-8")
    (tmp_path / ".env").symlink_to(outside / "secret.txt")
    _glob_reader(tmp_path, ".env")
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(["python3", "reader.py"])
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


@requires_posix
def test_symlink_target_mutation_during_confirmation_invalidates_approval(tmp_path, monkeypatch):
    """A DANGEROUS command earns HITL; mutating a secret symlink's target
    inside the approval callback must invalidate the bound context so the
    mutated state is never spawned against."""
    (tmp_path / "public.txt").write_text("not secret\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text(f"{SECRET}\n", encoding="utf-8")
    (tmp_path / ".env").symlink_to(tmp_path / "public.txt")
    approvals = []

    def mutate(command, reason):
        approvals.append((command, reason))
        (tmp_path / ".env").unlink()
        (tmp_path / ".env").symlink_to(tmp_path / "secret.txt")
        return True

    _write_policy_ask(tmp_path, ["true *"])
    ex = TerminalExecutor(tmp_path, timeout=5)
    ex._request_user_approval = mutate
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match="approved execution context changed"):
        ex.run(["true", "trigger-ask"])
    assert approvals


def test_symlink_replaced_with_regular_file_during_confirmation_invalidates_approval(tmp_path, monkeypatch):
    (tmp_path / "secret.txt").write_text(f"{SECRET}\n", encoding="utf-8")
    (tmp_path / ".env").symlink_to(tmp_path / "secret.txt")
    approvals = []

    def mutate(command, reason):
        approvals.append((command, reason))
        (tmp_path / ".env").unlink()
        (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
        return True

    _write_policy_ask(tmp_path, ["true *"])
    ex = TerminalExecutor(tmp_path, timeout=5)
    ex._request_user_approval = mutate
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match="approved execution context changed"):
        ex.run(["true", "trigger-ask"])
    assert approvals


def test_regular_secret_file_replaced_with_symlink_during_confirmation_invalidates_approval(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text(f"{SECRET}\n", encoding="utf-8")
    approvals = []

    def mutate(command, reason):
        approvals.append((command, reason))
        (tmp_path / ".env").unlink()
        (tmp_path / ".env").symlink_to(tmp_path / "secret.txt")
        return True

    _write_policy_ask(tmp_path, ["true *"])
    ex = TerminalExecutor(tmp_path, timeout=5)
    ex._request_user_approval = mutate
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match="approved execution context changed"):
        ex.run(["true", "trigger-ask"])
    assert approvals


@requires_bwrap
def test_hardlink_alias_of_resolved_secret_symlink_target_is_masked(tmp_path, monkeypatch):
    real = tmp_path / "real-ssh"
    real.mkdir()
    key_file = real / "config"
    key_file.write_text(f"{SECRET}\n", encoding="utf-8")
    (tmp_path / ".ssh").symlink_to(real)
    os.link(key_file, tmp_path / "innocent_alias.txt")
    (tmp_path / "reader.py").write_text(
        "print(open('innocent_alias.txt', errors='replace').read())\n", encoding="utf-8"
    )
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(["python3", "reader.py"])
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


@requires_bwrap
def test_shell_cannot_read_secret_via_symlink(tmp_path, monkeypatch):
    (tmp_path / "secret.txt").write_text(f"{SECRET}\n", encoding="utf-8")
    (tmp_path / ".env").symlink_to(tmp_path / "secret.txt")
    (tmp_path / "leak.sh").write_text("cat < .env\n", encoding="utf-8")
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(["sh", "leak.sh"])
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


@requires_bwrap
def test_awk_cannot_read_secret_via_symlink(tmp_path, monkeypatch):
    (tmp_path / "secret.txt").write_text(f"{SECRET}\n", encoding="utf-8")
    (tmp_path / ".env").symlink_to(tmp_path / "secret.txt")
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(["awk", 'BEGIN{p="." "env"; getline x < p; print x}'])
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


@requires_bwrap
def test_sed_cannot_read_secret_via_symlink(tmp_path, monkeypatch):
    (tmp_path / "secret.txt").write_text(f"{SECRET}\n", encoding="utf-8")
    (tmp_path / ".env").symlink_to(tmp_path / "secret.txt")
    (tmp_path / "input.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "read.sed").write_text("1r .env\n", encoding="utf-8")
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(["sed", "-n", "--file=read.sed", "input.txt"])
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


# ─────────────────── CRITICAL-2: executable identity impersonation ───────────


def _venv_bin(tmp_path: Path) -> Path:
    vb = tmp_path / ".venv" / "bin"
    vb.mkdir(parents=True)
    return vb


def test_venv_cat_symlink_to_bash_denied_before_project_allow(tmp_path, monkeypatch):
    vb = _venv_bin(tmp_path)
    (vb / "cat").symlink_to(SYSTEM_BASH)
    _write_policy(tmp_path, ["cat *"])
    approvals = []
    ex = TerminalExecutor(tmp_path, timeout=5)
    ex._request_user_approval = lambda c, r: approvals.append((c, r)) or True
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match="symlink resolves outside"):
        ex.run([".venv/bin/cat", "-c", "echo BYPASS > marker"])
    assert approvals == []
    assert not (tmp_path / "marker").exists()


def test_venv_python_symlink_to_bash_denied_before_project_allow(tmp_path, monkeypatch):
    vb = _venv_bin(tmp_path)
    (vb / "python").symlink_to(SYSTEM_BASH)
    (tmp_path / "script.py").write_text("echo BYPASS > marker\n", encoding="utf-8")
    _write_policy(tmp_path, ["python *"])
    approvals = []
    ex = TerminalExecutor(tmp_path, timeout=5)
    ex._request_user_approval = lambda c, r: approvals.append((c, r)) or True
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match="symlink resolves outside"):
        ex.run([".venv/bin/python", "script.py"])
    assert approvals == []
    assert not (tmp_path / "marker").exists()


def test_venv_python3_symlink_to_sh_denied(tmp_path, monkeypatch):
    vb = tmp_path / "venv" / "bin"
    vb.mkdir(parents=True)
    (vb / "python3").symlink_to(SYSTEM_SH)
    (tmp_path / "script.py").write_text("echo BYPASS > marker\n", encoding="utf-8")
    _write_policy(tmp_path, ["python3 *"])
    ex = TerminalExecutor(tmp_path, timeout=5)
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match="symlink resolves outside"):
        ex.run(["venv/bin/python3", "script.py"])
    assert not (tmp_path / "marker").exists()


@requires_posix
def test_workspace_path_cat_alias_to_bash_requires_approval_not_silent_allow(tmp_path, monkeypatch):
    """A bare 'cat' token resolved via the controlled project-toolchain PATH
    (.venv/bin prepended) onto a Bash alias must not silently execute: the
    'cat *' allow rule alone must not authorize it without approval."""
    vb = _venv_bin(tmp_path)
    (vb / "cat").symlink_to(SYSTEM_BASH)
    _write_policy(tmp_path, ["cat *"])
    ex = TerminalExecutor(tmp_path, timeout=5)
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(ApprovalRequiredError):
        ex.run(["cat", "-c", "echo BYPASS > marker"])
    assert not (tmp_path / "marker").exists()


@requires_posix
def test_executable_binding_records_requested_and_effective_identity(tmp_path):
    vb = _venv_bin(tmp_path)
    (vb / "cat").symlink_to(SYSTEM_BASH)
    ex = TerminalExecutor(tmp_path, timeout=5)

    binding = ex._resolve_executable_binding("cat", tmp_path, ex._build_env(None))

    assert binding.token == "cat"
    assert binding.lookup_path == str(vb / "cat")
    assert binding.lookup_in_workspace is True
    assert binding.lookup_is_symlink is True
    assert binding.resolved_path == str(Path(SYSTEM_BASH).resolve())
    assert binding.requested_identity == "cat"
    assert binding.effective_identity == "bash"
    assert binding.identity_matches is False
    assert binding.venv_launcher is False


@requires_posix
def test_executable_binding_records_matching_system_identity(tmp_path):
    ex = TerminalExecutor(tmp_path, timeout=5)

    binding = ex._resolve_executable_binding("cat", tmp_path, ex._build_env(None))

    assert binding.lookup_path is not None
    assert binding.lookup_in_workspace is False
    assert binding.requested_identity == "cat"
    assert binding.effective_identity == "cat"
    assert binding.identity_matches is True


def test_executable_binding_records_verified_venv_launcher(tmp_path, trusted_python_path):
    vb = _venv_bin(tmp_path)
    real_python = trusted_python_path
    (vb / "python").symlink_to(real_python)
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    ex = TerminalExecutor(tmp_path, timeout=5)

    binding = ex._resolve_executable_binding(
        ".venv/bin/python", tmp_path, ex._build_env(None)
    )

    assert binding.lookup_path == str(vb / "python")
    assert binding.lookup_in_workspace is True
    assert binding.lookup_is_symlink is True
    assert binding.resolved_path == str(real_python)
    assert binding.requested_identity == "python"
    assert binding.effective_identity == terminal_module.canonical_executable_name(
        real_python.name
    )
    assert binding.identity_matches == (
        binding.requested_identity == binding.effective_identity
    )
    assert binding.venv_launcher is True


def test_executable_symlink_chain_ending_in_bash_is_denied(tmp_path, monkeypatch):
    vb = _venv_bin(tmp_path)
    (vb / "link-two").symlink_to(SYSTEM_BASH)
    (vb / "python").symlink_to("link-two")
    (tmp_path / "script.py").write_text("echo BYPASS > marker\n", encoding="utf-8")
    _write_policy(tmp_path, ["python *"])
    ex = TerminalExecutor(tmp_path, timeout=5)
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match="symlink resolves outside"):
        ex.run([".venv/bin/python", "script.py"])
    assert not (tmp_path / "marker").exists()


def test_confirmation_callback_cannot_mutate_symlink_to_bash(tmp_path, monkeypatch, trusted_python_path):
    """A legitimate venv launcher approved by HITL must not be swappable for
    Bash between approval and spawn."""
    vb = _venv_bin(tmp_path)
    (vb / "python").symlink_to(trusted_python_path)
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (tmp_path / "script.py").write_text("print('ok')\n", encoding="utf-8")
    _write_policy(tmp_path, [])  # no allow rule: risk assessment path is exercised

    approvals = []

    def mutate(command, reason):
        approvals.append((command, reason))
        (vb / "python").unlink()
        (vb / "python").symlink_to(SYSTEM_BASH)
        return True

    ex = TerminalExecutor(tmp_path, timeout=5)
    ex._request_user_approval = mutate
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError):
        ex.run([".venv/bin/python", "script.py"])
    assert approvals


def test_fake_pyvenv_cfg_does_not_authorize_non_python_target(tmp_path, monkeypatch):
    """A pyvenv.cfg alone (no genuine trusted Python target) must not launder
    an arbitrary interpreter through the venv exception."""
    vb = _venv_bin(tmp_path)
    (vb / "python").symlink_to(SYSTEM_BASH)
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (tmp_path / "script.py").write_text("echo BYPASS > marker\n", encoding="utf-8")
    _write_policy(tmp_path, ["python *"])
    ex = TerminalExecutor(tmp_path, timeout=5)
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match="symlink resolves outside"):
        ex.run([".venv/bin/python", "script.py"])
    assert not (tmp_path / "marker").exists()


def test_valid_venv_python_launcher_still_works(tmp_path, monkeypatch, trusted_python_path):
    vb = _venv_bin(tmp_path)
    (vb / "python").symlink_to(trusted_python_path)
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (tmp_path / "hi.py").write_text("print('VENV_OK')", encoding="utf-8")
    _write_policy(tmp_path, ["*"])
    ex = TerminalExecutor(tmp_path, timeout=10)
    result = ex.run([".venv/bin/python", "hi.py"])
    assert result.returncode == 0
    assert "VENV_OK" in result.stdout


def test_valid_venv_dir_python3_still_works(tmp_path, monkeypatch, trusted_python_path):
    vb = tmp_path / "venv" / "bin"
    vb.mkdir(parents=True)
    (vb / "python3").symlink_to(trusted_python_path)
    (tmp_path / "venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (tmp_path / "hi.py").write_text("print('VENV_OK')", encoding="utf-8")
    _write_policy(tmp_path, ["*"])
    ex = TerminalExecutor(tmp_path, timeout=10)
    result = ex.run(["venv/bin/python3", "hi.py"])
    assert result.returncode == 0
    assert "VENV_OK" in result.stdout


@requires_bwrap
def test_valid_venv_preserves_sys_prefix(tmp_path, monkeypatch, trusted_python_path):
    vb = _venv_bin(tmp_path)
    (vb / "python").symlink_to(trusted_python_path)
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (tmp_path / "hi.py").write_text("import sys; print('prefix', sys.prefix)", encoding="utf-8")
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run([".venv/bin/python", "hi.py"])
    assert result.returncode == 0
    assert str(tmp_path / ".venv") in result.stdout


def test_pyvenv_cfg_mutation_invalidates_approval(tmp_path, monkeypatch, trusted_python_path):
    vb = _venv_bin(tmp_path)
    (vb / "python").symlink_to(trusted_python_path)
    cfg = tmp_path / ".venv" / "pyvenv.cfg"
    cfg.write_text("home = /usr\n", encoding="utf-8")
    (tmp_path / "script.py").write_text("print('ok')\n", encoding="utf-8")
    _write_policy(tmp_path, [])

    approvals = []

    def mutate(command, reason):
        approvals.append((command, reason))
        cfg.write_text("home = /usr\nmutated = true\n", encoding="utf-8")
        return True

    ex = TerminalExecutor(tmp_path, timeout=5)
    ex._request_user_approval = mutate
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match="approved execution context changed"):
        ex.run([".venv/bin/python", "script.py"])
    assert approvals


def test_launcher_chain_mutation_invalidates_approval(tmp_path, monkeypatch, trusted_python_path):
    vb = _venv_bin(tmp_path)
    (vb / "python").symlink_to(trusted_python_path)
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (tmp_path / "script.py").write_text("print('ok')\n", encoding="utf-8")
    _write_policy(tmp_path, [])

    approvals = []

    def mutate(command, reason):
        approvals.append((command, reason))
        (vb / "python").unlink()
        (vb / "python").symlink_to(SYSTEM_BASH)
        return True

    ex = TerminalExecutor(tmp_path, timeout=5)
    ex._request_user_approval = mutate
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError):
        ex.run([".venv/bin/python", "script.py"])
    assert approvals
