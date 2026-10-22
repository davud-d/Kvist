# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from kernel.sandbox import self_info
from kernel.sandbox.self_info import (
    HOST_JETBRAINS,
    HOST_VSCODE,
    HOST_ZED,
    HostProcess,
    ancestor_chain,
    describe_host,
    detect_host,
    self_termination_denial,
)
from kernel.sandbox.terminal import HardDenyError, TerminalExecutor

# Kvist in the VS Code panel on Hyprland: core <- extension host <- code <- Hyprland.
VSCODE_CHAIN = (
    HostProcess(4121, "python3", ("python3", "kernel/__main__.py", "--frontend", "stdio-multi")),
    HostProcess(4088, "code", ("/opt/visual-studio-code/code", "--type=utility")),
    HostProcess(3990, "code", ("/opt/visual-studio-code/code",)),
    HostProcess(812, "Hyprland", ("Hyprland",)),
)


def fake_proc(tmp_path, monkeypatch, processes):
    """processes: (pid, ppid, comm, argv) rows written as /proc/<pid>/{stat,cmdline}."""
    root = tmp_path / "proc"
    for pid, ppid, comm, argv in processes:
        entry = root / str(pid)
        entry.mkdir(parents=True)
        (entry / "stat").write_text(f"{pid} ({comm}) S {ppid} {pid} {pid} 0 -1\n", encoding="utf-8")
        (entry / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in argv) + b"\0")
    monkeypatch.setattr(self_info, "_PROC", root)


def test_chain_walks_parents_up_to_init(tmp_path, monkeypatch):
    fake_proc(tmp_path, monkeypatch, [
        (40, 30, "python3", ["python3", "-m", "kernel"]),
        (30, 20, "fish", ["fish"]),
        (20, 1, "kitty", ["kitty"]),
        (1, 0, "systemd", ["/sbin/init"]),
    ])

    chain = ancestor_chain(40)

    assert [process.pid for process in chain] == [40, 30, 20]
    assert chain[0].argv == ("python3", "-m", "kernel")
    assert chain[0].program == "python3"


def test_comm_with_spaces_and_parenthesis_is_parsed(tmp_path, monkeypatch):
    fake_proc(tmp_path, monkeypatch, [(50, 1, "odd) name", ["odd"])])

    assert ancestor_chain(50) == (HostProcess(50, "odd) name", ("odd",)),)


def test_missing_proc_gives_empty_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(self_info, "_PROC", tmp_path / "absent")

    assert ancestor_chain(123) == ()


def test_parent_loop_terminates(tmp_path, monkeypatch):
    fake_proc(tmp_path, monkeypatch, [(60, 61, "a", ["a"]), (61, 60, "b", ["b"])])

    assert [process.pid for process in ancestor_chain(60)] == [60, 61]


@pytest.mark.parametrize(
    ("process", "host"),
    [
        (HostProcess(3, "code", ("/opt/visual-studio-code/code",)), HOST_VSCODE),
        (HostProcess(3, "electron", ("/opt/visual-studio-code/code",)), HOST_VSCODE),
        (HostProcess(3, "codium", ("codium",)), HOST_VSCODE),
        (
            HostProcess(3, "java", ("java", "-Didea.paths.selector=IU", "com.intellij.idea.Main")),
            HOST_JETBRAINS,
        ),
        (HostProcess(3, "pycharm", ("/opt/pycharm/bin/pycharm",)), HOST_JETBRAINS),
        (HostProcess(3, "zed-editor", ("zed-editor",)), HOST_ZED),
        (HostProcess(3, "kitty", ("kitty",)), ""),
    ],
)
def test_detect_host(process, host):
    chain = (HostProcess(9, "python3", ("python3",)), process)

    assert detect_host(chain) == host


def test_nearest_editor_wins():
    chain = (
        HostProcess(9, "python3", ("python3",)),
        HostProcess(8, "zed", ("zed",)),
        HostProcess(7, "code", ("code",)),
    )

    assert detect_host(chain) == HOST_ZED


def test_describe_host_names_label_and_processes():
    line = describe_host(VSCODE_CHAIN, HOST_VSCODE)

    assert line.startswith("VS Code; Kvist runs inside: python3 (4121) <- code (4088)")
    assert "Hyprland (812)" in line
    assert "Developer: Reload Window" in line
    assert "restart" not in describe_host(VSCODE_CHAIN, "")
    assert describe_host((), "") == ""


