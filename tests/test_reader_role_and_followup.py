# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from agent_state import PlanInfo
from role_router import (
    RoleRouteResult,
    has_no_write_marker,
    route_role,
    route_role_decision_with_llm,
)


# ── BUG-3: read-only requests get the reader role and cannot write ──────────

def test_has_no_write_marker_detects_explicit_read_only():
    assert has_no_write_marker("прочитай конфиг, но ничего не меняй")
    assert has_no_write_marker("review main.py but do not modify it")
    assert has_no_write_marker("look at settings (read-only)")
    assert not has_no_write_marker("исправь баг в main.py")


def test_keyword_route_sends_no_write_request_to_reader():
    assert route_role("прочитай hyprland config и ничего не меняй") == "reader"
    assert route_role("read the settings file, don't change anything") == "reader"


class _FakeRouterClient:
    """Returns 'coder' from the LLM so we can prove the direct read-only rule
    wins before the model is ever consulted."""

    def __init__(self):
        self.calls = 0
        self.chat = self

        class _Completions:
            def __init__(self, outer):
                self._outer = outer

            def create(self, **kwargs):
                self._outer.calls += 1
                from types import SimpleNamespace

                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="coder"))]
                )

        self.completions = _Completions(self)


def test_direct_read_only_rule_beats_llm():
    client = _FakeRouterClient()
    decision = route_role_decision_with_llm(
        user_goal="Найди конфиг hyprland, прочитай и скажи что улучшить, но ничего не меняй",
        client=client,
        model="mock",
    )
    assert isinstance(decision, RoleRouteResult)
    assert decision.role == "reader"
    assert decision.source == "direct"
    assert client.calls == 0


def test_run_loop_reader_role_blocks_file_writes(agent_factory):
    agent = agent_factory(max_steps=3)
    agent._state.current_agent_role = "reader"
    agent.executor.set_active_role("reader")
    agent._state.plan_info = PlanInfo(
        goal="Прочитать конфиг и дать рекомендации",
        complexity="simple",
        plan=["inspect config", "report findings"],
        success_criteria=["config is read"],
        risks=[],
    )
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"
    agent._state.response_language = "ru"
    agent._state.task_language = "ru"

    responses = [
        {
            "thought": "Пробую записать",
            "plan_step": "edit",
            "progress": 40,
            "user_message": "Пишу",
            "action": {"type": "write_file", "args": {"path": "out.txt", "content": "x"}},
        },
        {
            "thought": "Читаю",
            "plan_step": "inspect",
            "progress": 60,
            "user_message": "Читаю",
            "action": {"type": "read_file", "args": {"path": "out.txt"}},
        },
    ]
    call_count = {"value": 0}

    def fake_call_action_model(prompt: str) -> str:
        index = call_count["value"]
        if index >= len(responses):
            # After the write is refused, stop feeding actions.
            return json.dumps(responses[-1], ensure_ascii=False)
        call_count["value"] += 1
        return json.dumps(responses[index], ensure_ascii=False)

    agent._call_action_model = fake_call_action_model

    agent._run_loop("создай out.txt с содержимым", start_step=1)

    # The create-style goal was overridden to read-only by the reader role:
    # the write must never touch the workspace.
    assert not (agent.executor.workspace / "out.txt").exists()
    assert agent._state.read_only_goal is True
    assert agent._state.create_goal is False
    assert any(
        "read-only" in str(log.get("feedback", {}).get("error", "")).lower()
        for log in agent._state.step_logs
    )


# ── BUG-4: the length gate scales with turns completed in this process ──────


def test_validate_user_goal_rejects_short_input_on_a_cold_conversation():
    from main import validate_user_goal

    error = validate_user_goal("зачем?", language_hint="ru", completed_turns=0)
    assert error is not None


def test_validate_user_goal_accepts_short_followup_after_two_turns():
    from main import validate_user_goal

    error = validate_user_goal("зачем?", language_hint="ru", completed_turns=2)
    assert error is None


def test_validate_user_goal_keeps_the_cold_threshold_after_one_turn():
    """One finished turn is not yet a conversation; the gate must not relax."""
    from main import validate_user_goal

    assert validate_user_goal("зачем?", language_hint="ru", completed_turns=1)


def test_validate_user_goal_rejects_a_bare_dot_regardless_of_turns():
    from main import validate_user_goal

    for turns in (0, 1, 2, 50):
        assert validate_user_goal(".", language_hint="ru", completed_turns=turns)


def test_validate_user_goal_still_rejects_empty_after_many_turns():
    from main import validate_user_goal

    error = validate_user_goal("   ", language_hint="ru", completed_turns=50)
    assert error is not None


def test_validate_user_goal_defaults_to_the_cold_threshold():
    """A caller that forgets the counter must get the strict gate, not the loose one."""
    from main import validate_user_goal

    assert validate_user_goal("зачем?", language_hint="ru")


def test_minimum_prompt_length_tiers():
    from main import minimum_prompt_length

    assert minimum_prompt_length(0) == 8
    assert minimum_prompt_length(1) == 8
    assert minimum_prompt_length(2) == 4
    assert minimum_prompt_length(99) == 4
