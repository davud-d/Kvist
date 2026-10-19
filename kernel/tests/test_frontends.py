# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from kernel import frontends
from kernel import main
from kernel.agent import LocalAgent
from kernel.agent_state import AgentConfig
from kernel.event_session import EventSession
from kernel.event_stream_ui import EventStreamUI
from kernel.tests.conftest import FakeLLMClient


def test_cli_parser_accepts_frontend_flag():
    args = main._build_cli_parser().parse_args(["--workspace", "x", "--frontend", "stdio"])
    assert args.frontend == "stdio"
    default = main._build_cli_parser().parse_args(["--workspace", "x"])
    assert default.frontend is None  # resolved from the saved UI preference
    assert main._build_cli_parser().parse_args(["--frontend", "tui"]).frontend == "tui"


def test_cli_parser_accepts_every_role_the_core_can_run():
    # The Node frontends pass the saved /role back as --role, so a role the
    # parser rejects would kill the core at startup.
    for role in ("auto", "coder", "reader", "devops", "chat", "universal"):
        assert main._build_cli_parser().parse_args(["--role", role]).role == role


def test_cli_parser_role_defaults_to_none_for_saved_preference_resolution():
    default = main._build_cli_parser().parse_args(["--workspace", "x"])
    assert default.role is None  # resolved from the saved /role preference in main()
    assert main._build_cli_parser().parse_args(["--role", "auto"]).role == "auto"
    assert main._build_cli_parser().parse_args(["--role", "coder"]).role == "coder"


def test_resolve_startup_role_falls_back_to_the_saved_preference_only_when_omitted(monkeypatch):
    monkeypatch.setattr(main, "get_saved_role", lambda: "coder")
    assert main.resolve_startup_role(None) == "coder"
    # An explicit --role always wins, even over a different saved role, and
    # "auto" typed explicitly (the GUI and VS Code extension always send an
    # explicit role) must pass through unchanged rather than being treated as
    # "nothing was specified".
    assert main.resolve_startup_role("reader") == "reader"
    assert main.resolve_startup_role("auto") == "auto"


def test_resolve_startup_role_defaults_to_auto_with_nothing_saved(monkeypatch):
    monkeypatch.setattr(main, "get_saved_role", lambda: None)
    assert main.resolve_startup_role(None) == "auto"


def test_resolve_startup_role_never_persists_an_explicit_override(monkeypatch):
    monkeypatch.setattr(main, "get_saved_role", lambda: "coder")
    saved = []
    monkeypatch.setattr(main, "save_role_selection", lambda *a, **k: saved.append((a, k)))
    assert main.resolve_startup_role("devops") == "devops"
    assert saved == []  # a one-run override is never written back to the saved preference


def test_main_resolves_role_from_the_saved_preference_before_launching_the_tui(monkeypatch, tmp_path):
    captured = {}

    def fake_launch_tui(args, quiet=False):
        captured["role"] = args.role
        return 0

    monkeypatch.setattr(main, "launch_tui", fake_launch_tui)
    monkeypatch.setattr(main, "get_saved_role", lambda: "devops")
    monkeypatch.setattr(main, "_DIRECT_CLI_ARGS", None)
    monkeypatch.setattr(
        main.sys,
        "argv",
        ["main.py", "--workspace", str(tmp_path), "--frontend", "tui"],
    )
    with pytest.raises(SystemExit) as excinfo:
        main.main()
    assert excinfo.value.code == 0
    assert captured["role"] == "devops"


def test_main_lets_an_explicit_role_override_the_saved_preference(monkeypatch, tmp_path):
    captured = {}

    def fake_launch_tui(args, quiet=False):
        captured["role"] = args.role
        return 0

    monkeypatch.setattr(main, "launch_tui", fake_launch_tui)
    monkeypatch.setattr(main, "get_saved_role", lambda: "devops")
    monkeypatch.setattr(main, "_DIRECT_CLI_ARGS", None)
    monkeypatch.setattr(
        main.sys,
        "argv",
        ["main.py", "--workspace", str(tmp_path), "--frontend", "tui", "--role", "reader"],
    )
    with pytest.raises(SystemExit) as excinfo:
        main.main()
    assert excinfo.value.code == 0
    assert captured["role"] == "reader"


