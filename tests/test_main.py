from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

import main
from executor import SafeExecutor


def test_cli_help_exits_before_terminal_and_provider_initialization(tmp_path):
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["HOME"] = str(tmp_path)
    environment.pop("FORCE_COLOR", None)
    environment["NO_COLOR"] = "1"
    environment["PYTHON_COLORS"] = "0"

    completed = subprocess.run(
        [sys.executable, str(root / "main.py"), "--help"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )

    assert completed.returncode == 0
    assert "--workspace WORKSPACE" in completed.stdout
    assert completed.stderr == ""


def test_resolve_workspace_path_creates_directory_and_rejects_file(tmp_path):
    workspace = main.resolve_workspace_path(str(tmp_path / "new"))
    assert workspace.is_dir()

    file_path = tmp_path / "file"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="directory"):
        main.resolve_workspace_path(str(file_path))


def test_resolve_workspace_path_defaults_to_documents_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_documents_directory", lambda: tmp_path / "Documents")

    workspace = main.resolve_workspace_path()

    assert workspace == (tmp_path / "Documents" / "Kvist Workspace").resolve()
    assert workspace.is_dir()


def test_build_agent_wires_config_without_real_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "openai")
    monkeypatch.setattr(main, "get_saved_models", lambda provider: {})
    monkeypatch.setattr(main, "ensure_provider_credentials", lambda **kwargs: {"openai_api_key": "key"})
    monkeypatch.setattr(main, "fetch_available_models", lambda provider, credentials, force_refresh=False: (["gpt-test"], None))
    monkeypatch.setattr(
        main,
        "resolve_accessible_models",
        lambda provider, models, available, language_hint=None: (models, ["warn"]),
    )
    monkeypatch.setattr(main, "create_llm_client", lambda provider, credentials, verbose=False: SimpleNamespace(chat=None))

    agent, config, warnings = main.build_agent(
        executor=SafeExecutor(tmp_path),
        max_steps=7,
        persist_logs=False,
        verbose=True,
        language_hint="en",
    )

    assert agent.config.max_steps == 7
    assert config.provider == "openai"
    assert warnings == ["warn"]


def test_startup_banner_renders_dynamic_values_as_literal_safe_text(
    monkeypatch,
):
    capture = Console(record=True, width=180)
    monkeypatch.setattr(main, "console", capture)
    monkeypatch.setattr(main, "get_saved_role", lambda: "auto")

    class DangerousWorkspace:
        def __str__(self):
            return "workspace [red]\x1b[2J"

    agent = SimpleNamespace(
        executor=SimpleNamespace(workspace=DangerousWorkspace()),
        current_session="default",
        _list_sessions=lambda: ["default"],
        _session_history=[
            {
                "role": "chat",
                "goal": "literal [/cyan][red]markup[/red] \u202e",
            }
        ],
    )
    config = main.AgentConfig(
        provider="openai",
        fast_model="model [bold]literal[/bold]",
        smart_model="smart",
        planner_model="planner",
    )

    main.render_startup_banner(agent, config, 5, False, ["warn [red]\x08"])

    rendered = capture.export_text()
    assert "[/cyan][red]markup[/red]" in rendered
    assert "[bold]literal[/bold]" in rendered
    assert "\x1b" not in rendered
    assert "\u202e" not in rendered
    assert "\x08" not in rendered


def test_choose_provider_delegates_to_ui(monkeypatch):
    class FakeUI:
        def __init__(self, **kwargs):
            pass

        def choose_option(self, **kwargs):
            return kwargs["current_value"]

    monkeypatch.setattr(main, "TerminalBannerUI",FakeUI)
    assert main.choose_provider("openai", False) == "openai"


def test_choose_session_creates_normalized_named_session(monkeypatch):
    created = []

    class FakeUI:
        def __init__(self, **kwargs):
            pass

        def choose_option(self, **kwargs):
            return "__new__"

        def prompt_text(self, **kwargs):
            return "Feature_One"

    agent = SimpleNamespace(
        current_session="default",
        _list_sessions=lambda: [],
        new_session=created.append,
    )
    monkeypatch.setattr(main, "TerminalBannerUI",FakeUI)

    assert main.choose_session(agent, False) == "feature_one"
    assert created == ["feature_one"]


def test_choose_session_rejects_path_like_name(monkeypatch):
    created = []

    class FakeUI:
        def __init__(self, **kwargs):
            pass

        def choose_option(self, **kwargs):
            return "__new__"

        def prompt_text(self, **kwargs):
            return "../outside"

    agent = SimpleNamespace(
        current_session="default",
        _list_sessions=lambda: [],
        new_session=created.append,
    )
    monkeypatch.setattr(main, "TerminalBannerUI",FakeUI)

    assert main.choose_session(agent, False) is None
    assert created == []


def test_choose_session_successful_delete_returns_active_session(monkeypatch):
    selections = iter(["__delete__", "old_session"])

    class FakeUI:
        def __init__(self, **kwargs):
            pass

        def choose_option(self, **kwargs):
            return next(selections)

    deleted = []
    agent = SimpleNamespace(
        current_session="current",
        _list_sessions=lambda: ["current", "old_session"],
        _delete_session=deleted.append,
    )
    monkeypatch.setattr(main, "TerminalBannerUI",FakeUI)

    assert main.choose_session(agent, False) == "current"
    assert deleted == ["old_session"]


