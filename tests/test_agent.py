from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace

import pytest

from agent_state import PlanInfo
from tests.conftest import FakeLLMClient


@pytest.mark.offline_smoke
def test_local_agent_run_chat_path_uses_router_result(agent_factory, monkeypatch):
    agent = agent_factory(stream=False)
    agent.client = FakeLLMClient(content="Hello **world**")
    monkeypatch.setattr("agent_model._load_role_prompt", lambda role: "chat prompt")

    result = agent.run("hello", routed_role="chat")

    assert result == "Hello **world**"
    assert agent._state.current_agent_role == "chat"


def test_chat_redacts_credentials_before_provider_request(
    agent_factory,
    monkeypatch,
):
    synthetic = "sk-proj-" + "c" * 24
    agent = agent_factory(stream=False)
    client = FakeLLMClient(content="safe response")
    agent.client = client
    monkeypatch.setattr("agent_model._load_role_prompt", lambda role: "chat prompt")

    result = agent.run(
        f"neutral conversation api_key={synthetic}",
        routed_role="chat",
    )

    assert result == "safe response"
    assert synthetic not in str(client.requests)
    assert "[REDACTED]" in str(client.requests)


@pytest.mark.parametrize("stream", [False, True])
def test_freeflow_chat_history_stays_below_hard_input_limit(
    agent_factory,
    monkeypatch,
    stream,
):
    agent = _chat_agent(agent_factory, monkeypatch, stream=stream)
    agent.config.provider = "freeflow"
    agent._count_tokens = lambda text: len(text)
    agent.client = FakeLLMClient(content="safe response", stream_chunks=["safe response"])

    result = agent._run_chat("x" * 20000)

    assert result == "safe response"
    request_messages = agent.client.requests[0]["messages"]
    assert sum(len(message["content"]) for message in request_messages) <= 11000
    assert "context trimmed" in str(request_messages)
    assert str(request_messages[-1]["content"]).endswith("x" * 100)


def test_freeflow_chat_history_under_limit_is_not_trimmed(
    agent_factory,
    monkeypatch,
):
    agent = _chat_agent(agent_factory, monkeypatch, stream=False)
    agent.config.provider = "freeflow"
    agent._count_tokens = lambda text: len(text)
    agent.client = FakeLLMClient(content="safe response")

    agent._run_chat("short request")

    request_messages = agent.client.requests[0]["messages"]
    assert "CONTEXT TRIMMED" not in str(request_messages)


def test_chat_failure_does_not_echo_provider_credentials(
    agent_factory,
    monkeypatch,
):
    synthetic = "sk-proj-" + "f" * 24
    agent = agent_factory(stream=False)

    def fail(**kwargs):
        raise RuntimeError(f"Authorization: Bearer {synthetic}")

    agent.client.chat.completions.create = fail
    monkeypatch.setattr(
        "llm_clients.fallback_candidates_for_model",
        lambda *args, **kwargs: [],
    )
    agent._state.response_language = "en"

    result = agent._run_chat("hello")

    assert synthetic not in result
    assert "[REDACTED]" in result


# ── chat cancellation and provider-failure recovery ──────────────────────────

class _RecordingStream:
    """Minimal provider stream that can fail part-way through iteration."""

    def __init__(self, chunks, error=None):
        self._chunks = chunks
        self._error = error
        self.closed = False

    def __iter__(self):
        for chunk in self._chunks:
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=chunk))]
            )
        if self._error is not None:
            raise self._error

    def close(self):
        self.closed = True


def _chat_agent(agent_factory, monkeypatch, *, stream: bool):
    agent = agent_factory(stream=stream)
    rendered: list[str] = []

    @contextlib.contextmanager
    def live_markdown():
        yield rendered.append

    agent._ui.live_markdown = live_markdown
    agent.rendered_chunks = rendered
    monkeypatch.setattr("agent_model._load_role_prompt", lambda role: "chat prompt")
    monkeypatch.setattr(
        "llm_clients.fallback_candidates_for_model",
        lambda *args, **kwargs: [],
    )
    agent._state.response_language = "en"
    return agent


