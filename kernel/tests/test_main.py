from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from kernel import main
from kernel.sandbox.executor import SafeExecutor


def test_cli_help_exits_before_terminal_and_provider_initialization(tmp_path):
    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["HOME"] = str(tmp_path)
    environment.pop("FORCE_COLOR", None)
    environment["NO_COLOR"] = "1"
    environment["PYTHON_COLORS"] = "0"

    completed = subprocess.run(
        [sys.executable, str(root / "kernel" / "__main__.py"), "--help"],
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


def test_cli_version_exits_before_terminal_and_provider_initialization(tmp_path):
    from kernel.config.version import kvist_version

    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["HOME"] = str(tmp_path)
    environment.pop("FORCE_COLOR", None)
    environment["NO_COLOR"] = "1"
    environment["PYTHON_COLORS"] = "0"

    completed = subprocess.run(
        [sys.executable, str(root / "kernel" / "__main__.py"), "--version"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == kvist_version()
    assert completed.stderr == ""


def test_resolve_workspace_path_creates_directory_and_rejects_file(tmp_path):
    workspace = main.resolve_workspace_path(str(tmp_path / "new"))
    assert workspace.is_dir()

    file_path = tmp_path / "file"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="directory"):
        main.resolve_workspace_path(str(file_path))


def test_resolve_workspace_path_defaults_to_launch_directory(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(main, "_documents_directory", lambda: tmp_path / "Documents")
    monkeypatch.chdir(project)

    assert main.resolve_workspace_path() == project.resolve()
    assert not (tmp_path / "Documents").exists()


@pytest.mark.parametrize("launch", ["home", "root"])
def test_resolve_workspace_path_falls_back_from_a_broad_launch_directory(tmp_path, monkeypatch, launch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(main, "_documents_directory", lambda: tmp_path / "Documents")
    monkeypatch.setattr(main.Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(home if launch == "home" else Path(home.anchor))

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
    # The network note is checked on its own below; here only the provider's warnings may appear.
    monkeypatch.setattr(main, "network_isolation_available", lambda: True)

    agent, config, warnings = main.build_agent(
        executor=SafeExecutor(tmp_path),
        max_steps=7,
        persist_logs=False,
        verbose=True,
        language_hint="en",
    )

    assert agent.config.max_steps == 7
    assert config.provider == "openai"
    assert warnings[0] == "warn"
    # May contain reasoning effort advisory if config differs from default
    assert all("warn" in w or "Reasoning effort" in w for w in warnings)


def test_build_agent_says_when_commands_cannot_be_cut_off_the_network(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "openai")
    monkeypatch.setattr(main, "get_saved_models", lambda provider: {})
    monkeypatch.setattr(main, "ensure_provider_credentials", lambda **kwargs: {"openai_api_key": "key"})
    monkeypatch.setattr(main, "fetch_available_models", lambda provider, credentials, force_refresh=False: (["gpt-test"], None))
    monkeypatch.setattr(main, "create_llm_client", lambda provider, credentials, verbose=False: SimpleNamespace(chat=None))

    def warnings_with(isolation: bool) -> list[str]:
        monkeypatch.setattr(main, "network_isolation_available", lambda: isolation)
        return main.build_agent(executor=SafeExecutor(tmp_path), max_steps=7, persist_logs=False,
                                verbose=False, language_hint="en")[2]

    assert f"{main.NETWORK_UNENFORCED_NOTE}." in warnings_with(False)
    assert not any(main.NETWORK_UNENFORCED_NOTE in warning for warning in warnings_with(True))


def test_build_agent_wires_manual_tier_provider(tmp_path, monkeypatch):
    # Manual-tier providers (Qwen, Moonshot, ...) resolve like OmniRoute: no
    # defaults, so an already-saved model set must flow straight through.
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "qwen")
    monkeypatch.setattr(
        main, "get_saved_models", lambda provider: {"fast": "qwen-flash", "smart": "qwen-max"}
    )
    monkeypatch.setattr(main, "ensure_provider_credentials", lambda **kwargs: {"qwen_api_key": "key"})
    monkeypatch.setattr(main, "fetch_available_models", lambda provider, credentials, force_refresh=False: ([], None))
    monkeypatch.setattr(main, "create_llm_client", lambda provider, credentials, verbose=False: SimpleNamespace(chat=None))

    agent, config, warnings = main.build_agent(
        executor=SafeExecutor(tmp_path),
        max_steps=7,
        persist_logs=False,
        verbose=True,
        language_hint="en",
    )

    assert config.provider == "qwen"
    assert config.fast_model == "qwen-flash"


def test_build_agent_wires_ui_backed_secret_prompt(tmp_path, monkeypatch):
    """ensure_provider_credentials must get a prompt_secret_fn backed by the
    real masked UI, not silently fall through to bare getpass() (no visible
    feedback at all) the way it used to for any first-time key entry."""
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "openai")
    monkeypatch.setattr(main, "get_saved_models", lambda provider: {})
    monkeypatch.setattr(main, "fetch_available_models", lambda provider, credentials, force_refresh=False: (["gpt-test"], None))
    monkeypatch.setattr(
        main, "resolve_accessible_models",
        lambda provider, models, available, language_hint=None: (models, []),
    )
    monkeypatch.setattr(main, "create_llm_client", lambda provider, credentials, verbose=False: SimpleNamespace(chat=None))

    captured_kwargs = {}

    def fake_ensure(**kwargs):
        captured_kwargs.update(kwargs)
        return {"openai_api_key": "key"}

    monkeypatch.setattr(main, "ensure_provider_credentials", fake_ensure)

    main.build_agent(
        executor=SafeExecutor(tmp_path),
        max_steps=7,
        persist_logs=False,
        verbose=False,
        language_hint="en",
    )

    assert callable(captured_kwargs["prompt_secret_fn"])
    # Headless (no TTY): keeps the original stdin-pipe fallback (run_task.py /
    # scripted use can still pipe a secret in) rather than using the UI.
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(main, "prompt_secret", lambda label: f"piped:{label}")
    assert captured_kwargs["prompt_secret_fn"]("Some label") == "piped:Some label"

    # With a TTY, it must delegate to TerminalBannerUI.prompt_secret rather
    # than the bare getpass() fallback.
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    seen_titles = []

    class FakeUI:
        def __init__(self, **kwargs):
            pass

        def prompt_secret(self, title, **kwargs):
            seen_titles.append(title)
            return "typed-secret"

    monkeypatch.setattr(main, "TerminalBannerUI", FakeUI)
    assert captured_kwargs["prompt_secret_fn"]("OpenAI API key") == "typed-secret"
    assert seen_titles == ["OpenAI API key"]


def test_build_agent_confirms_base_url_on_first_time_manual_provider_setup(tmp_path, monkeypatch):
    """A manual-tier provider with a single known endpoint (no regional
    race) must still let the user confirm/override the base URL on first
    setup, pre-filled with the default."""
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "mistral")
    monkeypatch.setattr(
        main, "get_saved_models",
        lambda provider: {"fast": "mistral-small", "smart": "mistral-large"},
    )
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main, "load_available_credentials", lambda keys: {})
    monkeypatch.setattr(main, "fetch_available_models", lambda provider, credentials, force_refresh=False: ([], None))
    monkeypatch.setattr(main, "create_llm_client", lambda provider, credentials, verbose=False: SimpleNamespace(chat=None))

    saved_secrets = {}
    monkeypatch.setattr(main, "set_provider_secret", lambda key, value, **kw: saved_secrets.__setitem__(key, value))
    monkeypatch.setattr(
        main, "ensure_provider_credentials",
        lambda **kwargs: {"mistral_api_key": "key"},
    )

    def race_should_not_run(*args, **kwargs):
        raise AssertionError("race_base_urls must not run for a single-endpoint provider")

    monkeypatch.setattr(main, "race_base_urls", race_should_not_run)

    seen_prompts = []

    class FakeUI:
        def __init__(self, **kwargs):
            pass

        def prompt_text(self, title, current_value="", **kwargs):
            seen_prompts.append((title, current_value))
            return "https://api.mistral.ai/v1"

    monkeypatch.setattr(main, "TerminalBannerUI", FakeUI)

    main.build_agent(
        executor=SafeExecutor(tmp_path),
        max_steps=7,
        persist_logs=False,
        verbose=False,
        language_hint="en",
    )

    assert len(seen_prompts) == 1
    title, current_value = seen_prompts[0]
    assert "Mistral AI" in title
    assert current_value == "https://api.mistral.ai/v1"
    assert saved_secrets == {"mistral_base_url": "https://api.mistral.ai/v1"}


def test_build_agent_races_regional_endpoints_authenticated_and_suggests_the_fastest(tmp_path, monkeypatch):
    """xiaomi-tokenplan has 3 known regional endpoints — first-time setup
    must collect the key up front, race the regions *with* it, and persist
    whichever accepted the key fastest (not just whichever answered fastest:
    a fast-but-wrong region would still "respond", just with a 401)."""
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "xiaomi-tokenplan")
    monkeypatch.setattr(
        main, "get_saved_models",
        lambda provider: {"fast": "mimo-v2.5", "smart": "mimo-v2.5"},
    )
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main, "load_available_credentials", lambda keys: {})
    monkeypatch.setattr(main, "fetch_available_models", lambda provider, credentials, force_refresh=False: ([], None))
    monkeypatch.setattr(main, "create_llm_client", lambda provider, credentials, verbose=False: SimpleNamespace(chat=None))
    monkeypatch.setattr(main, "ensure_provider_credentials", lambda **kwargs: {"xiaomi_tokenplan_api_key": "typed-key"})

    saved_secrets = {}
    monkeypatch.setattr(main, "set_provider_secret", lambda key, value, **kw: saved_secrets.__setitem__(key, value))

    race_calls = []

    def fake_race(candidates, api_key, timeout=8.0):
        race_calls.append((candidates, api_key))
        # cn "responds" (still up) but rejects this key; sgp accepts it.
        return "https://token-plan-sgp.xiaomimimo.com/v1", {
            "https://token-plan-cn.xiaomimimo.com/v1": {"status": "unauthorized", "elapsed": 0.1},
            "https://token-plan-sgp.xiaomimimo.com/v1": {"status": "ok", "elapsed": 0.42},
            "https://token-plan-ams.xiaomimimo.com/v1": {"status": "unreachable", "elapsed": None},
        }

    monkeypatch.setattr(main, "race_base_urls", fake_race)

    class FakeUI:
        def __init__(self, **kwargs):
            pass

        def prompt_secret(self, title, **kwargs):
            return "typed-key"

    monkeypatch.setattr(main, "TerminalBannerUI", FakeUI)

    main.build_agent(
        executor=SafeExecutor(tmp_path),
        max_steps=7,
        persist_logs=False,
        verbose=False,
        language_hint="en",
    )

    assert len(race_calls) == 1
    candidates, api_key = race_calls[0]
    assert set(candidates) == {
        "https://token-plan-cn.xiaomimimo.com/v1",
        "https://token-plan-sgp.xiaomimimo.com/v1",
        "https://token-plan-ams.xiaomimimo.com/v1",
    }
    assert api_key == "typed-key"
    assert saved_secrets == {
        "xiaomi_tokenplan_api_key": "typed-key",
        "xiaomi_tokenplan_base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
    }