def test_load_launcher_builtin_and_unknown():
    assert callable(frontends.load_launcher("stdio"))
    with pytest.raises(RuntimeError):
        frontends.load_launcher("definitely_not_a_frontend")


def test_make_session_injects_event_stream_ui(tmp_path, monkeypatch):
    captured = {}

    def fake_build_agent(executor, **kwargs):
        captured["ui"] = kwargs.get("ui")
        agent = LocalAgent(
            FakeLLMClient(),
            executor,
            AgentConfig(persist_logs=False, api_request_interval=0),
            ui=kwargs["ui"],
        )
        return agent, agent.config, []

    monkeypatch.setattr(main, "build_agent", fake_build_agent)

    session = frontends.make_session(str(tmp_path))

    assert isinstance(session, EventSession)
    assert isinstance(captured["ui"], EventStreamUI)


def test_cli_parser_accepts_session_lifecycle_flags():
    from kernel.main import _build_cli_parser

    parser = _build_cli_parser()
    args = parser.parse_args(
        [
            "--workspace",
            ".",
            "--session-op",
            "switch",
            "--session-name",
            "feature",
        ]
    )
    assert args.session_op == "switch"
    assert args.session_name == "feature"

    resumed = parser.parse_args(["--workspace", ".", "--frontend", "stdio", "--resume"])
    assert resumed.resume is True


def test_session_op_skips_provider_and_terminal_imports(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "importtime",
            str(Path(main.__file__).with_name("__main__.py")),
            "--workspace",
            str(tmp_path),
            "--session-op",
            "status",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1])["ok"] is True
    imported = {line.rsplit("|", 1)[-1].strip() for line in completed.stderr.splitlines()}
    assert not {"openai", "prompt_toolkit", "agent", "banner"} & imported


def test_stdio_launcher_import_is_valid():
    from kernel.event_stdio import serve_stdio
    from kernel.frontends import load_launcher

    launcher = load_launcher("stdio")
    assert callable(launcher)
    assert callable(serve_stdio)


def _tui_args(**overrides):
    args = main._build_cli_parser().parse_args(["--workspace", "ws", "--frontend", "tui"])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _make_tty(monkeypatch, entry):
    import subprocess

    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(main, "_TUI_ENTRY", entry)
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "openai")
    monkeypatch.setattr(main, "resolve_workspace_path", lambda raw: raw)
    calls = []
    monkeypatch.setattr(subprocess, "call", lambda command, env: calls.append((command, env)) or 7)
    return calls


def test_launch_tui_falls_back_without_a_tty(monkeypatch, tmp_path):
    entry = tmp_path / "cli.js"
    entry.write_text("")
    calls = _make_tty(monkeypatch, entry)
    monkeypatch.setattr(main.sys.stdout, "isatty", lambda: False)
    assert main.launch_tui(_tui_args()) is None
    assert calls == []


def test_launch_tui_falls_back_when_not_built(monkeypatch, tmp_path, capsys):
    calls = _make_tty(monkeypatch, tmp_path / "missing.js")
    assert main.launch_tui(_tui_args()) is None
    assert "npm install && npm run build" in capsys.readouterr().err
    assert main.launch_tui(_tui_args(), quiet=True) is None
    assert capsys.readouterr().err == ""
    assert calls == []


def test_launch_tui_opens_setup_when_no_provider_is_saved(monkeypatch, tmp_path):
    entry = tmp_path / "cli.js"
    entry.write_text("")
    calls = _make_tty(monkeypatch, entry)
    monkeypatch.delenv("AGENT_LLM_PROVIDER", raising=False)
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: None)
    assert main.launch_tui(_tui_args()) == 7
    assert "--setup" in calls[-1][0]


def test_launch_tui_leaves_setup_out_once_a_provider_is_known(monkeypatch, tmp_path):
    entry = tmp_path / "cli.js"
    entry.write_text("")
    calls = _make_tty(monkeypatch, entry)
    main.launch_tui(_tui_args())
    assert "--setup" not in calls[-1][0]
    main.launch_tui(_tui_args(setup=True))
    assert "--setup" in calls[-1][0]


