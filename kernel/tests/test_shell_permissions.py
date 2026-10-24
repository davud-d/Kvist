# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import socket
import sys
import threading
from types import SimpleNamespace

import pytest

from kernel.sandbox import sandbox

from kernel.sandbox import terminal
from kernel.protocol.event_session import EventApprovalGate
from kernel.sandbox.executor import ExecutorError, SafeExecutor
from kernel.protocol.frontend_protocol import FrontendChannel, InputRequestEvent
from kernel.protocol.parser import CommandValidationError, validate_action_args
from kernel.sandbox.permission_policy import append_allow_rule, load_policy
from kernel.sandbox.sandbox import SandboxMode, wrap_command_for_sandbox
from kernel.sandbox.terminal import HardDenyError, TerminalExecutor
from kernel.tests.conftest import AllowAllApprovals


class Approvals:
    def __init__(self, answers=(True, True)):
        self.answers = iter(answers)
        self.requests = []

    def confirm(self, title, body, question):
        self.requests.append((title, body, question))
        return next(self.answers)

    def confirm_command(self, command, reason):
        return True

    def commit_remembered_rule(self):
        pass


@pytest.fixture
def shell(tmp_path, monkeypatch):
    gate = Approvals()
    executor = TerminalExecutor(tmp_path, approvals=gate)
    calls = []
    monkeypatch.setattr(terminal, "host_network_launcher", lambda allowed: None if allowed else sys.executable)
    monkeypatch.setattr(terminal, "run_supervised", lambda argv, **kw: calls.append((argv, kw)) or SimpleNamespace(
        stdout=b"ok", stderr=b"", stdout_truncated=False, stderr_truncated=False,
        timed_out=False, returncode=0,
    ))
    # Keep real validation and context comparison; only OS spawning is faked.
    append_allow_rule(tmp_path, "echo *")
    return executor, gate, calls


@pytest.mark.parametrize("answers,count", [((False,), 1), ((True, False), 2)])
def test_either_denial_stops_spawn_even_with_allow_rule(shell, answers, count):
    executor, gate, calls = shell
    gate.answers = iter(answers)
    result = executor.run(["echo", "hello"], sandbox="host", network=True, justification="Install dependencies")
    assert result.denied
    assert len(gate.requests) == count
    assert calls == []


def test_permissions_are_separate_per_command_and_not_remembered(shell):
    executor, gate, calls = shell
    gate.answers = iter((True,) * 4)
    for _ in range(2):
        assert executor.run(["echo", "hello"], sandbox="host", network=True, justification="Use the host tool").returncode == 0
    assert [r[0].split(" /")[0] for r in gate.requests] == ["Host execution", "Network access"] * 2
    assert len(calls) == 2
    assert executor._run_sandbox == "default" and executor._run_network is None
    assert load_policy(executor.workspace)[0].allow == ("echo *",)
    assert '"echo", "hello"' in gate.requests[0][1]
    assert str(executor.workspace) in gate.requests[0][1]


def test_ask_user_false_never_spawns_or_prompts(shell):
    executor, gate, calls = shell
    result = executor.run(["echo", "hello"], sandbox="host", network=True, justification="Probe", ask_user=False)
    assert result.denied and not gate.requests and not calls


def test_hard_denial_precedes_permission_dialog(shell):
    executor, gate, calls = shell
    with pytest.raises(HardDenyError):
        executor.run(["cat", ".env"], sandbox="host", network=True, justification="Probe")
    assert not gate.requests and not calls


def test_context_changed_during_capability_dialog_is_rejected(shell, monkeypatch):
    executor, gate, calls = shell
    def confirm(*args):
        monkeypatch.setenv("LANG", "changed_after_approval")
        return True
    gate.confirm = confirm
    with pytest.raises(HardDenyError, match="context changed"):
        executor.run(["echo", "hello"], sandbox="host", network=True, justification="Probe")
    assert not calls


@pytest.mark.parametrize("extra", [
    {"sandbox": []}, {"sandbox": "none"}, {"network": "true"}, {"network": 1},
    {"sandbox": "host"}, {"network": True, "justification": "  "},
])
def test_permission_arguments_are_validated(extra):
    with pytest.raises(CommandValidationError):
        validate_action_args("run_shell", {"command": ["echo", "hello"], **extra})


