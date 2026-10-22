# -*- coding: utf-8 -*-
"""The style belongs to the session, not to the global preference.

`/style` writes the preference for future sessions. Whether the *active*
session takes it depends on one thing: has anything been produced under the old
choice yet.
"""
from __future__ import annotations

import pytest

from kernel.session.agent_logging import session_styles


@pytest.fixture(autouse=True)
def global_pick(monkeypatch):
    monkeypatch.setattr("kernel.config.user_config.get_saved_style", lambda style_type: "Default")


def test_a_session_without_a_stamp_adopts_the_current_pick():
    assert session_styles({}) == {"agents": "Default", "chat": "Default"}


def test_a_stamped_session_keeps_its_own_style():
    stored = {"styles": {"agents": "Paranoid", "chat": "Honest"}}
    assert session_styles(stored) == {"agents": "Paranoid", "chat": "Honest"}


@pytest.mark.parametrize("stored", [{"styles": None}, {"styles": {"agents": "  "}}, {}])
def test_a_missing_or_blank_stamp_falls_back(stored):
    assert session_styles(stored)["agents"] == "Default"


def test_new_session_is_stamped_before_its_first_step(agent_factory):
    """The stamp must exist from the start, not from the first save."""
    agent = agent_factory()
    agent._restore_session_document({})
    assert agent._session_styles == {"agents": "Default", "chat": "Default"}


def test_untouched_session_adopts_a_new_style(agent_factory):
    agent = agent_factory()
    agent._restore_session_document({})

    assert agent.adopt_session_style("Architect") is True
    assert agent.session_style("agents") == "Architect"


def test_adopting_a_style_stamps_the_chat_style_that_goes_with_it(agent_factory):
    agent = agent_factory()
    agent._restore_session_document({})

    agent.adopt_session_style("Minimalist")

    assert agent._session_styles == {"agents": "Minimalist", "chat": "No-Friendly"}


def test_session_with_work_keeps_the_style_its_history_was_made_with(agent_factory):
    agent = agent_factory()
    agent._restore_session_document({})
    agent._session_history = [{"goal": "почини таймаут", "result": "готово"}]

    assert agent.adopt_session_style("Architect") is False
    assert agent.session_style("agents") == "Default"


def test_a_switched_session_runs_under_its_own_stamp(agent_factory):
    agent = agent_factory()
    agent._restore_session_document(
        {"session_history": [], "styles": {"agents": "Minimalist", "chat": "Default"}}
    )
    assert agent.session_style("agents") == "Minimalist"


def test_the_stamp_survives_a_save_round_trip(agent_factory):
    agent = agent_factory()
    agent._restore_session_document({})
    agent.adopt_session_style("Explainer")
    agent._session_history = [{"goal": "g", "result": "r"}]
    agent.save_session()

    reloaded = agent._read_session_document(agent.current_session)
    assert reloaded["styles"] == {"agents": "Explainer", "chat": "Friendly"}