def test_installer_opens_the_setup_screen_even_with_a_provider_saved(monkeypatch, tmp_path):
    entry = tmp_path / "cli.js"
    entry.write_text("")
    calls = _make_tty(monkeypatch, entry)
    monkeypatch.setenv("KVIST_INSTALL_SCRIPT", str(tmp_path / "install.sh"))
    assert main.launch_tui(_tui_args(installer=True)) == 7
    command, env = calls[-1]
    assert "--setup" in command
    assert env["KVIST_INSTALL_SCRIPT"] == str(tmp_path / "install.sh")


def test_find_node_prefers_the_installers_node_then_the_installed_one_then_path(monkeypatch, tmp_path):
    import shutil

    def executable(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        return str(path)

    on_path = executable(tmp_path / "path" / "node")
    monkeypatch.setattr(shutil, "which", lambda name: on_path if name == "node" else None)
    monkeypatch.delenv("KVIST_NODE", raising=False)
    assert main.find_node() == on_path

    installed = executable(tmp_path / "lib" / "node" / "bin" / "node")
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.sys, "executable", str(tmp_path / "lib" / "kvist-core"))
    assert main.find_node() == installed

    named = executable(tmp_path / "bootstrap" / "node")
    monkeypatch.setenv("KVIST_NODE", named)
    assert main.find_node() == named

    # A name that is not an executable file is ignored rather than trusted.
    monkeypatch.setenv("KVIST_NODE", str(tmp_path / "missing"))
    assert main.find_node() == installed


def test_installer_without_its_script_refuses_to_start(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("KVIST_INSTALL_SCRIPT", raising=False)
    monkeypatch.setattr(main, "_DIRECT_CLI_ARGS", None)
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--workspace", str(tmp_path), "--installer"])
    with pytest.raises(SystemExit) as excinfo:
        main.main()
    assert excinfo.value.code == 2
    assert "install.sh" in capsys.readouterr().err


def test_setup_asking_for_the_classic_terminal_continues_there_unsaved(monkeypatch, tmp_path):
    entry = tmp_path / "cli.js"
    entry.write_text("")
    saved = []
    monkeypatch.setattr(main, "save_frontend", saved.append)
    _make_tty(monkeypatch, entry)
    monkeypatch.setattr(subprocess, "call", lambda command, env: main._TUI_SETUP_CLASSIC_CODE)
    assert main.launch_tui(_tui_args(setup=True)) is None
    assert saved == []


def test_setup_choice_is_saved_and_the_agent_starts_without_setup(monkeypatch, tmp_path):
    entry = tmp_path / "cli.js"
    entry.write_text("")
    saved = []
    monkeypatch.setattr(main, "save_frontend", saved.append)
    _make_tty(monkeypatch, entry)
    monkeypatch.delenv("AGENT_LLM_PROVIDER", raising=False)
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: None)
    launches = []

    def finish_setup_then_quit(command, env):
        launches.append(command)
        if len(launches) == 1:
            Path(env["KVIST_SWITCH_FILE"]).write_text("claude\n", encoding="utf-8")
            # Setup has saved a provider by the time it asks for the switch.
            monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "anymodel")
            return main._TUI_SWITCH_CODE
        return 0

    monkeypatch.setattr(subprocess, "call", finish_setup_then_quit)
    assert main.launch_tui(_tui_args(setup=False)) == 0
    assert saved == ["claude"]
    assert ["--setup" in command for command in launches] == [True, False]
    assert launches[1][launches[1].index("--skin") + 1] == "claude"


def test_launch_tui_hands_node_the_core_command_and_returns_its_exit_code(monkeypatch, tmp_path):
    import json

    entry = tmp_path / "cli.js"
    entry.write_text("")
    calls = _make_tty(monkeypatch, entry)
    code = main.launch_tui(_tui_args(role="coder", provider="openai"))
    assert code == 7
    command, env = calls[0]
    assert command[1] == str(entry)
    assert command[command.index("--role") + 1] == "coder"
    assert command[command.index("--provider") + 1] == "openai"
    core = json.loads(env["KVIST_CORE_CMD"])
    assert core[0] == main.sys.executable and core[-1].endswith("__main__.py")