def test_chat_keyboard_interrupt_before_stream_returns_to_prompt(
    agent_factory,
    monkeypatch,
):
    agent = _chat_agent(agent_factory, monkeypatch, stream=True)

    def interrupt(**kwargs):
        raise KeyboardInterrupt

    agent.client.chat.completions.create = interrupt

    result = agent.run("hello", routed_role="chat")

    assert "stopped by you" in result.casefold()
    # The stopped turn stays in context, answered only by the stop note.
    assert [message["role"] for message in agent._chat_history] == [
        "system",
        "user",
        "assistant",
    ]
    assert agent._chat_history[-1]["content"] == (
        "[The user stopped this reply before it finished.]"
    )
    assert agent._session_history[-1]["stopped_by_user"] is True


def test_chat_keyboard_interrupt_mid_stream_keeps_partial_answer_with_note(
    agent_factory,
    monkeypatch,
):
    agent = _chat_agent(agent_factory, monkeypatch, stream=True)
    stream = _RecordingStream(["partial "], error=KeyboardInterrupt())
    agent.client.chat.completions.create = lambda **kwargs: stream

    result = agent.run("hello", routed_role="chat")

    assert "stopped by you" in result.casefold()
    assert stream.closed
    assert agent._chat_history[-1] == {
        "role": "assistant",
        "content": "partial\n\n[The user stopped this reply before it finished.]",
    }
    assert agent._session_history[-1]["goal"] == "hello"
    assert agent._session_history[-1]["stopped_by_user"] is True


def test_chat_stream_is_closed_after_a_successful_answer(
    agent_factory,
    monkeypatch,
):
    agent = _chat_agent(agent_factory, monkeypatch, stream=True)
    stream = _RecordingStream(["done"])
    agent.client.chat.completions.create = lambda **kwargs: stream

    assert agent._run_chat("hello") == "done"
    assert stream.closed


def test_chat_provider_error_mid_stream_rolls_back_history(
    agent_factory,
    monkeypatch,
):
    synthetic = "sk-proj-" + "d" * 24
    agent = _chat_agent(agent_factory, monkeypatch, stream=True)
    stream = _RecordingStream(
        ["partial "],
        error=RuntimeError(f"Authorization: Bearer {synthetic}"),
    )
    agent.client.chat.completions.create = lambda **kwargs: stream

    result = agent._run_chat("hello")

    assert synthetic not in result
    assert "[REDACTED]" in result
    assert stream.closed
    assert [message["role"] for message in agent._chat_history] == ["system"]


def test_chat_recovers_for_the_next_request_after_cancellation(
    agent_factory,
    monkeypatch,
):
    agent = _chat_agent(agent_factory, monkeypatch, stream=True)

    def interrupt(**kwargs):
        raise KeyboardInterrupt

    agent.client.chat.completions.create = interrupt
    agent.run("first", routed_role="chat")

    second = _RecordingStream(["second answer"])
    agent.client.chat.completions.create = lambda **kwargs: second

    assert agent.run("second", routed_role="chat") == "second answer"
    assert [message["role"] for message in agent._chat_history] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert agent._chat_history[-1]["content"] == "second answer"


@pytest.mark.offline_smoke
def test_local_agent_run_coder_path_builds_plan_and_runtime(agent_factory, monkeypatch):
    agent = agent_factory()
    calls = {}

    agent._create_plan = lambda goal: PlanInfo(goal, "simple", ["inspect"], ["done"], [])
    def fake_run_loop(goal, start_step):
        calls["args"] = (goal, start_step)
        return "finished"

    agent._run_loop = fake_run_loop
    monkeypatch.setattr("agent.detect_dependency_versions", lambda workspace: {"pytest": "==8"})

    result = agent.run("edit file", routed_role="coder")

    assert result == "finished"
    assert calls["args"] == ("edit file", 1)
    assert agent._state.plan_info.plan == ["inspect"]
    assert agent._state.dependency_versions == {"pytest": "==8"}


def test_local_agent_resume_returns_none_without_checkpoint(agent_factory):
    assert agent_factory().resume() is None


