# -*- coding: utf-8 -*-
from __future__ import annotations


def test_call_model_debug_prints_input_tokens(agent_factory):
    agent = agent_factory(stream=False, cache_enabled=False)
    captured: list[str] = []
    agent._ui.debug_print = lambda msg: captured.append(str(msg))
    agent._current_step = 4

    agent._call_model(
        model="mock-model",
        system_prompt="you are a system prompt",
        user_prompt="hello world",
        usage_kind="step",
    )

    token_lines = [m for m in captured if "[tokens]" in m]
    assert token_lines, captured
    line = token_lines[0]
    assert "input=" in line
    assert "step=4" in line
    assert "kind=step" in line