def test_tui_switch_reads_the_chosen_interface_and_hands_over(monkeypatch, tmp_path):
    entry = tmp_path / "cli.js"
    entry.write_text("")
    saved = []
    monkeypatch.setattr(main, "save_frontend", saved.append)
    calls = _make_tty(monkeypatch, entry)

    def choose_classic(command, env):
        Path(env["KVIST_SWITCH_FILE"]).write_text("terminal\n", encoding="utf-8")
        return main._TUI_SWITCH_CODE

    monkeypatch.setattr(subprocess, "call", choose_classic)
    assert main.launch_tui(_tui_args()) is None  # None = classic terminal starts
    assert saved == ["terminal"]
    assert calls == []


def test_launch_tui_passes_the_skin_of_the_chosen_frontend(monkeypatch, tmp_path):
    entry = tmp_path / "cli.js"
    entry.write_text("")
    calls = _make_tty(monkeypatch, entry)
    main.launch_tui(_tui_args())
    assert calls[-1][0][calls[-1][0].index("--skin") + 1] == "ink"
    main.launch_tui(_tui_args(frontend="claude"))
    assert calls[-1][0][calls[-1][0].index("--skin") + 1] == "claude"


def test_tui_switch_between_node_interfaces_relaunches_with_the_new_skin(monkeypatch, tmp_path):
    entry = tmp_path / "cli.js"
    entry.write_text("")
    saved = []
    monkeypatch.setattr(main, "save_frontend", saved.append)
    calls = _make_tty(monkeypatch, entry)
    launches = []

    def choose_claude_then_quit(command, env):
        launches.append(command)
        if len(launches) == 1:
            Path(env["KVIST_SWITCH_FILE"]).write_text("claude\n", encoding="utf-8")
            return main._TUI_SWITCH_CODE
        return 0

    monkeypatch.setattr(subprocess, "call", choose_claude_then_quit)
    assert main.launch_tui(_tui_args()) == 0
    assert saved == ["claude"]
    assert [command[command.index("--skin") + 1] for command in launches] == ["ink", "claude"]
    assert calls == []


def test_installer_switch_starts_the_agent_instead_of_reopening_setup(monkeypatch, tmp_path):
    entry = tmp_path / "cli.js"
    entry.write_text("")
    monkeypatch.setattr(main, "save_frontend", lambda value: None)
    monkeypatch.setenv("KVIST_INSTALL_SCRIPT", str(tmp_path / "install.sh"))
    _make_tty(monkeypatch, entry)
    launches = []

    def choose_claude_then_quit(command, env):
        launches.append(command)
        if len(launches) == 1:
            Path(env["KVIST_SWITCH_FILE"]).write_text("claude\n", encoding="utf-8")
            return main._TUI_SWITCH_CODE
        return 0

    monkeypatch.setattr(subprocess, "call", choose_claude_then_quit)
    assert main.launch_tui(_tui_args(installer=True)) == 0
    assert "--setup" in launches[0]
    assert "--setup" not in launches[1]


@pytest.mark.parametrize(
    ("raw", "current", "expected"),
    [
        ("/tui", "terminal", "tui"),
        ("/tui", "tui", "terminal"),
        ("/tui", "claude", "terminal"),
        ("/tui on", "terminal", "tui"),
        ("/tui <claude>", "terminal", "claude"),
        ("/tui off", "claude", "terminal"),
        ("/tui glass", "terminal", "glass"),
        ("/tui voice", "terminal", "voice"),
        ("/tui", "voice", "tui"),
        ("/tui", "glass", "tui"),
        ("/tui hologram", "terminal", None),
        ("/tui on off", "terminal", None),
    ],
)
def test_classic_tui_command_picks_the_frontend_to_save(raw, current, expected):
    assert main.parse_tui_choice(raw, current) == expected


def test_tui_switch_without_a_usable_choice_still_falls_back(monkeypatch, tmp_path, capsys):
    entry = tmp_path / "cli.js"
    entry.write_text("")
    _make_tty(monkeypatch, entry)
    monkeypatch.setattr(subprocess, "call", lambda command, env: main._TUI_SWITCH_CODE)
    assert main.launch_tui(_tui_args()) is None  # no file written

    def choose_unknown(command, env):
        Path(env["KVIST_SWITCH_FILE"]).write_text("hologram", encoding="utf-8")
        return main._TUI_SWITCH_CODE

    monkeypatch.setattr(subprocess, "call", choose_unknown)
    assert main.launch_tui(_tui_args()) is None
    assert "Unknown interface" in capsys.readouterr().err