def test_build_agent_raises_clearly_when_key_rejected_in_every_region(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "qwen")
    monkeypatch.setattr(
        main, "get_saved_models",
        lambda provider: {"fast": "qwen-flash", "smart": "qwen-max"},
    )
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main, "load_available_credentials", lambda keys: {})
    monkeypatch.setattr(
        main, "race_base_urls",
        lambda candidates, api_key, timeout=8.0: (
            None, {url: {"status": "unauthorized", "elapsed": 0.1} for url in candidates}
        ),
    )

    class FakeUI:
        def __init__(self, **kwargs):
            pass

        def prompt_secret(self, title, **kwargs):
            return "bad-key"

    monkeypatch.setattr(main, "TerminalBannerUI", FakeUI)

    with pytest.raises(RuntimeError, match="rejected this API key in every known region"):
        main.build_agent(
            executor=SafeExecutor(tmp_path),
            max_steps=7,
            persist_logs=False,
            verbose=False,
            language_hint="en",
        )


def test_build_agent_raises_clearly_when_no_region_responds(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "qwen")
    monkeypatch.setattr(
        main, "get_saved_models",
        lambda provider: {"fast": "qwen-flash", "smart": "qwen-max"},
    )
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main, "load_available_credentials", lambda keys: {})
    monkeypatch.setattr(
        main, "race_base_urls",
        lambda candidates, api_key, timeout=8.0: (
            None, {url: {"status": "unreachable", "elapsed": None} for url in candidates}
        ),
    )

    class FakeUI:
        def __init__(self, **kwargs):
            pass

        def prompt_secret(self, title, **kwargs):
            return "some-key"

    monkeypatch.setattr(main, "TerminalBannerUI", FakeUI)

    with pytest.raises(RuntimeError, match="Could not reach any Qwen"):
        main.build_agent(
            executor=SafeExecutor(tmp_path),
            max_steps=7,
            persist_logs=False,
            verbose=False,
            language_hint="en",
        )


