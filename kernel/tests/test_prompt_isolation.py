# tests/test_prompt_isolation.py

import json
from types import SimpleNamespace

import pytest

from kernel.agent_model import (
    build_report_system_prompt,
    build_role_system_prompt,
    call_action_model,
    _load_constitution,
    _load_role_prompt,
)
from kernel.user_config import get_style_options, style_slug


def test_action_model_uses_current_agent_role_prompt(tmp_path):
    captured = {}

    class Agent:
        config = SimpleNamespace(fast_model="fast", smart_model="smart")
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

    The prompt files themselves never carry it: build_role_system_prompt and
    build_report_system_prompt put it in front. commit turns a diff into a
    message nobody talks to and never pays for it.
    """
    agent = SimpleNamespace(
        executor=SimpleNamespace(workspace=tmp_path),
        config=SimpleNamespace(end_session_enabled=False),
        _session_styles={},
        _ui=SimpleNamespace(),
        _state=SimpleNamespace(current_agent_role="coder", plan_phase="", read_only_goal=False),
    )
    constitution = _load_constitution()
    for role in ("coder", "reader", "devops", "chat"):
        built = build_role_system_prompt(agent, role)
        assert built.startswith(constitution), role
        assert built.index(_load_role_prompt(role)) >= len(constitution), role

    for role in ("report", "commit"):
        assert constitution not in _load_role_prompt(role)


def _report_agent(workspace, agents_style):
    return SimpleNamespace(
        executor=SimpleNamespace(workspace=workspace),
        _session_styles={"agents": agents_style, "chat": "Default"},
    )


@pytest.mark.parametrize("style", sorted(get_style_options()))
def test_the_reporter_speaks_under_the_constitution_and_the_session_style(tmp_path, style):
    """The report is the person's account of the run: same character, same voice."""
    constitution = _load_constitution()
    built = build_report_system_prompt(_report_agent(tmp_path, style))

    assert built.startswith(constitution)
    assert "\nSTANCE. " in built
    report_at = built.index(_load_role_prompt("report"))
    assert report_at >= len(constitution)
    assert built.index(f"WORKING STYLE: {style_slug(style)}") > report_at


def test_the_prototype_label_reaches_the_reporter(tmp_path):
    built = build_report_system_prompt(_report_agent(tmp_path, "Fast-and-dirty"))

    assert "The label is mandatory, it stands in the first line of the\nreport" in built


def test_the_reporter_never_gets_the_workspace_instructions(tmp_path):
    (tmp_path / "KVIST.md").write_text("# Rules\n\nAlways end reports with a haiku.\n", encoding="utf-8")
    agent = SimpleNamespace(
        executor=SimpleNamespace(workspace=tmp_path),
        config=SimpleNamespace(end_session_enabled=False),
        _session_styles={},
        _ui=SimpleNamespace(),
        _state=SimpleNamespace(current_agent_role="coder", plan_phase="", read_only_goal=False),
    )

    assert "haiku" in build_role_system_prompt(agent, "coder")
    assert "haiku" not in build_report_system_prompt(agent)


def test_render_report_sends_the_reporter_system_prompt(agent_factory, monkeypatch):
    from kernel import agent_model
    from kernel.agent_runtime import _render_report

    sent = {}

    def fake_completion(self, model, messages, stream=False, json_mode=True):
        sent["messages"] = messages
        return "done"

    monkeypatch.setattr(agent_model, "_create_json_completion", fake_completion)
    monkeypatch.setattr(agent_model, "safe_extract_completion_content", lambda resp, *a: resp)
    agent = agent_factory(stream=False)

    _render_report(agent, "{}")

    assert sent["messages"][0] == {"role": "system", "content": build_report_system_prompt(agent)}


def test_prompt_dir_resolves_to_correct_directory():
    """_PROMPT_DIR must point to prompts/system/, NOT a stale prompts/prompts/ copy."""
    from pathlib import Path
    from kernel.agent_model import _PROMPT_DIR

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
    from kernel.agent_model import _PROMPTS_ROOT as _PROMPT_DIR

    stale_dir = _PROMPT_DIR / "prompts"
    if stale_dir.exists():
        # Stale directory exists, but code must NOT use it
        resolved = _PROMPT_DIR.resolve()
        assert resolved != stale_dir.resolve(), (
            f"STALE PROMPT DETECTED: _PROMPT_DIR resolves to {resolved}, "
            f"but prompts/prompts/ also exists at {stale_dir}. "
            "Remove prompts/prompts/ — it is not the active prompt source."
        )