def test_launch_tui_says_what_is_missing_in_a_packaged_build(monkeypatch, tmp_path, capsys):
    calls = _make_tty(monkeypatch, tmp_path / "missing.mjs")
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    assert main.launch_tui(_tui_args()) is None
    err = capsys.readouterr().err
    # A packaged build cannot be fixed with npm; it has to be rebuilt.
    assert "made without it" in err and "npm install" not in err
    assert calls == []


def test_launch_tui_needs_node_on_path(monkeypatch, tmp_path, capsys):
    entry = tmp_path / "kvist-tui.mjs"
    entry.write_text("")
    calls = _make_tty(monkeypatch, entry)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert main.launch_tui(_tui_args()) is None
    assert "Node.js 22" in capsys.readouterr().err
    assert calls == []


def test_the_ui_bundle_is_looked_up_under_the_packaged_data_dir(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setattr(main.sys, "_MEIPASS", str(tmp_path), raising=False)
    try:
        reloaded = importlib.reload(main)
        assert reloaded._TUI_ENTRY == tmp_path / "frontends" / "tui" / "ink" / "dist-ui" / "kvist-tui.mjs"
    finally:
        monkeypatch.delattr(main.sys, "_MEIPASS", raising=False)
        importlib.reload(main)


@pytest.mark.parametrize("resume", [True, "chat-requested"])
def test_launch_tui_forwards_resume(monkeypatch, tmp_path, resume):
    entry = tmp_path / "ui.mjs"
    entry.touch()
    calls = _make_tty(monkeypatch, entry)
    main.launch_tui(_tui_args(resume=resume))
    command = calls[0][0]
    assert command[command.index("--resume") + 1] == ("" if resume is True else resume)


def test_setup_folder_choice_becomes_the_workspace(monkeypatch, tmp_path):
    entry = tmp_path / "cli.js"
    entry.write_text("")
    chosen = tmp_path / "project"
    chosen.mkdir()
    saved = []
    monkeypatch.setattr(main, "save_frontend", saved.append)
    _make_tty(monkeypatch, entry)
    launches = []

    def finish_setup_in_a_folder(command, env):
        launches.append(command)
        if len(launches) == 1:
            Path(env["KVIST_SWITCH_FILE"]).write_text(f"claude\n{chosen}\n", encoding="utf-8")
            return main._TUI_SWITCH_CODE
        return 0

    monkeypatch.setattr(subprocess, "call", finish_setup_in_a_folder)
    assert main.launch_tui(_tui_args(setup=True)) == 0
    # The interface keeps its case-folded name and the folder keeps its own spelling.
    assert saved == ["claude"]
    assert launches[1][launches[1].index("--workspace") + 1] == str(chosen)


def test_setup_folder_that_is_gone_is_ignored(monkeypatch, tmp_path):
    entry = tmp_path / "cli.js"
    entry.write_text("")
    monkeypatch.setattr(main, "save_frontend", lambda value: None)
    _make_tty(monkeypatch, entry)
    launches = []

    def choose(command, env):
        launches.append(command)
        if len(launches) == 1:
            Path(env["KVIST_SWITCH_FILE"]).write_text(f"claude\n{tmp_path / 'deleted'}\n", encoding="utf-8")
            return main._TUI_SWITCH_CODE
        return 0

    monkeypatch.setattr(subprocess, "call", choose)
    main.launch_tui(_tui_args(setup=True))
    assert launches[1][launches[1].index("--workspace") + 1] == "ws"


def test_classic_handover_keeps_the_chosen_folder(monkeypatch, tmp_path):
    entry = tmp_path / "cli.js"
    entry.write_text("")
    chosen = tmp_path / "project"
    chosen.mkdir()
    _make_tty(monkeypatch, entry)

    def classic(command, env):
        Path(env["KVIST_SWITCH_FILE"]).write_text(f"\n{chosen}\n", encoding="utf-8")
        return main._TUI_SETUP_CLASSIC_CODE

    monkeypatch.setattr(subprocess, "call", classic)
    args = _tui_args(setup=True)
    assert main.launch_tui(args) is None
    assert args.workspace == str(chosen)


def test_provider_only_setup_resolves_no_workspace_and_asks_for_no_interface(monkeypatch, tmp_path):
    entry = tmp_path / "cli.js"
    entry.write_text("")
    calls = _make_tty(monkeypatch, entry)
    monkeypatch.setattr(main, "resolve_workspace_path", lambda raw: pytest.fail("a workspace was resolved"))
    assert main.launch_tui(_tui_args(workspace=None), provider_only=True) == 7
    command = calls[-1][0]
    assert "--setup" in command and "--setup-provider-only" in command


def _acp_args():
    return main._build_cli_parser().parse_args(["--acp", "--login"])


def _fake_login(monkeypatch, result):
    import types

    monkeypatch.setitem(sys.modules, "acp_frontend", types.SimpleNamespace(run_login=lambda: result))


def test_acp_login_uses_the_plain_prompts_when_the_setup_screen_cannot_run(monkeypatch):
    _fake_login(monkeypatch, 5)
    monkeypatch.setattr(main, "launch_tui", lambda args, **kwargs: None)
    assert main._acp_login(_acp_args()) == 5


def test_acp_login_reports_what_the_setup_screen_did(monkeypatch, capsys):
    _fake_login(monkeypatch, 5)
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "deepseek")
    monkeypatch.setattr(main, "launch_tui", lambda args, **kwargs: main._TUI_SETUP_DONE_CODE)
    assert main._acp_login(_acp_args()) == 0
    assert "Provider saved: deepseek" in capsys.readouterr().err
    monkeypatch.setattr(main, "launch_tui", lambda args, **kwargs: 0)
    assert main._acp_login(_acp_args()) == 1
    assert "nothing was changed" in capsys.readouterr().err
    monkeypatch.setattr(main, "launch_tui", lambda args, **kwargs: 3)
    assert main._acp_login(_acp_args()) == 3