def test_build_agent_skips_base_url_prompt_when_provider_already_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "xiaomi-tokenplan")
    monkeypatch.setattr(
        main, "get_saved_models",
        lambda provider: {"fast": "mimo-v2.5", "smart": "mimo-v2.5"},
    )
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main, "load_available_credentials", lambda keys: {"xiaomi_tokenplan_api_key": "already-set"})
    monkeypatch.setattr(main, "fetch_available_models", lambda provider, credentials, force_refresh=False: ([], None))
    monkeypatch.setattr(main, "create_llm_client", lambda provider, credentials, verbose=False: SimpleNamespace(chat=None))
    monkeypatch.setattr(
        main, "ensure_provider_credentials",
        lambda **kwargs: {"xiaomi_tokenplan_api_key": "already-set"},
    )

    prompt_calls = []
    monkeypatch.setattr(main, "TerminalBannerUI", lambda **kw: SimpleNamespace(prompt_text=lambda **k: prompt_calls.append(k)))

    main.build_agent(
        executor=SafeExecutor(tmp_path),
        max_steps=7,
        persist_logs=False,
        verbose=False,
        language_hint="en",
    )

    assert prompt_calls == []


def test_build_agent_rejects_manual_tier_provider_with_no_models(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "moonshot")
    monkeypatch.setattr(main, "get_saved_models", lambda provider: {})
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main, "TerminalBannerUI", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(
        main, "configure_manual_provider_models_interactively", lambda ui, models, provider: {}
    )

    with pytest.raises(RuntimeError, match="no model configured"):
        main.build_agent(
            executor=SafeExecutor(tmp_path),
            max_steps=7,
            persist_logs=False,
            verbose=False,
            language_hint="en",
        )


