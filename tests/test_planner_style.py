# -*- coding: utf-8 -*-
"""One session, one style.

A plan is built once, at the start of a run, and every step after it reads the
style again. If the two could come from different files, a paranoid plan would
be executed by a minimalist: the pre-check step nobody now believes in. The
style is therefore stamped on the session, not read from the global preference.
"""
from __future__ import annotations

import pytest

import agent_model
from agent_planner import PLANNER_PROMPT, create_plan

_PLAN_JSON = (
    '{"goal":"g","complexity":"simple","task_kind":"action","plan":["s"],'
    '"success_criteria":["a","b"],"risks":["r"]}'
)


@pytest.fixture
def style_dir(tmp_path, monkeypatch):
    """Point the style loader at a temporary prompts/styles tree."""
    agents = tmp_path / "agents"
    agents.mkdir()
    monkeypatch.setattr(agent_model, "_STYLES_DIR", tmp_path)
    return agents


def _planning_agent(agent_factory, captured):
    agent = agent_factory()
    agent._session_history = []

    def fake_call_model(model, system_prompt, user_prompt, **kwargs):
        captured["system_prompt"] = system_prompt
        return _PLAN_JSON

    agent._call_model = fake_call_model
    return agent


def test_missing_style_file_yields_no_block(agent_factory, style_dir):
    agent = agent_factory()
    agent._session_styles = {"agents": "Default", "chat": "Default"}
    assert agent_model.planner_style_context(agent) == ""


def test_empty_style_file_yields_no_block(agent_factory, style_dir):
    (style_dir / "minimalist.txt").write_text("", encoding="utf-8")
    agent = agent_factory()
    agent._session_styles = {"agents": "Minimalist", "chat": "Default"}
    assert agent_model.planner_style_context(agent) == ""


def test_style_is_framed_as_the_executor_not_the_planner(agent_factory, style_dir):
    (style_dir / "paranoid.txt").write_text("Отказ дороже времени.", encoding="utf-8")
    agent = agent_factory()
    agent._session_styles = {"agents": "Paranoid", "chat": "Default"}

    block = agent_model.planner_style_context(agent)

    assert block.startswith("## EXECUTOR WORKING STYLE")
    assert "Отказ дороже времени." in block
    # The planner must not adopt the voice; the framing has to say so.
    assert "never act" in block
    assert "address that agent, not you" in block


def test_planner_uses_the_agents_family_not_chat(agent_factory, style_dir):
    """The plan is executed by an action role, so the chat style is irrelevant."""
    (style_dir / "architect.txt").write_text("Форма важнее строк.", encoding="utf-8")
    (style_dir / "friendly.txt").write_text("НЕ ЭТОТ ФАЙЛ.", encoding="utf-8")
    agent = agent_factory()
    agent._session_styles = {"agents": "Architect", "chat": "Friendly"}

    block = agent_model.planner_style_context(agent)

    assert "Форма важнее строк." in block
    assert "НЕ ЭТОТ ФАЙЛ." not in block


def test_create_plan_sends_the_style_after_the_planner_prompt(agent_factory, style_dir):
    (style_dir / "explainer.txt").write_text("Сначала причина.", encoding="utf-8")
    captured = {}
    agent = _planning_agent(agent_factory, captured)
    agent._session_styles = {"agents": "Explainer", "chat": "Default"}

    create_plan(agent, "почини таймаут")

    assert captured["system_prompt"].startswith(PLANNER_PROMPT)
    assert "Сначала причина." in captured["system_prompt"]


def test_create_plan_without_a_style_sends_the_bare_planner_prompt(
    agent_factory, style_dir
):
    captured = {}
    agent = _planning_agent(agent_factory, captured)
    agent._session_styles = {"agents": "Default", "chat": "Default"}

    create_plan(agent, "почини таймаут")

    assert captured["system_prompt"] == PLANNER_PROMPT


def test_plan_and_steps_cannot_come_from_different_styles(
    agent_factory, style_dir, monkeypatch
):
    """Changing the global preference mid-session must not split the run."""
    (style_dir / "paranoid.txt").write_text("Отказ дороже времени.", encoding="utf-8")
    (style_dir / "minimalist.txt").write_text("Строка это обязательство.", encoding="utf-8")
    monkeypatch.setattr("user_config.get_saved_style", lambda style_type: "Paranoid")
    captured = {}
    agent = _planning_agent(agent_factory, captured)
    agent._session_styles = {"agents": "Paranoid", "chat": "Default"}

    create_plan(agent, "удали временные файлы")
    plan_prompt = captured["system_prompt"]

    # The user picks another style globally while the run is in flight. The
    # session was stamped Paranoid, so the remaining steps stay Paranoid.
    monkeypatch.setattr("user_config.get_saved_style", lambda style_type: "Minimalist")
    step_prompt = agent_model.build_role_system_prompt(agent, "coder")

    assert "Отказ дороже времени." in plan_prompt
    assert "Отказ дороже времени." in step_prompt
    assert "Строка это обязательство." not in step_prompt


def test_planner_prompt_documents_the_style_bound():
    """The block needs its matching bound inside the prompt, like SESSION CONTEXT."""
    assert "EXECUTOR WORKING STYLE" in PLANNER_PROMPT
    assert "never how much work is taken on" in PLANNER_PROMPT