def _glass(monkeypatch, tmp_path, check_code=0, check_err=""):
    """A fake kvist-glass: ``--check`` answers with ``check_code``; the run is recorded."""
    glass = tmp_path / "kvist-glass"
    glass.write_text("")
    glass.chmod(0o755)
    monkeypatch.setenv("KVIST_GLASS", str(glass))
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(main, "resolve_workspace_path", lambda raw: raw)
    def check(command, stderr, text):
        # The terminal check must see the real terminal: only stderr is captured.
        assert command[-1] == "--check" and stderr == subprocess.PIPE
        return subprocess.CompletedProcess(command, check_code, None, check_err)

    monkeypatch.setattr(subprocess, "run", check)
    return glass


def test_glass_runs_with_this_program_as_its_core(monkeypatch, tmp_path):
    glass = _glass(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(subprocess, "call", lambda command, env: calls.append((command, env)) or 0)

    assert main.launch_glass(_tui_args(frontend="glass")) == 0

    command, env = calls[0]
    assert command == [str(glass), "--workspace", "ws"]
    assert json.loads(env["KVIST_CORE_CMD"])[-1].endswith("__main__.py")
    assert "KVIST_SWITCH_FILE" in env


def test_glass_on_a_terminal_it_refuses_falls_back_with_the_reason(monkeypatch, tmp_path, capsys):
    _glass(monkeypatch, tmp_path, check_code=3, check_err="no kitty graphics here")
    monkeypatch.setattr(subprocess, "call", lambda command, env: pytest.fail("must not start"))

    assert main.launch_glass(_tui_args(frontend="glass")) is None
    assert "no kitty graphics here" in capsys.readouterr().err


def test_glass_without_a_build_falls_back(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KVIST_GLASS", str(tmp_path / "missing"))
    monkeypatch.setattr(main, "_GLASS_BUILD", tmp_path / "missing")
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main.sys.stdout, "isatty", lambda: True)

    assert main.launch_glass(_tui_args(frontend="glass")) is None
    assert "cargo build --release" in capsys.readouterr().err


def test_tui_inside_glass_starts_the_chosen_interface(monkeypatch, tmp_path):
    _glass(monkeypatch, tmp_path)
    saved = []
    monkeypatch.setattr(main, "save_frontend", saved.append)
    monkeypatch.setattr(main, "launch_tui", lambda args: f"launched {args.frontend}")

    def choose_ink(command, env):
        Path(env["KVIST_SWITCH_FILE"]).write_text("tui\n", encoding="utf-8")
        return main._TUI_SWITCH_CODE

    monkeypatch.setattr(subprocess, "call", choose_ink)

    assert main.launch_glass(_tui_args(frontend="glass")) == "launched tui"
    assert saved == ["tui"]


def _voice(monkeypatch, tmp_path, check_code=0, check_err=""):
    screen = tmp_path / "kvist-voice-screen"
    screen.write_text("")
    screen.chmod(0o755)
    monkeypatch.setenv("KVIST_VOICE_SCREEN", str(screen))
    monkeypatch.setattr(main, "_VOICE_DIR", tmp_path / "no-launcher")
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(main, "resolve_workspace_path", lambda raw: raw)
    monkeypatch.setattr(
        subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, check_code, None, check_err)
    )
    return screen