def test_configure_manual_provider_models_interactively_offers_live_pick(monkeypatch):
    monkeypatch.setattr(
        main, "_model_picker_options",
        lambda provider: ([{"value": "qwen-max", "label": "qwen-max", "description": ""}], True),
    )

    seen_menus = []

    class FakeUI:
        def choose_option(self, **kwargs):
            options = kwargs["options"]
            seen_menus.append([item["value"] for item in options])
            if "pick" in [item["value"] for item in options] and kwargs["title"] == "Select action:":
                return "pick"
            return "qwen-max"

    result = main.configure_manual_provider_models_interactively(
        FakeUI(), current_models={"fast": "", "smart": ""}, provider="qwen"
    )

    assert result == {"fast": "qwen-max", "smart": "qwen-max"}
    assert "pick" in seen_menus[0]


def test_configure_manual_provider_models_interactively_falls_back_to_text_entry(monkeypatch):
    monkeypatch.setattr(main, "_model_picker_options", lambda provider: ([], False))

    class FakeUI:
        def choose_option(self, **kwargs):
            options = [item["value"] for item in kwargs["options"]]
            assert "pick" not in options
            return "one"

        def prompt_text(self, **kwargs):
            return "custom-model-id"

    result = main.configure_manual_provider_models_interactively(
        FakeUI(), current_models={"fast": "", "smart": ""}, provider="qwen"
    )

    assert result == {"fast": "custom-model-id", "smart": "custom-model-id"}


def test_build_agent_rejects_omniroute_with_no_models(tmp_path, monkeypatch):
    # "Keep current models" with nothing configured returns an empty dict; the
    # old code then hit KeyError: 'fast' deep in credential resolution.
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "omniroute")
    monkeypatch.setattr(main, "get_saved_models", lambda provider: {})
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main, "TerminalBannerUI", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(
        main, "configure_omniroute_models_interactively", lambda ui, models: {}
    )

    with pytest.raises(RuntimeError, match="no model configured"):
        main.build_agent(
            executor=SafeExecutor(tmp_path),
            max_steps=7,
            persist_logs=False,
            verbose=False,
            language_hint="en",
        )


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


def test_terminal_supports_mouse_clicks_requires_a_real_tty(monkeypatch):
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(main.sys.stdout, "isatty", lambda: True)
    assert main._terminal_supports_mouse_clicks() is False


def test_terminal_supports_mouse_clicks_rejects_linux_console_and_dumb(monkeypatch):
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(main.os, "name", "posix")
    for term in ("linux", "dumb", ""):
        monkeypatch.setenv("TERM", term) if term else monkeypatch.delenv("TERM", raising=False)
        assert main._terminal_supports_mouse_clicks() is False


