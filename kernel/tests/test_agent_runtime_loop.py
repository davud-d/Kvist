# -*- coding: utf-8 -*-
from __future__ import annotations


def test_a_rejected_call_logs_the_arguments_the_model_sent(agent_factory):
    from kernel.agent_runtime import _plan_calls
    from kernel.protocol import ModelResponse, ToolCallRequest

    agent = agent_factory()
    seen: list[str] = []
    agent._ui.debug_print = seen.append
    call = ToolCallRequest("c1", "run_shell", {"command": "pytest"})

    planned = _plan_calls(agent, ModelResponse(text="", calls=[call]), "batch")

    assert planned[0].command is None
    assert any("[rejected call] run_shell" in line and "pytest" in line for line in seen)