def test_voice_runs_the_screen_with_this_program_as_its_core(monkeypatch, tmp_path):
    screen = _voice(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(subprocess, "call", lambda command, env: calls.append((command, env)) or 0)

    assert main.launch_voice(_tui_args(frontend="voice")) == 0

    command, env = calls[0]
    assert command == [str(screen), "--workspace", "ws"]
    assert json.loads(env["KVIST_CORE_CMD"])[-1].endswith("__main__.py")
    assert "KVIST_SWITCH_FILE" in env


def test_tui_inside_voice_starts_the_chosen_interface(monkeypatch, tmp_path):
    _voice(monkeypatch, tmp_path)
    saved = []
    monkeypatch.setattr(main, "save_frontend", saved.append)
    monkeypatch.setattr(main, "launch_tui", lambda args: f"launched {args.frontend}")

    def choose_ink(command, env):
        Path(env["KVIST_SWITCH_FILE"]).write_text("tui\n", encoding="utf-8")
        return main._TUI_SWITCH_CODE

    monkeypatch.setattr(subprocess, "call", choose_ink)

    assert main.launch_voice(_tui_args(frontend="voice")) == "launched tui"
    assert saved == ["tui"]


def test_voice_starts_through_its_launcher_when_it_ships_beside_the_screen(monkeypatch, tmp_path):
    screen = _voice(monkeypatch, tmp_path)
    folder = tmp_path / "voice"
    folder.mkdir()
    launcher = folder / "kvist-voice"
    launcher.write_text("")
    launcher.chmod(0o755)
    monkeypatch.setattr(main, "_VOICE_DIR", folder)
    calls = []
    monkeypatch.setattr(subprocess, "call", lambda command, env: calls.append((command, env)) or 0)

    assert main.launch_voice(_tui_args(frontend="voice")) == 0

    command, env = calls[0]
    assert command[0] == str(launcher)
    assert env["KVIST_VOICE_SCREEN"] == str(screen)


def test_voice_on_a_terminal_it_refuses_falls_back_with_the_reason(monkeypatch, tmp_path, capsys):
    _voice(monkeypatch, tmp_path, check_code=3, check_err="no kitty graphics here")
    monkeypatch.setattr(subprocess, "call", lambda command, env: pytest.fail("must not start"))

    assert main.launch_voice(_tui_args(frontend="voice")) is None
    assert "no kitty graphics here" in capsys.readouterr().err


def test_voice_without_a_build_falls_back(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KVIST_VOICE_SCREEN", str(tmp_path / "missing"))
    monkeypatch.setattr(main, "_VOICE_SCREEN_BUILD", tmp_path / "missing")
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main.sys.stdout, "isatty", lambda: True)

    assert main.launch_voice(_tui_args(frontend="voice")) is None
    assert "frontends/voice/screen" in capsys.readouterr().err


def test_tui_switch_to_voice_starts_it(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "save_frontend", lambda value: None)
    monkeypatch.setattr(main, "launch_voice", lambda args: 7)
    switch = tmp_path / "frontend"
    switch.write_text("voice\n", encoding="utf-8")

    assert main._switch_frontend(switch, _tui_args(frontend="tui")) == 7
