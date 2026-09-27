# -*- coding: utf-8 -*-
"""The universal role reuses the runtime's shared "no tool call ends the
turn" mechanism (agent_runtime._run_turn) and its shared completion
blockers — nothing role-specific was added to either. These tests prove
both hold for "universal" exactly as they do for "coder": a plain-text
reply finishes the turn immediately, and a claimed-done change with no
actual edit is still refused.
"""
from __future__ import annotations

from agent_runtime import _evidence_completion_blockers
from agent_state import PlanInfo


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
    # task_kind="action": a greeting is not a "report" (which would demand an
    # 80+ character substantive answer, see agent_runtime._is_substantive_answer)
    # nor a "change" (which would demand a file edit) — it is a short status
    # exchange, exactly like the "action" kind's own definition.
    agent._create_plan = lambda goal: PlanInfo(
        goal, "simple", [], [], [], task_kind="action"
    )

    result = agent.run("Привет, как дела?", routed_role="universal")

    assert result == reply
    assert agent._state.current_agent_role == "universal"
    assert agent._state.run_status != "blocked"


def test_universal_change_task_is_still_blocked_without_a_real_edit(agent_factory):
    """Completion blockers are generic (agent_runtime._evidence_completion_blockers
    does not branch on role), so a "universal" run claiming a change is done
    while files_changed is empty must be refused exactly like a coder run."""
    agent = agent_factory()
    agent._state.current_agent_role = "universal"
    agent._state.create_goal = True
    agent._state.task_language = "en"

    blockers = _evidence_completion_blockers(agent)

    kinds = [kind for kind, _text in blockers]
    assert "no_changes" in kinds