@pytest.mark.parametrize(
    ("segment", "stopped"),
    [
        (["kill", "4121"], "python3 (pid 4121)"),
        (["kill", "-9", "3990"], "code (pid 3990)"),
        (["kill", "-s", "KILL", "812"], "Hyprland (pid 812)"),
        (["kill", "-9", "-1"], "every process the user owns"),
        (["kill", "--", "-1"], "every process the user owns"),
        (["pkill", "code"], "code (pid 4088)"),
        (["pkill", "-9", "Hypr"], "Hyprland (pid 812)"),
        (["pkill", "-f", "visual-studio"], "code (pid 4088)"),
        (["pkill", "-f", "stdio-multi"], "python3 (pid 4121)"),
        (["pkill", "-P", "3990"], "code (pid 4088)"),
        (["pkill", "--parent=812"], "code (pid 3990)"),
        (["pkill", "-if", "HYPRLAND"], "Hyprland (pid 812)"),
        (["pkill", "-v", "firefox"], "every process except the pattern"),
        (["killall", "code"], "code (pid 4088)"),
        (["killall", "-I", "HYPRLAND"], "Hyprland (pid 812)"),
        (["killall", "-r", "^pyth"], "python3 (pid 4121)"),
        (["killall", "-s", "TERM", "python3"], "python3 (pid 4121)"),
        (["/usr/bin/pkill", "code"], "code (pid 4088)"),
        (["bash", "-c", "echo bye; pkill code"], "code (pid 4088)"),
        (["bash", "-lc", "killall Hyprland"], "Hyprland (pid 812)"),
        (["fish", "-c", "kill 812"], "Hyprland (pid 812)"),
        (["sh", "-c", "sh -c 'kill 4121'"], "python3 (pid 4121)"),
    ],
)
def test_stopping_a_host_process_is_denied(segment, stopped):
    reason = self_termination_denial(segment, VSCODE_CHAIN, HOST_VSCODE)

    assert reason is not None
    assert reason.startswith("self_termination:")
    assert stopped in reason


@pytest.mark.parametrize(
    "segment",
    [
        ["reboot"],
        ["shutdown", "-h", "now"],
        ["systemctl", "reboot"],
        ["systemctl", "--user", "exit"],
        ["systemctl", "start", "poweroff.target"],
        ["systemctl", "isolate", "multi-user.target"],
        ["systemctl", "restart", "sddm"],
        ["systemctl", "stop", "display-manager.service"],
        ["loginctl", "terminate-session", "2"],
        ["loginctl", "kill-user", "davud"],
        ["hyprctl", "dispatch", "exit"],
        ["hyprctl", "dispatch", "killactive"],
        ["hyprctl", "--batch", "dispatch workspace 2 ; dispatch exit"],
    ],
)
def test_ending_the_session_is_denied(segment):
    reason = self_termination_denial(segment, VSCODE_CHAIN, HOST_VSCODE)

    assert reason is not None
    assert "the login or desktop session" in reason
    assert "ends the login or desktop session" in reason


@pytest.mark.parametrize(
    "segment",
    [
        ["kill", "99999"],
        ["kill", "-l"],
        ["kill", "-1"],  # signal 1 with no target: kill refuses it itself
        ["pkill", "firefox"],
        ["pkill", "-x", "cod"],
        ["pkill", "-P", "99999"],
        ["pkill", "-f", "("],  # invalid pattern: pkill refuses it itself
        ["killall", "firefox"],
        ["killall", "-l"],
        ["systemctl", "status", "reboot.target"],
        ["systemctl", "start", "nginx"],
        ["systemctl", "isolate", "graphical.target"],
        ["systemctl", "restart", "docker"],
        ["loginctl", "list-sessions"],
        ["hyprctl", "reload"],
        ["hyprctl", "dispatch", "workspace", "2"],
        ["bash", "-c", "echo pkill code"],
        ["code", "--install-extension", "kvist.vsix", "--force"],
        ["git", "status"],
    ],
)
def test_ordinary_commands_pass(segment):
    assert self_termination_denial(segment, VSCODE_CHAIN, HOST_VSCODE) is None


@pytest.mark.parametrize(
    ("host", "step"),
    [
        (HOST_VSCODE, "Developer: Reload Window"),
        (HOST_JETBRAINS, "Restart IDE"),
        (HOST_ZED, "quit Zed and open it again"),
        ("", "run this step themselves"),
    ],
)
def test_handoff_names_the_steps_for_the_host(host, step):
    reason = self_termination_denial(["kill", "3990"], VSCODE_CHAIN, host)

    assert step in reason
    assert "/resume" in reason
    assert "/session" in reason
    assert "\n" not in reason  # the runtime keeps a denial's first line as its rule


