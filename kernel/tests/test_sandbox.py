from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kernel.tests.conftest import requires_posix

from kernel import sandbox
from kernel import terminal as terminal_module
from kernel.permission_policy import policy_file_path
from kernel.sandbox import SandboxMode, detect_sandbox_mode, resolve_bubblewrap_path, wrap_command_for_sandbox
from kernel.terminal import HardDenyError, TerminalExecutor


def fake_which(value):
    return lambda name, path=None: value


def test_detect_bubblewrap_when_available(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(
        sandbox,
        "resolve_bubblewrap_path",
        lambda: "/usr/bin/bwrap",
    )

    mode, note = detect_sandbox_mode("bubblewrap")

    assert mode is SandboxMode.BUBBLEWRAP
    assert note == ""


def test_explicit_bubblewrap_unavailable_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "bubblewrap")
    monkeypatch.setattr(sandbox.shutil, "which", fake_which(None))

    mode, note = detect_sandbox_mode("bubblewrap")
    executor = TerminalExecutor(tmp_path)

    assert mode is SandboxMode.WORKSPACE_ONLY
    assert "blocked" in note.casefold()
    with pytest.raises(HardDenyError, match="mandatory Bubblewrap"):
        executor.run(["echo", "hello"])


def test_detect_auto_uses_restricted_fallback_without_bwrap(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", fake_which(None))

    mode, note = detect_sandbox_mode("auto")

    assert mode is SandboxMode.WORKSPACE_ONLY
    assert "restricted" in note.casefold()


def test_detect_auto_selects_bubblewrap_when_available(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(
        sandbox,
        "resolve_bubblewrap_path",
        lambda: "/usr/bin/bwrap",
    )

    mode, note = detect_sandbox_mode("auto")

    assert mode is SandboxMode.BUBBLEWRAP
    assert note == ""


def test_bubblewrap_lookup_rejects_untrusted_or_mutable_target(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.shutil, "which", fake_which("/usr/bin/bwrap"))
    monkeypatch.setattr(sandbox, "_trusted_system_binary", lambda lookup, target: False)
    assert resolve_bubblewrap_path() is None


def test_bubblewrap_lookup_accepts_trust_checked_absolute_target(
    monkeypatch,
    tmp_path,
):
    binary = tmp_path / "bwrap"
    binary.write_bytes(b"fixture")
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.shutil, "which", fake_which(str(binary)))
    monkeypatch.setattr(
        sandbox,
        "_trusted_system_binary",
        lambda lookup, target: True,
    )

    assert resolve_bubblewrap_path() == str(binary.resolve())


def test_unknown_value_uses_secure_auto_resolution(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", fake_which(None))
    mode, note = detect_sandbox_mode("chroot")
    assert mode is SandboxMode.WORKSPACE_ONLY
    assert "chroot" in note
    assert "restricted" in note.casefold()


def test_wrap_command_noop_for_workspace_only(tmp_path):
    command = ["ls", "-la"]
    wrapped = wrap_command_for_sandbox(command, SandboxMode.WORKSPACE_ONLY, tmp_path, tmp_path)
    assert wrapped == command


def test_wrap_command_builds_minimal_bwrap_invocation(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".kvist").mkdir()
    wrapped = wrap_command_for_sandbox(
        ["ls", "-la"],
        SandboxMode.BUBBLEWRAP,
        workspace,
        workspace,
        bwrap_path="/usr/bin/bwrap",
        network_allowed=False,
    )

    assert wrapped[0] == "/usr/bin/bwrap"
    triples = list(zip(wrapped, wrapped[1:], wrapped[2:]))
    assert ("--ro-bind", "/", "/") not in triples
    assert "/home" not in wrapped
    assert "/root" not in wrapped
    assert "/run" not in wrapped
    assert "--proc" in wrapped and "--dev" in wrapped and "--tmpfs" in wrapped
    assert ["--setenv", "HOME", "/tmp/kvist-home"] == wrapped[
        wrapped.index("--setenv") : wrapped.index("--setenv") + 3
    ]
    assert "--die-with-parent" in wrapped
    assert "--new-session" in wrapped
    assert "--unshare-pid" in wrapped
    assert "--unshare-ipc" in wrapped
    assert "--unshare-uts" in wrapped
    assert "--unshare-net" in wrapped
    bind_index = wrapped.index("--bind")
    assert wrapped[bind_index + 1 : bind_index + 3] == [str(workspace), str(workspace)]
    assert ["--tmpfs", str(workspace / ".kvist")] in [
        wrapped[index : index + 2] for index in range(len(wrapped) - 1)
    ]
    separator_index = wrapped.index("--")
    assert wrapped[separator_index + 1 :] == ["ls", "-la"]


def test_wrap_command_rejects_cwd_outside_workspace(tmp_path):
    workspace = tmp_path / "ws"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()

    with pytest.raises(ValueError, match="cwd must be a directory inside"):
        wrap_command_for_sandbox(
            ["ls"],
            SandboxMode.BUBBLEWRAP,
            workspace,
            outside,
            bwrap_path="/usr/bin/bwrap",
            network_allowed=False,
        )


@requires_posix
def test_optional_runtime_mounts_only_added_when_present(tmp_path, monkeypatch):
    original_exists = Path.exists

    def selected_exists(path):
        if str(path) in {"/usr", "/lib"}:
            return True
        if str(path) in {"/bin", "/sbin", "/lib64"}:
            return False
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", selected_exists)
    wrapped = wrap_command_for_sandbox(
        ["ls"],
        SandboxMode.BUBBLEWRAP,
        tmp_path,
        tmp_path,
        bwrap_path="/usr/bin/bwrap",
        network_allowed=True,
    )
    assert ["--ro-bind", "/usr", "/usr"] in [
        wrapped[index : index + 3] for index in range(len(wrapped) - 2)
    ]
    assert "/lib" in wrapped
    assert "/lib64" not in wrapped


def test_terminal_uses_bubblewrap_when_auto_and_available(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "auto")
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(
        sandbox,
        "resolve_bubblewrap_path",
        lambda: "/usr/bin/bwrap",
    )
    monkeypatch.setattr(
        terminal_module,
        "resolve_bubblewrap_path",
        lambda: "/usr/bin/bwrap",
    )

    executor = TerminalExecutor(tmp_path)

    assert executor.sandbox_mode is SandboxMode.BUBBLEWRAP
    assert executor._bwrap_path == "/usr/bin/bwrap"


def test_auto_fallback_without_tree_control_hard_denies_arbitrary_code(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "auto")
    monkeypatch.setattr(sandbox.shutil, "which", fake_which(None))
    path = policy_file_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"allow": ["python *"], "deny": [], "ask": []}), encoding="utf-8")
    (tmp_path / "safe.py").write_text("print('safe')\n", encoding="utf-8")
    executor = TerminalExecutor(tmp_path)
    executor._cgroup_available = False
    requested = []
    monkeypatch.setattr(
        TerminalExecutor,
        "_request_user_approval",
        classmethod(lambda cls, command, reason: requested.append((command, reason)) or False),
    )

    with pytest.raises(HardDenyError, match="cannot control detached descendants"):
        executor.run(["python", "safe.py"])
    assert requested == []


