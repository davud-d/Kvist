# -*- coding: utf-8 -*-
"""The universal role shares model-owned completion with the other action roles."""
from __future__ import annotations



def test_universal_plain_conversation_ends_the_turn_without_any_tool_call(agent_factory):
    """A conversational message routed to "universal" gets a text-only reply.

    The action model's first response (also used for the native-tools probe,
    which will not look like a valid "ping" call and so falls back to the
    JSON-mode text protocol) carries no <tool_call> block, so the turn must
    end on step 1 with that text as the final answer — not a plan-not-
    complete refusal, not another round trip.
    """
    reply = "Привет! У меня всё хорошо, работаю. А у тебя как дела?"
    agent = agent_factory()
    # The action loop streams its calls; FakeLLMClient answers a streamed
    # request from stream_chunks, not from .content (that field only backs a
    # non-streaming call, such as the native-tools "ping" probe).
    agent.client.content = reply
    agent.client.stream_chunks = [reply]

    result = agent.run("Привет, как дела?", routed_role="universal")

    assert result == reply
    assert agent._state.current_agent_role == "universal"
    assert agent._state.run_status != "blocked"


def test_universal_can_finish_a_build_without_source_edits(agent_factory):
    from kernel.agent.agent_runtime import _run_final_answer
    from kernel.tests.conftest import make_command

    agent = agent_factory()
    agent._state.current_agent_role = "universal"
    agent._state.create_goal = True
    outcome = _run_final_answer(agent, "Build the binary", 1,
        make_command("final_answer", {"message": "The build is available."}, "done"))

    assert outcome.finished
    assert outcome.final_message == "The build is available."
    assert agent._state.run_status == "completed"
