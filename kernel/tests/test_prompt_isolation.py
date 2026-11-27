# tests/test_prompt_isolation.py

import json
from types import SimpleNamespace

import pytest

from kernel.agent.agent_model import (
    build_role_system_prompt,
    call_action_model,
    _load_constitution,
    _load_role_prompt,
)
from kernel.config.user_config import get_style_options, style_slug


def test_action_model_uses_current_agent_role_prompt(tmp_path):
    captured = {}

    class Agent:
        config = SimpleNamespace(provider="openai", fast_model="fast", smart_model="smart", voice_mode=False)
        executor = SimpleNamespace(workspace=tmp_path)
        _ui = SimpleNamespace()
        _state = SimpleNamespace(
            current_agent_role="devops",
            selected_action_model="",
            selected_action_role="",
            plan_phase="",
            read_only_goal=False,
        )

        def _select_action_model_for_run(self):
            return "fast"

        def _prefer_smart_model(self):
            return False
        
        def _call_model(self, model, system_prompt, user_prompt, **kwargs):
            captured["model"] = model
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return "{}"

    call_action_model(Agent(), "test task")

    # build_role_system_prompt narrows: constitution, role prompt, then the
    # selected style and this workspace's instructions. So the role prompt is
    # neither the prefix nor the whole system prompt.
    system_prompt = captured["system_prompt"]
    assert system_prompt.startswith(_load_constitution())
    assert _load_role_prompt("devops") in system_prompt
    assert _load_role_prompt("coder") not in system_prompt
    assert _load_role_prompt("chat") not in system_prompt


def test_constitution_precedes_every_acting_role(tmp_path):
    """The layer above every role that acts or speaks to the person.

    The prompt files themselves never carry it: build_role_system_prompt puts it
    in front. commit turns a diff into a message nobody talks to and never pays for it.
    """
    agent = SimpleNamespace(
        executor=SimpleNamespace(workspace=tmp_path),
        config=SimpleNamespace(end_session_enabled=False, provider="openai", fast_model="gpt-5-mini", smart_model="gpt-5", voice_mode=False),
        _session_styles={},
        _ui=SimpleNamespace(),
        _state=SimpleNamespace(current_agent_role="coder", plan_phase="", read_only_goal=False),
    )
    constitution = _load_constitution()
    for role in ("coder", "reader", "devops", "chat"):
        built = build_role_system_prompt(agent, role)
        assert built.startswith(constitution), role
        assert built.index(_load_role_prompt(role)) >= len(constitution), role

    assert constitution not in _load_role_prompt("commit")


@pytest.mark.parametrize("role", ["coder", "reader", "devops", "chat", "universal"])
def test_roles_receive_current_command_reference(tmp_path, role):
    from kernel.protocol.slash_commands import SLASH_COMMANDS

    agent = SimpleNamespace(
        executor=SimpleNamespace(workspace=tmp_path),
        config=SimpleNamespace(end_session_enabled=False, provider="openai", fast_model="gpt-5-mini", smart_model="gpt-5", voice_mode=False),
        _session_styles={},
        _ui=SimpleNamespace(),
        _state=SimpleNamespace(current_agent_role=role, plan_phase="", read_only_goal=False),
    )
    built = build_role_system_prompt(agent, role)
    for command in SLASH_COMMANDS:
        assert f"/{command.name}:" in built or f"/{command.name}," in built
        assert command.description in built
        for alias in command.aliases:
            assert f"/{alias}" in built
    assert "without assuming the user is a beginner" in built
    assert "Describing a command does not execute it" in built
    assert "Do not invent slash commands, flags, subcommands or arguments" in built
    assert "When frontend details are unknown, recommend the bare command" in built
    assert "/role offers auto, coder, reader, devops, chat and universal" in built
    assert "reference does not reveal the active role" in built


@pytest.mark.parametrize("style", sorted(get_style_options()))
@pytest.mark.parametrize("role", ["coder", "reader", "devops", "chat"])
def test_every_style_and_role_gets_the_shared_voice(agent_factory, style, role):
    from kernel.agent.agent_model import _STYLES_DIR
    from kernel.config.user_config import chat_style_for

    agent = agent_factory()
    agent._session_styles = {"agents": style, "chat": chat_style_for(style)}
    built = build_role_system_prompt(agent, role)
    common = (_STYLES_DIR / "common.txt").read_text(encoding="utf-8").strip()

    assert built.count(common) == 1
    assert built.index(common) > built.index(_load_role_prompt(role))
    family = "chat" if role == "chat" else "agents"
    selected = agent._session_styles[family]
    specific = (_STYLES_DIR / family / f"{style_slug(selected)}.txt").read_text(
        encoding="utf-8"
    ).strip()
    assert built.index(specific) > built.index(common)