def test_without_a_chain_only_static_checks_remain():
    assert self_termination_denial(["pkill", "code"], (), "") is None
    assert self_termination_denial(["reboot"], (), "") is not None


def test_terminal_denies_before_any_approval(tmp_path, monkeypatch):
    executor = TerminalExecutor(tmp_path)
    executor.host_chain = VSCODE_CHAIN
    executor.host = HOST_VSCODE
    requested: list = []
    monkeypatch.setattr(
        TerminalExecutor,
        "_request_user_approval",
        classmethod(lambda cls, command, reason: requested.append(command) or True),
    )

    with pytest.raises(HardDenyError, match="self_termination"):
        executor.run(["pkill", "code"])
    with pytest.raises(HardDenyError, match="self_termination"):
        executor.run(["git", "status", "|", "killall", "code"])

    assert requested == []


def test_terminal_passes_ordinary_process_commands(tmp_path):
    executor = TerminalExecutor(tmp_path)
    executor.host_chain = VSCODE_CHAIN

    executor._deny_self_termination(["hyprctl", "reload"])
    executor._deny_self_termination(["kill", "99999"])


def test_terminal_reads_its_own_chain(tmp_path):
    executor = TerminalExecutor(tmp_path)

    if self_info._PROC.exists():
        assert executor.host_chain
        assert executor.host_chain[0].pid > 1


def test_runtime_prompt_names_the_host(agent_factory, monkeypatch):
    agent = agent_factory()
    agent._state.system_host = describe_host(VSCODE_CHAIN, HOST_VSCODE)
    monkeypatch.setattr(
        "kernel.agent.agent_context.current_time_context",
        lambda: {"current_time": "2026-01-01 00:00:00 UTC"},
    )

    prompt = agent._build_runtime_prompt("goal", 1)

    assert "- Host: VS Code; Kvist runs inside: python3 (4121)" in prompt


def test_environment_facts_fill_the_host_line(agent_factory, monkeypatch):
    monkeypatch.setattr("kernel.agent.agent.ancestor_chain", lambda: VSCODE_CHAIN)
    agent = agent_factory()

    agent._set_environment_facts()

    assert agent._state.system_host.startswith("VS Code;")


def test_host_line_says_the_conversation_survives_a_restart():
    line = describe_host(VSCODE_CHAIN, HOST_VSCODE)

    assert "A restart loses nothing" in line
    assert "/session" in line
    assert "/resume" in line


def test_chat_turn_carries_the_host_fact(agent_factory, monkeypatch):
    from kernel.tests.conftest import FakeLLMClient

    agent = agent_factory(stream=False)
    monkeypatch.setattr("kernel.agent.agent_model._load_role_prompt", lambda role: "chat prompt")
    monkeypatch.setattr(
        "kernel.llm.llm_clients.fallback_candidates_for_model", lambda *args, **kwargs: []
    )
    agent.client = FakeLLMClient(content="ok")
    agent._state.system_host = describe_host(VSCODE_CHAIN, HOST_VSCODE)

    agent._run_chat("перезапусти vs code")

    sent = agent.client.requests[0]["messages"][-1]["content"]
    assert "(Runtime fact: host = VS Code;" in sent
    assert "A restart loses nothing" in sent
    # The fact decorates the wire message only; the stored turn stays clean.
    assert agent._chat_history[-2]["content"] == "перезапусти vs code"


def test_terminal_script_scan_checks_workspace_scripts_without_running_them(tmp_path):
    executor = TerminalExecutor(tmp_path)
    executor.host_chain = VSCODE_CHAIN
    bad = tmp_path / "restart.sh"
    bad.write_text("#!/bin/sh\npkill -x code\n", encoding="utf-8")
    with pytest.raises(HardDenyError, match="self_termination"):
        executor._deny_self_termination(["bash", "restart.sh"])

    bad.write_text("#!/bin/sh\necho pkill\n", encoding="utf-8")
    executor._deny_self_termination(["sh", "./restart.sh"])

    bad.write_text("#!/bin/sh\nbash -c 'kill 4121'\n", encoding="utf-8")
    with pytest.raises(HardDenyError, match="self_termination"):
        executor._deny_self_termination(["./restart.sh"])


def test_terminal_script_scan_does_not_read_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = TerminalExecutor(workspace)
    executor.host_chain = VSCODE_CHAIN
    outside = tmp_path / "outside.sh"
    outside.write_text("pkill -x code\n", encoding="utf-8")

    executor._deny_self_termination(["sh", str(outside)])