def test_executor_forwards_permissions_and_defaults(tmp_path, monkeypatch):
    executor = SafeExecutor(tmp_path)
    received = []
    def run(command, **kwargs):
        received.append(kwargs)
        return terminal.TerminalResult(command, 0, "", "")
    monkeypatch.setattr(executor._terminal, "run", run)
    executor._run_shell(["echo", "hello"])
    executor._run_shell(["echo", "hello"], sandbox="host", network=True, justification="Install")
    assert received[0]["network"] is False
    assert received[1]["sandbox"] == "host"
    assert received[1]["network"] is True
    assert received[1]["justification"] == "Install"


@pytest.mark.parametrize("network", [None, "false", 0, 1])
def test_executor_cannot_inherit_network_policy_through_invalid_args(tmp_path, network):
    executor = SafeExecutor(tmp_path)
    with pytest.raises(ExecutorError, match="network must be a boolean"):
        executor._run_shell(["echo", "hello"], network=network)


@pytest.mark.parametrize("answer", ["false", "true", 1, None])
def test_malformed_frontend_answer_cannot_approve_capability(answer):
    channel = SimpleNamespace(request=lambda *args, **kwargs: SimpleNamespace(approved=answer))
    gate = EventApprovalGate(channel)
    assert gate.confirm("Network access", "echo hello", "Allow?") is False


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_offline_host_fails_closed_without_backend(platform, monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", platform)
    monkeypatch.setattr(sandbox, "resolve_bubblewrap_path", lambda: None)
    monkeypatch.setattr(sandbox.Path, "is_file", lambda path: False)
    with pytest.raises(ValueError, match="No command was started"):
        sandbox.host_network_launcher(False)
    assert sandbox.host_network_launcher(True) is None


@pytest.mark.parametrize("network", [False, True])
def test_linux_host_keeps_network_permission_independent(tmp_path, monkeypatch, network):
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    argv = wrap_command_for_sandbox(["/usr/bin/echo", "ok"], SandboxMode.HOST, tmp_path, tmp_path,
                                    bwrap_path="/usr/bin/bwrap", network_allowed=network)
    assert ("--unshare-net" in argv) is (not network)
    assert "--unshare-pid" in argv
    assert argv[-3:] == ["--", "/usr/bin/echo", "ok"]


def test_mac_host_profile_denies_network(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "darwin")
    argv = wrap_command_for_sandbox(["/bin/echo", "ok"], SandboxMode.HOST, tmp_path, tmp_path,
                                    bwrap_path="/usr/bin/sandbox-exec", network_allowed=False)
    assert argv == ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)(deny network*)", "/bin/echo", "ok"]


def test_network_only_preserves_filesystem_sandbox(shell):
    executor, gate, calls = shell
    executor.sandbox_mode = SandboxMode.BUBBLEWRAP
    executor._bwrap_path = sys.executable
    result = executor.run(["echo", "hello"], network=True, justification="Download package")
    assert result.returncode == 0
    assert len(gate.requests) == 1 and gate.requests[0][0].startswith("Network")
    assert "--unshare-net" not in calls[0][0]
    assert calls[0][0][calls[0][0].index("--bind") + 1] == str(executor.workspace)


def test_explicit_offline_overrides_global_allow(shell, monkeypatch):
    executor, gate, calls = shell
    executor.sandbox_mode = SandboxMode.BUBBLEWRAP
    executor._bwrap_path = sys.executable
    monkeypatch.setenv("AGENT_SANDBOX_NET", "allow")
    assert executor.run(["echo", "hello"], network=False).returncode == 0
    assert "--unshare-net" in calls[0][0]
    assert not gate.requests


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable fixture")
def test_host_permission_enables_discovery_without_leaking_host_path(shell, tmp_path, monkeypatch):
    executor, gate, calls = shell
    tools_dir = tmp_path.parent / (tmp_path.name + "-tools")
    tools_dir.mkdir()
    tool = tools_dir / "host-compiler"
    tool.write_text(f"#!{sys.executable}\nprint('compiler')\n", encoding="utf-8")
    tool.chmod(0o700)
    monkeypatch.setenv("PATH", str(tools_dir))
    result = executor.run(["host-compiler", "--version"], sandbox="host", network=True, justification="Find compiler")
    assert result.returncode == 0
    assert calls[0][0][0] == str(tool)
    assert calls[0][1]["env"]["PATH"].split(os.pathsep)[0] == str(tools_dir)
    executor._rotate_child_home()
    assert str(tools_dir) not in executor._build_env(None)["PATH"].split(os.pathsep)