def test_local_agent_resume_repopulates_environment_context(agent_factory):
    agent = agent_factory(persist_logs=True)
    agent._save_checkpoint("do the task", 2)

    captured = {}

    def fake_run_loop(goal, start_step):
        # Env facts must be set before the loop builds its first prompt,
        # otherwise the OS/Shell lines render blank after /resume.
        captured["os"] = agent._state.system_os
        captured["shell"] = agent._state.system_shell
        return "done"

    agent._run_loop = fake_run_loop

    assert agent.resume() == "done"
    assert captured["os"]
    assert captured["shell"]


def test_switch_session_saves_outgoing_and_restores_selected_session(agent_factory):
    agent = agent_factory()
    agent.new_session("alpha")
    agent._session_history = [
        {"goal": "alpha task", "role": "coder", "result": "alpha result"}
    ]
    agent._chat_history = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "alpha chat"},
    ]

    agent.new_session("beta")
    agent._session_history = [
        {"goal": "beta task", "role": "devops", "result": "beta result"}
    ]
    agent.save_session()

    agent.switch_session("alpha")

    assert agent.current_session == "alpha"
    assert agent._session_history[0]["goal"] == "alpha task"
    assert agent._chat_history[-1]["content"] == "alpha chat"

    restored = agent_factory()
    assert restored.current_session == "alpha"
    assert restored._session_history[0]["goal"] == "alpha task"


def test_switch_session_rejects_invalid_name_before_mutating_state(agent_factory):
    agent = agent_factory()

    with pytest.raises(ValueError, match="Invalid session name"):
        agent.switch_session("../outside")

    assert agent.current_session == "default"


def test_deleted_session_backup_is_not_restored(agent_factory):
    agent = agent_factory()
    agent.new_session("temporary")
    agent._session_history = [{"goal": "must be deleted"}]
    agent.save_session()
    agent.save_session()
    state_path = (
        agent.executor.workspace
        / ".kvist"
        / "sessions"
        / "temporary.json"
    )
    assert state_path.with_suffix(".json.bak").is_file()

    # Leave "temporary" for a fresh session; the active one cannot be deleted.
    agent.new_session()
    assert agent._delete_session("temporary") is True

    assert "temporary" not in agent._list_sessions()
    assert not state_path.exists()
    assert not state_path.with_suffix(".json.bak").exists()


def test_new_named_session_never_backs_up_outgoing_history(agent_factory):
    agent = agent_factory()
    agent._session_history = [{"goal": "private outgoing history"}]
    agent._last_response_language = "ru"
    agent.save_session()

    agent.new_session("fresh")

    state_path = (
        agent.executor.workspace / ".kvist" / "sessions" / "fresh.json"
    )
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["session_history"] == []
    assert payload["response_language"] is None
    assert not state_path.with_suffix(".json.bak").exists()
    assert agent._session_history == []
    assert agent._last_response_language is None


def test_new_named_session_refuses_to_overwrite_existing_data(agent_factory):
    agent = agent_factory()
    agent.new_session("preserved")
    agent._session_history = [{"goal": "keep me"}]
    agent.save_session()
    agent.new_session("elsewhere")

    with pytest.raises(ValueError, match="already exists"):
        agent.new_session("preserved")

    assert agent.current_session == "elsewhere"
    agent.switch_session("preserved")
    assert agent._session_history == [{"goal": "keep me"}]


def test_failed_switch_to_corrupt_session_does_not_mutate_current_state(
    agent_factory,
):
    agent = agent_factory()
    agent._session_history = [{"goal": "current state"}]
    agent.save_session()
    corrupt_path = (
        agent.executor.workspace / ".kvist" / "sessions" / "corrupt.json"
    )
    corrupt_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="corrupt"):
        agent.switch_session("corrupt")

    assert agent.current_session == "default"
    assert agent._session_history == [{"goal": "current state"}]
    active = json.loads(
        (
            agent.executor.workspace / ".kvist" / "active_session.json"
        ).read_text(encoding="utf-8")
    )
    assert active["session"] == "default"