def test_terminal_supports_mouse_clicks_accepts_xterm(monkeypatch):
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(main.os, "name", "posix")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert main._terminal_supports_mouse_clicks() is True


def test_confirm_with_mouse_checkbox_returns_none_without_mouse_support(monkeypatch):
    monkeypatch.setattr(main, "_terminal_supports_mouse_clicks", lambda: False)
    assert main.confirm_with_mouse_checkbox("title", "checkbox") is None


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


def test_choose_session_shows_each_session_title(monkeypatch):
    captured = {}

    class FakeUI:
        def __init__(self, **kwargs):
            pass

        def choose_option(self, **kwargs):
            captured["options"] = kwargs["options"]
            return None

    agent = SimpleNamespace(
        current_session="alpha",
        _list_sessions=lambda: ["alpha", "beta"],
        _read_session_document=lambda name: (
            {"title": "Fix the login bug"} if name == "alpha" else {}
        ),
    )
    monkeypatch.setattr(main, "TerminalBannerUI", FakeUI)

    main.choose_session(agent, False)

    labels = {option["value"]: option["label"] for option in captured["options"]}
    assert labels["alpha"] == "● alpha — Fix the login bug"
    assert labels["beta"] == "  beta"


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
        _read_session_document=lambda name: {},
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

        def start_fresh_session(self):
            return self.current_session, True
        _ui = FakeUI()
        client = object()

        def is_session_closed(self):
            return False

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
    config = SimpleNamespace(
        provider="openai", stream=False, fast_model="fixture", smart_model="fixture-smart"
    )
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


@pytest.mark.parametrize("mode,network,expected", [
    ("workspace_only", "deny", "net host (unisolated)"),
    ("workspace_only", "allow", "net host (unisolated)"),
    ("bubblewrap", "deny", "net denied"),
    ("bubblewrap", "allow", "net allowed"),
])
def test_status_reports_effective_network_isolation(tmp_path, monkeypatch, mode, network, expected):
    monkeypatch.setenv("AGENT_SANDBOX_NET", network)
    agent = SimpleNamespace(
        current_session="default", executor=SimpleNamespace(sandbox_mode=mode),
        start_fresh_session=lambda: ("default", True),
        _ui=_ShutdownUI(), save_session=lambda: None,
    )
    recorded = _run_main_with_agent(tmp_path, monkeypatch, agent, ["/status", "/bye"])

    main.main()

    assert expected in recorded.export_text()


def test_permissions_uses_active_role_and_renders_literal_policy_values(tmp_path, monkeypatch):
    from kernel.sandbox.permission_policy import PermissionPolicy

    calls = []
    agent = SimpleNamespace(
        current_session="default", start_fresh_session=lambda: ("default", True),
        _ui=_ShutdownUI(), save_session=lambda: None,
        executor=SimpleNamespace(
            workspace=tmp_path, active_role="devops",
            explain_shell_command=lambda command, role: (
                calls.append((command, role)) or {"verdict": "ask", "reasons": []}
            ),
        ),
    )
    monkeypatch.setattr(
        "kernel.sandbox.permission_policy.load_policy",
        lambda workspace: (PermissionPolicy(allow=("echo [red]literal[/red]",)), ""),
    )
    recorded = _run_main_with_agent(
        tmp_path, monkeypatch, agent, ["/permissions docker stop worker", "/bye"]
    )
    monkeypatch.setattr(main, "get_saved_role", lambda: "auto")

    main.main()

    assert calls == [(["docker", "stop", "worker"], "devops")]
    rendered = recorded.export_text()
    assert "Allow: echo [red]literal[/red]" in rendered
    assert "Ask: (none)" in rendered
    assert "[bold]" not in rendered
    assert "[dim]" not in rendered


def test_interrupted_session_save_does_not_crash_shutdown(tmp_path, monkeypatch):
    calls = {"save": 0}

    class FakeAgent:
        current_session = "default"

        def start_fresh_session(self):
            return self.current_session, True
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

        def start_fresh_session(self):
            return self.current_session, True
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

        def start_fresh_session(self):
            return self.current_session, True
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


class _ApiSettingsUI:
    """Scripted arrow-key menu + typed field for the /settings-api dialog."""

    def __init__(self, choices, typed=()):
        self._choices = iter(choices)
        self._typed = iter(typed)
        self.prompts = []

    def __call__(self, **kwargs):
        return self

    def choose_option(self, **kwargs):
        return next(self._choices)

    def prompt_text(self, **kwargs):
        self.prompts.append(kwargs)
        return next(self._typed)