def test_main_recovers_from_redacted_task_failure(tmp_path, monkeypatch):
    synthetic = "sk-proj-" + "z" * 24
    calls = {"run": 0, "save": 0}

    class FakeUI:
        def stop_live(self):
            pass

        def start_live(self):
            pass

    class FakeAgent:
        current_session = "default"
        _ui = FakeUI()
        client = object()

        def run(self, user_goal, routed_role=None):
            calls["run"] += 1
            raise RuntimeError(f"Authorization: Bearer {synthetic}")

        def save_session(self):
            calls["save"] += 1

    config = SimpleNamespace(
        provider="openai",
        stream=False,
        fast_model="fixture",
    )
    inputs = iter(["trigger failure now", "/bye"])
    recorded_console = Console(
        record=True,
        width=120,
        force_terminal=False,
        color_system=None,
    )
    monkeypatch.setattr(
        main.sys,
        "argv",
        ["kvist", "--workspace", str(tmp_path), "--no-banner"],
    )
    monkeypatch.setattr(
        main,
        "build_agent",
        lambda **kwargs: (FakeAgent(), config, []),
    )
    monkeypatch.setattr(main, "multiline_input", lambda: next(inputs))
    monkeypatch.setattr(main, "render_startup_banner", lambda *args: None)
    monkeypatch.setattr(main, "sync_strime_suggestion", lambda *args: None)
    monkeypatch.setattr(main, "get_saved_role", lambda: "chat")
    monkeypatch.setattr(main, "console", recorded_console)

    main.main()

    rendered = recorded_console.export_text()
    assert calls == {"run": 1, "save": 1}
    assert synthetic not in rendered
    assert "[REDACTED]" in rendered
    assert "Task failed safely" in rendered


def _run_main_with_agent(tmp_path, monkeypatch, agent, inputs):
    config = SimpleNamespace(provider="openai", stream=False, fast_model="fixture")
    recorded_console = Console(
        record=True,
        width=120,
        force_terminal=False,
        color_system=None,
    )
    supplied = iter(inputs)

    def next_input():
        value = next(supplied)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(
        main.sys,
        "argv",
        ["kvist", "--workspace", str(tmp_path), "--no-banner"],
    )
    monkeypatch.setattr(main, "build_agent", lambda **kwargs: (agent, config, []))
    monkeypatch.setattr(main, "multiline_input", next_input)
    monkeypatch.setattr(main, "render_startup_banner", lambda *args: None)
    monkeypatch.setattr(main, "sync_strime_suggestion", lambda *args: None)
    monkeypatch.setattr(main, "get_saved_role", lambda: "chat")
    monkeypatch.setattr(main, "console", recorded_console)
    return recorded_console


class _ShutdownUI:
    def stop_live(self):
        pass

    def start_live(self):
        pass


def test_interrupted_session_save_does_not_crash_shutdown(tmp_path, monkeypatch):
    calls = {"save": 0}

    class FakeAgent:
        current_session = "default"
        _ui = _ShutdownUI()
        client = object()

        def run(self, user_goal, routed_role=None):
            return "ok"

        def save_session(self):
            calls["save"] += 1
            raise KeyboardInterrupt

    recorded = _run_main_with_agent(
        tmp_path,
        monkeypatch,
        FakeAgent(),
        [KeyboardInterrupt()],
    )

    with pytest.raises(SystemExit) as exit_info:
        main.main()

    assert exit_info.value.code == 130
    assert calls["save"] == 1
    rendered = recorded.export_text()
    assert "Traceback (most recent call last)" not in rendered
    assert "interrupted" in rendered.casefold()


def test_failed_session_save_is_redacted_on_shutdown(tmp_path, monkeypatch):
    synthetic = "sk-proj-" + "y" * 24

    class FakeAgent:
        current_session = "default"
        _ui = _ShutdownUI()
        client = object()

        def run(self, user_goal, routed_role=None):
            return "ok"

        def save_session(self):
            raise OSError(f"disk failure while writing Authorization: Bearer {synthetic}")

    recorded = _run_main_with_agent(tmp_path, monkeypatch, FakeAgent(), ["/bye"])

    main.main()

    rendered = recorded.export_text()
    assert synthetic not in rendered
    assert "[REDACTED]" in rendered
    assert "Traceback (most recent call last)" not in rendered


def test_late_interrupt_still_restores_the_terminal(monkeypatch):
    stops = []

    class FlakyScreen:
        def stop(self):
            stops.append(True)
            if len(stops) == 1:
                raise KeyboardInterrupt

    main.release_terminal(FlakyScreen(), False)

    assert len(stops) == 2


def test_late_interrupt_still_leaves_the_alternate_screen(monkeypatch):
    calls = []

    class RecordingConsole:
        def set_alt_screen(self, enabled):
            calls.append(enabled)
            if len(calls) == 1:
                raise KeyboardInterrupt

    monkeypatch.setattr(main, "console", RecordingConsole())

    main.release_terminal(None, True)

    assert calls == [False, False]