def _no_network_boundary(network):
    raise ValueError("No supported network isolation backend")


def test_a_host_command_where_network_cannot_be_cut_runs_and_says_so(shell, monkeypatch):
    executor, gate, calls = shell
    monkeypatch.setattr(terminal, "host_network_launcher", _no_network_boundary)

    result = executor.run(["echo", "hello"], sandbox="host", network=False, justification="Find compiler")

    assert result.returncode == 0 and len(calls) == 1
    [(title, body, _question)] = gate.requests
    assert title.startswith("Host execution") and sandbox.NETWORK_UNENFORCED_NOTE in body
    assert executor._run_sandbox == "default" and executor._run_network_unenforced is False


def test_a_command_where_network_cannot_be_cut_keeps_its_sandbox_mode_and_is_marked(shell, monkeypatch):
    executor, _gate, _calls = shell
    monkeypatch.setattr(terminal, "host_network_launcher", _no_network_boundary)
    executor.sandbox_mode = SandboxMode.WORKSPACE_ONLY
    executor._run_network = False

    assert executor._effective_sandbox() == (SandboxMode.WORKSPACE_ONLY, True, executor._bwrap_path)
    assert executor._run_network_unenforced is True


@pytest.mark.parametrize("unenforced", [True, False])
def test_an_approval_says_so_only_where_network_cannot_be_cut(shell, monkeypatch, unenforced):
    executor, _gate, _calls = shell
    reasons = []
    monkeypatch.setattr(executor, "_request_user_approval", lambda command, reason: reasons.append(reason) or False)
    executor._run_network_unenforced = unenforced

    executor._approve_or_deny(["tool"], "runs code", lambda: None, offer_rule=False)

    assert reasons[0].startswith("runs code")
    assert (sandbox.NETWORK_UNENFORCED_NOTE in reasons[0]) is unenforced


def test_frontend_channel_has_two_non_rememberable_requests(shell):
    executor, _, calls = shell
    channel = FrontendChannel()
    executor._approvals = EventApprovalGate(channel, workspace=executor.workspace)
    results = []
    worker = threading.Thread(target=lambda: results.append(executor.run(
        ["echo", "hello"], sandbox="host", network=True, justification="Use host tool",
    )), daemon=True)
    worker.start()
    try:
        for expected in ("Host execution", "Network access"):
            request = channel.get(timeout=3)
            assert isinstance(request, InputRequestEvent)
            assert request.kind == "confirm" and request.title.startswith(expected)
            assert request.remember_pattern is None
            assert json.dumps(["echo", "hello"]) in request.body
            channel.respond(request.request_id, approved=True, remember=True)
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert results[0].returncode == 0 and len(calls) == 1
        assert load_policy(executor.workspace)[0].allow == ("echo *",)
    finally:
        channel.close()
        worker.join(timeout=3)


@pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="Native POSIX network backend")
def test_real_host_and_child_have_no_network_until_approved(tmp_path):
    try:
        sandbox.host_network_launcher(False)
    except ValueError:
        pytest.skip("OS network isolation backend is not installed")
    script = tmp_path / "probe.py"
    script.write_text(
        "import socket, subprocess, sys\n"
        "try:\n"
        "    s = socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=0.5)\n"
        "    s.close()\n"
        "    print('online', flush=True)\n"
        "except OSError:\n"
        "    print('offline', flush=True)\n"
        "if len(sys.argv) == 2:\n"
        "    subprocess.run([sys.executable, __file__, sys.argv[1], 'child'], check=True)\n",
        encoding="utf-8",
    )
    executor = TerminalExecutor(tmp_path, approvals=AllowAllApprovals())
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        command = [sys.executable, "probe.py", str(listener.getsockname()[1])]
        for network, expected in ((False, "offline"), (True, "online"), (False, "offline")):
            result = executor.run(command, sandbox="host", network=network, justification="Test local network boundary")
            assert result.returncode == 0, result.stderr
            assert result.stdout.splitlines() == [expected, expected]