def test_chat_inherits_only_the_stamped_working_styles_voice(agent_factory, monkeypatch):
    from kernel.config.user_config import chat_style_for

    agent = agent_factory()
    # Both choices have the same chat palette, but retain their own accent.
    assert chat_style_for("Explainer") == chat_style_for("Friendly Developer")
    monkeypatch.setattr("kernel.config.user_config.get_saved_style", lambda family: "Minimalist")
    agent._session_styles = {"agents": "Explainer", "chat": "Friendly"}
    explaining = build_role_system_prompt(agent, "chat")
    agent._session_styles = {"agents": "Friendly Developer", "chat": "Friendly"}
    friendly = build_role_system_prompt(agent, "chat")

    assert "the distinctive contribution is the causal link" in explaining
    assert "The distinctive contribution is the causal link" not in friendly
    assert "warmth is visible in making the situation easy to follow" in friendly
    for built in (explaining, friendly):
        assert "working style:" not in built
        assert "== TWO FORKS ==" not in built
        assert "Economy applies to the person's reading" not in built
        assert _load_role_prompt("chat") in built


def test_shared_voice_and_chat_accent_are_reread_per_call(agent_factory, monkeypatch, tmp_path):
    from kernel.agent import agent_model
    styles = tmp_path / "styles"
    (styles / "agents").mkdir(parents=True)
    (styles / "chat").mkdir()
    common = styles / "common.txt"
    accent = styles / "agents" / "default.txt"
    common.write_text("Shared voice before edit", encoding="utf-8")
    accent.write_text(
        "Working instructions\n== how it sounds ==\nOld accent\n== THE LIMIT ==\nWork limit",
        encoding="utf-8",
    )
    (styles / "chat" / "default.txt").write_text("Chat palette", encoding="utf-8")
    monkeypatch.setattr(agent_model, "_STYLES_DIR", styles)
    agent = agent_factory()
    agent._session_styles = {"agents": "Default", "chat": "Default"}

    before = build_role_system_prompt(agent, "chat")
    common.write_text("Shared voice after edit", encoding="utf-8")
    accent.write_text("== how it sounds ==\nNew accent", encoding="utf-8")
    after = build_role_system_prompt(agent, "chat")

    assert "Shared voice before edit" in before
    assert "Old accent" in before
    assert "Working instructions" not in before
    assert "Work limit" not in before
    assert "Shared voice after edit" in after
    assert "New accent" in after
    assert "Shared voice before edit" not in after
    assert "Old accent" not in after


def test_missing_selected_style_keeps_the_shared_voice(agent_factory):
    agent = agent_factory()
    agent._session_styles = {"agents": "Missing", "chat": "Missing"}
    for role in ("coder", "chat"):
        built = build_role_system_prompt(agent, role)
        assert "shared voice: Kvist" in built
        assert "== selected style's conversation accent ==" not in built


def test_prompt_dir_resolves_to_correct_directory():
    """_PROMPT_DIR must point to prompts/system/, NOT a stale prompts/prompts/ copy."""
    from pathlib import Path
    from kernel.agent.agent_model import _PROMPT_DIR

    resolved = _PROMPT_DIR.resolve()
    assert resolved.name == "system", f"_PROMPT_DIR resolves to {resolved}, expected .../prompts/system"
    assert resolved.parent.name == "prompts", f"_PROMPT_DIR resolves to {resolved}"
    # Must NOT end with prompts/prompts
    assert not str(resolved).endswith("prompts/prompts"), (
        f"_PROMPT_DIR points to stale duplicate: {resolved}"
    )


def test_prompt_duplicate_directory_not_loaded():
    """Verify that prompts/prompts/ (if it exists) is NOT the active prompt source."""
    from pathlib import Path
    from kernel.agent.agent_model import _PROMPTS_ROOT as _PROMPT_DIR

    stale_dir = _PROMPT_DIR / "prompts"
    if stale_dir.exists():
        # Stale directory exists, but code must NOT use it
        resolved = _PROMPT_DIR.resolve()
        assert resolved != stale_dir.resolve(), (
            f"STALE PROMPT DETECTED: _PROMPT_DIR resolves to {resolved}, "
            f"but prompts/prompts/ also exists at {stale_dir}. "
            "Remove prompts/prompts/ — it is not the active prompt source."
        )
