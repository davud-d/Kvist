# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import frontends
import main
from agent import LocalAgent
from agent_state import AgentConfig
from event_session import EventSession
from event_stream_ui import EventStreamUI
from tests.conftest import FakeLLMClient


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

    def fake_launch_tui(args):
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

    def fake_launch_tui(args):
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
    from main import _build_cli_parser

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
            str(Path(main.__file__)),
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
    from event_stdio import serve_stdio
    from frontends import load_launcher

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


def test_launch_tui_falls_back_when_not_built_or_no_provider(monkeypatch, tmp_path, capsys):
    calls = _make_tty(monkeypatch, tmp_path / "missing.js")
    assert main.launch_tui(_tui_args()) is None
    assert "npm install && npm run build" in capsys.readouterr().err

    entry = tmp_path / "cli.js"
    entry.write_text("")
    monkeypatch.setattr(main, "_TUI_ENTRY", entry)
    monkeypatch.delenv("AGENT_LLM_PROVIDER", raising=False)
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: None)
    assert main.launch_tui(_tui_args()) is None
    assert "provider" in capsys.readouterr().err
    assert calls == []


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
    assert core[0] == main.sys.executable and core[-1].endswith("main.py")


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


@pytest.mark.parametrize(
    ("raw", "current", "expected"),
    [
        ("/tui", "terminal", "tui"),
        ("/tui", "tui", "terminal"),
        ("/tui", "claude", "terminal"),
        ("/tui on", "terminal", "tui"),
        ("/tui <claude>", "terminal", "claude"),
        ("/tui off", "claude", "terminal"),
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
        assert reloaded._TUI_ENTRY == tmp_path / "dist-ui" / "kvist-tui.mjs"
    finally:
        monkeypatch.delattr(main.sys, "_MEIPASS", raising=False)
        importlib.reload(main)