def test_bye_and_interrupt_share_one_shutdown_path(tmp_path, monkeypatch):
    saves = []

    class FakeAgent:
        current_session = "default"
        _ui = _ShutdownUI()
        client = object()

        def run(self, user_goal, routed_role=None):
            return "ok"

        def save_session(self):
            saves.append(True)

    _run_main_with_agent(tmp_path, monkeypatch, FakeAgent(), ["/bye"])
    main.main()
    assert len(saves) == 1

    saves.clear()
    _run_main_with_agent(tmp_path, monkeypatch, FakeAgent(), [KeyboardInterrupt()])
    with pytest.raises(SystemExit) as exit_info:
        main.main()
    assert exit_info.value.code == 130
    assert len(saves) == 1


def test_configure_custom_provider_interactively_url_normalization(monkeypatch):
    saved_providers = {}
    monkeypatch.setattr(main, "get_custom_providers", lambda: {})
    monkeypatch.setattr(main, "save_custom_provider", saved_providers.__setitem__)

    class FakeUI:
        def __init__(self, **kwargs):
            self.option_selections = {}
            self.prompt_selections = {}

        def prompt_text(self, title, **kwargs):
            for pattern, val in self.prompt_selections.items():
                if pattern in title:
                    return val
            return "default_val"

        def prompt_secret(self, title, **kwargs):
            return ""

        def choose_option(self, title, **kwargs):
            for pattern, val in self.option_selections.items():
                if pattern in title:
                    return val
            return kwargs.get("current_value")

    ui = FakeUI()
    monkeypatch.setattr(main, "TerminalBannerUI",lambda **kwargs: ui)

    # 1. Test URL "https://api.example.dev" and "add"
    ui.prompt_selections = {
        "provider ID": "test-provider-1",
        "base URL": "https://api.example.dev",
        "display name": "Test Provider 1",
        "model ID": "gpt-4",
    }
    ui.option_selections = {
        "Select protocol": "openai",
        "no path suffix": "add",
        "streaming": "no",
        "Model configuration": "one",
        "Create this provider?": "yes",
    }
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-1"]["base_url"] == "https://api.example.dev/v1"

    # 2. Test URL "https://api.example.dev" and "keep"
    ui.prompt_selections["provider ID"] = "test-provider-2"
    ui.option_selections["no path suffix"] = "keep"
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-2"]["base_url"] == "https://api.example.dev"

    # 3. Test URL "https://api.example.dev/" and "add"
    ui.prompt_selections["provider ID"] = "test-provider-3"
    ui.prompt_selections["base URL"] = "https://api.example.dev/"
    ui.option_selections["no path suffix"] = "add"
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-3"]["base_url"] == "https://api.example.dev/v1"

    # 4. Test URL "https://api.example.dev/v1" (should not ask)
    ui.prompt_selections["provider ID"] = "test-provider-4"
    ui.prompt_selections["base URL"] = "https://api.example.dev/v1"
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-4"]["base_url"] == "https://api.example.dev/v1"

    # 5. Test URL "https://api.example.dev/v1beta" (should not ask)
    ui.prompt_selections["provider ID"] = "test-provider-5"
    ui.prompt_selections["base URL"] = "https://api.example.dev/v1beta"
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-5"]["base_url"] == "https://api.example.dev/v1beta"

    # 6. Test Anthropic protocol (should not ask or modify)
    ui.prompt_selections["provider ID"] = "test-provider-6"
    ui.prompt_selections["base URL"] = "https://api.example.dev"
    ui.option_selections["Select protocol"] = "anthropic"
    main.configure_custom_provider_interactively(ui, False)
    # Note: Anthropic should keep the original base URL as is
    assert saved_providers["test-provider-6"]["base_url"] == "https://api.example.dev"

    # 7. Test URL without scheme "api.example.dev" and "add_https" auto-add, and then "add" /v1
    ui.prompt_selections = {
        "provider ID": "test-provider-7",
        "base URL": "api.example.dev",
        "display name": "Test Provider 7",
        "model ID": "gpt-4",
    }
    ui.option_selections = {
        "must start with https": "add_https",
        "Select protocol": "openai",
        "no path suffix": "add",
        "streaming": "no",
        "Model configuration": "one",
        "Create this provider?": "yes",
    }
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-7"]["base_url"] == "https://api.example.dev/v1"

    # 8. Test URL without scheme "api.example.dev" and "add_https" auto-add, and then "keep" as is
    ui.prompt_selections["provider ID"] = "test-provider-8"
    ui.option_selections["no path suffix"] = "keep"
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-8"]["base_url"] == "https://api.example.dev"

    # 9. Test URL without scheme "api.example.dev/v1" and "add_https" auto-add
    ui.prompt_selections["provider ID"] = "test-provider-9"
    ui.prompt_selections["base URL"] = "api.example.dev/v1"
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-9"]["base_url"] == "https://api.example.dev/v1"
