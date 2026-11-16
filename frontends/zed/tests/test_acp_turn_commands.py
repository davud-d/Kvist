"""``/council`` and ``/best-of`` over ACP: commands that are turns of the agent (UI_API.md section 8c)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from acp import schema

from acp_events import AcpEventTranslator
from acp_frontend import ACP_ONLY_COMMANDS, KvistAcpAgent, _decision_text, available_commands
from test_acp_frontend import Wire, _prompt, _request, fake_build_agent, run


@pytest.fixture(autouse=True)
def configured_provider(monkeypatch):
    """A configured provider is what unlocks ``session/new``; no key is read."""
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "openai")


def _agent_on_the_session_ui(client):
    """A real LocalAgent that speaks through the UI the ACP session gives it, as the real build does."""
    from kernel.agent.agent import LocalAgent
    from kernel.agent.agent_state import AgentConfig
    from kernel.tests.conftest import NullPerfLogger

    def build(executor, ui, **kwargs):
        config = AgentConfig(persist_logs=False, api_request_interval=0, retry_base_delay=0, cache_enabled=False)
        agent = LocalAgent(client=client, executor=executor, config=config, ui=ui)
        agent._perf_logger = NullPerfLogger()
        return agent, SimpleNamespace(role_selection="chat", reasoning_effort="medium", verbose=False), []

    return build


def _council_runner(seen):
    """A runner standing in for the council: its events through the agent's UI, and the answer of a finished turn."""
    from kernel.bestof.turns import TurnResult

    def runner(agent, question):
        seen.append(question)
        agent._ui.print_council_started({"council_id": "c1", "question": question, "members": []})
        agent._ui.print_council_verdict({"council_id": "c1", "chosen": "security", "decision": "d", "reason": "r", "dissent": []})
        return TurnResult(True, "council: decision chosen", "## council\n\nThe chosen decision: use security")

    return runner


def test_the_core_turn_commands_are_advertised_and_have_no_handler_of_their_own():
    from kernel import main
    from kernel.bestof.turns import TURN_COMMANDS

    advertised = dict(available_commands())
    for name in TURN_COMMANDS:
        assert name in advertised and name in ACP_ONLY_COMMANDS
        assert name not in main.STDIO_COMMANDS
    assert "council" in advertised


def test_a_council_prompt_runs_as_a_turn_and_its_final_answer_reaches_the_client(tmp_path, monkeypatch):
    from kernel.tests.conftest import FakeLLMClient

    seen: list[str] = []
    monkeypatch.setattr("kernel.bestof.turns.turn_runner", lambda name: _council_runner(seen))
    client = FakeLLMClient(content="chat", stream_chunks=["ok"])

    async def scenario():
        async with Wire(build_agent_fn=_agent_on_the_session_ui(client)) as wire:
            await wire.initialize()
            session_id = await wire.open_session(str(tmp_path))
            first = await wire.call("session/prompt", _prompt(session_id, "/council which one is simpler?"))
            assert first["result"]["stopReason"] == "end_turn", first
            assert any("The chosen decision: use security" in text for text in wire.agent_text()), wire.notifications
            # The session is free again: an ordinary request runs.
            second = await wire.call("session/prompt", _prompt(session_id, "hello there"))
            assert second["result"]["stopReason"] == "end_turn", second
            assert "ok" in "".join(wire.agent_text())

    run(scenario())
    assert seen == ["which one is simpler?"]


def test_a_council_with_no_question_says_how_to_use_it_and_runs_nothing(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr("kernel.bestof.turns.turn_runner", lambda name: _council_runner(seen))

    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            session_id = await wire.open_session(str(tmp_path))
            response = await wire.call("session/prompt", _prompt(session_id, "/council"))
            assert response["result"]["stopReason"] == "end_turn", response
            assert any("Usage: /council <question>" in text for text in wire.agent_text())

    run(scenario())
    assert seen == []


def test_a_decision_puts_its_whole_body_in_the_thread_and_asks_a_plain_choice():
    sent: list[str] = []

    class Client:
        async def session_update(self, session_id, update):
            sent.append(update.content.text)

        async def create_elicitation(self, message, mode, **kwargs):
            sent.append(f"ELICITATION: {message}")
            return schema.AcceptElicitationResponse(content={"value": "accept"}, action="accept")

    agent = KvistAcpAgent(build_agent_fn=fake_build_agent())
    agent._client = Client()
    agent._capabilities = schema.ClientCapabilities(elicitation=schema.ElicitationCapabilities(form={}))
    answered = {}
    session = SimpleNamespace(
        session_id="s1",
        translator=AcpEventTranslator(),
        session=SimpleNamespace(respond=lambda request_id, **kw: answered.update(kw, request_id=request_id)),
    )
    body = "Verdict\nwinner: e1 (tests passed)\n| executor | outcome |\n| e1 | winner |"
    event = _request(kind="choice", title="Best-of result", body=body, request_id="q1",
                     options=[{"value": "accept", "label": "Accept e1"}, {"value": "discard", "label": "Discard"}],
                     purpose="bestof_decision", run_id="r1")

    run(agent._answer_input(session, event))

    assert sent[0] == body, "the verdict is in the thread whole"
    assert sent[1] == "ELICITATION: Best-of result", "the dialog keeps its title and the options, not the body again"
    assert answered["value"] == "accept" and answered["request_id"] == "q1"


def test_a_patch_in_a_decision_keeps_its_lines_in_a_diff_block():
    patch = "diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new"
    assert _decision_text(patch) == f"```diff\n{patch}\n```"
    assert _decision_text("winner: e1") == "winner: e1"


@pytest.mark.parametrize("kind", ["bestof_started", "bestof_status", "executor_activity", "bestof_verdict", "council_card", "council_verdict"])
def test_the_events_of_best_of_and_council_translate_to_nothing(kind):
    assert AcpEventTranslator().translate(SimpleNamespace(type=kind, run_id="r1")) == []
