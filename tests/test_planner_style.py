# -*- coding: utf-8 -*-
"""The planner must know which working style will execute its plan.

A plan built without the style pulls against the executor: a paranoid agent is
told to check before destructive actions but never gets that step planned, a
minimalist agent gets a plan full of additions it will argue with.
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


def _saved_style(monkeypatch, name: str) -> None:
    monkeypatch.setattr("user_config.get_saved_style", lambda style_type: name)


def _planning_agent(agent_factory, captured):
    agent = agent_factory()
    agent._session_history = []

    def fake_call_model(model, system_prompt, user_prompt, **kwargs):
        captured["system_prompt"] = system_prompt
        return _PLAN_JSON

    agent._call_model = fake_call_model
    return agent


def test_missing_style_file_yields_no_block(style_dir, monkeypatch):
    _saved_style(monkeypatch, "Default")
    assert agent_model.planner_style_context() == ""


def test_empty_style_file_yields_no_block(style_dir, monkeypatch):
    _saved_style(monkeypatch, "Minimalist")
    (style_dir / "minimalist.txt").write_text("", encoding="utf-8")
    assert agent_model.planner_style_context() == ""


def test_style_is_framed_as_the_executor_not_the_planner(style_dir, monkeypatch):
    _saved_style(monkeypatch, "Paranoid")
    (style_dir / "paranoid.txt").write_text("Failure outcosts time.", encoding="utf-8")

    block = agent_model.planner_style_context()

    assert block.startswith("## EXECUTOR WORKING STYLE")
    assert "Failure outcosts time." in block
    # The planner must not adopt the voice; the framing has to say so.
    assert "never act" in block
    assert "address that agent, not you" in block


def test_planner_uses_the_agents_family_not_chat(style_dir, monkeypatch):
    """The plan is executed by an action role, so the chat style is irrelevant."""
    captured = {}

    def fake_saved_style(style_type):
        captured["style_type"] = style_type
        return "Architect"

    monkeypatch.setattr("user_config.get_saved_style", fake_saved_style)
    (style_dir / "architect.txt").write_text("Shape over lines.", encoding="utf-8")

    agent_model.planner_style_context()
    assert captured["style_type"] == "agents"


def test_create_plan_sends_the_style_after_the_planner_prompt(
    agent_factory, style_dir, monkeypatch
):
    _saved_style(monkeypatch, "Explainer")
    (style_dir / "explainer.txt").write_text("Cause first.", encoding="utf-8")
    captured = {}
    agent = _planning_agent(agent_factory, captured)

    create_plan(agent, "fix the timeout")

    assert captured["system_prompt"].startswith(PLANNER_PROMPT)
    assert "Cause first." in captured["system_prompt"]


def test_create_plan_without_a_style_sends_the_bare_planner_prompt(
    agent_factory, style_dir, monkeypatch
):
    _saved_style(monkeypatch, "Default")
    captured = {}
    agent = _planning_agent(agent_factory, captured)

    create_plan(agent, "fix the timeout")

    assert captured["system_prompt"] == PLANNER_PROMPT


def test_planner_prompt_documents_the_style_bound():
    """The block needs its matching bound inside the prompt, like SESSION CONTEXT."""
    assert "EXECUTOR WORKING STYLE" in PLANNER_PROMPT
    assert "never how much work is taken on" in PLANNER_PROMPT