def test_bwrap_network_is_denied_unless_explicitly_allowed(tmp_path):
    denied = wrap_command_for_sandbox(
        ["ls"], SandboxMode.BUBBLEWRAP, tmp_path, tmp_path,
        bwrap_path="/usr/bin/bwrap", network_allowed=False,
    )
    allowed = wrap_command_for_sandbox(
        ["ls"], SandboxMode.BUBBLEWRAP, tmp_path, tmp_path,
        bwrap_path="/usr/bin/bwrap", network_allowed=True,
    )
    assert "--unshare-net" in denied
    assert "--unshare-net" not in allowed


def test_real_bwrap_hides_host_paths_and_uses_empty_home(tmp_path):
    binary = resolve_bubblewrap_path()
    if binary is None:
        pytest.skip("bubblewrap is not installed")
    command = [
        "/usr/bin/python",
        "-c",
        (
            "import json, os; "
            "print(json.dumps({'home_user': os.path.exists('/home/user'), "
            "'root': os.path.exists('/root'), 'passwd': os.path.exists('/etc/passwd'), "
            "'home': os.environ['HOME'], 'home_entries': os.listdir(os.environ['HOME'])}))"
        ),
    ]
    wrapped = wrap_command_for_sandbox(
        command,
        SandboxMode.BUBBLEWRAP,
        tmp_path,
        tmp_path,
        bwrap_path=binary,
        # Some nested CI containers cannot create a network namespace; host
        # path isolation is independent and remains what this test verifies.
        network_allowed=True,
    )
    completed = subprocess.run(wrapped, capture_output=True, text=True, timeout=10, check=False)
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    assert observed == {
        "home_user": False,
        "root": False,
        "passwd": False,
        "home": "/tmp/kvist-home",
        "home_entries": [],
    }