def test_settings_api_dialog_persists_and_applies_one_edit(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_MAX_CONTEXT_TOKENS", raising=False)
    ui = _ApiSettingsUI(choices=["context_tokens", "__done__"], typed=["1m"])
    monkeypatch.setattr(main, "TerminalBannerUI", ui)
    config = main.AgentConfig(provider="openai")

    changed = main.configure_api_settings(config, False)

    assert changed == {"context_tokens"}
    assert config.max_context_tokens == 1_000_000
    assert main.load_api_settings()["context_tokens"] == 1_000_000


def test_settings_api_dialog_reprompts_after_an_impossible_value(
    tmp_path, monkeypatch
):
    ui = _ApiSettingsUI(
        choices=["max_retries", "__done__"],
        typed=["99", "4"],
    )
    monkeypatch.setattr(main, "TerminalBannerUI", ui)
    config = main.AgentConfig()

    changed = main.configure_api_settings(config, False)

    assert changed == {"max_retries"}
    assert config.max_retries == 4
    assert "between 1 and 10" in ui.prompts[1]["help_text"]


def test_settings_api_dialog_writes_nothing_when_closed(tmp_path, monkeypatch):
    ui = _ApiSettingsUI(choices=[None])
    monkeypatch.setattr(main, "TerminalBannerUI", ui)
    config = main.AgentConfig()

    assert main.configure_api_settings(config, False) == set()
    assert not (tmp_path / ".kvist" / "config.json").exists()


def test_settings_api_dialog_cancelled_field_keeps_the_old_value(
    tmp_path, monkeypatch
):
    ui = _ApiSettingsUI(choices=["request_timeout", "__done__"], typed=[None])
    monkeypatch.setattr(main, "TerminalBannerUI", ui)
    config = main.AgentConfig()

    assert main.configure_api_settings(config, False) == set()
    assert config.api_timeout == 60.0


def test_settings_api_dialog_reset_restores_every_default(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_API_INTERVAL", raising=False)
    values = main.default_api_settings()
    values["request_interval"] = 0.0
    values["dynamic_models"] = False
    main.store_api_settings(values)

    ui = _ApiSettingsUI(choices=["__reset__", "yes", "__done__"])
    monkeypatch.setattr(main, "TerminalBannerUI", ui)
    config = main.AgentConfig()
    main.apply_api_settings(config)

    changed = main.configure_api_settings(config, False)

    assert changed == {"request_interval", "dynamic_models"}
    assert main.load_api_settings() == main.default_api_settings()
    assert config.api_request_interval == 5.0
    assert config.api_interval_user_set is False
    assert main.dynamic_model_loading_enabled() is True


def test_settings_api_dialog_toggles_dynamic_model_loading(tmp_path, monkeypatch):
    ui = _ApiSettingsUI(choices=["dynamic_models", "off", "__done__"])
    monkeypatch.setattr(main, "TerminalBannerUI", ui)
    config = main.AgentConfig()

    changed = main.configure_api_settings(config, False)

    assert changed == {"dynamic_models"}
    assert main.dynamic_model_loading_enabled() is False


def test_build_agent_skips_discovery_when_dynamic_loading_is_off(
    tmp_path, monkeypatch
):
    values = main.default_api_settings()
    values["dynamic_models"] = False
    values["request_timeout"] = 240.0
    main.store_api_settings(values)

    def _unreachable(*args, **kwargs):
        raise AssertionError("dynamic model loading is off")

    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "openai")
    monkeypatch.setattr(main, "get_saved_models", lambda provider: {})
    monkeypatch.setattr(
        main,
        "ensure_provider_credentials",
        lambda **kwargs: {"openai_api_key": "key"},
    )
    monkeypatch.setattr(main, "fetch_available_models", _unreachable)
    monkeypatch.setattr(
        main,
        "create_llm_client",
        lambda provider, credentials, verbose=False: SimpleNamespace(chat=None),
    )

    _agent, config, warnings = main.build_agent(
        executor=SafeExecutor(tmp_path / "workspace"),
        max_steps=7,
        persist_logs=False,
        verbose=False,
        language_hint="en",
    )

    assert config.api_timeout == 240.0
    assert any("Dynamic model loading is off" in warning for warning in warnings)
