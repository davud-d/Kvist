# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json

import pytest

import event_stdio
from agent import LocalAgent
from agent_state import AgentConfig
from tests.conftest import FakeLLMClient


def test_launch_fails_before_build_when_provider_is_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_LLM_PROVIDER", raising=False)
    monkeypatch.setattr(event_stdio, "get_saved_provider_or_none", lambda: None)
    called = False

    def build_agent(*args, **kwargs):
        nonlocal called
        called = True

    with pytest.raises(RuntimeError, match="pre-configured provider"):
        event_stdio.launch(
            str(tmp_path),
            "Inspect project",
            build_agent,
            in_stream=io.StringIO(""),
            out_stream=io.StringIO(),
        )

    assert called is False


def test_launch_runs_real_local_agent_over_ndjson(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "openai")

    def build_agent(
        executor,
        *,
        max_steps,
        persist_logs,
        verbose,
        provider_override,
        models_override,
        ui,
    ):
        assert max_steps == 50
        assert persist_logs is False
        assert verbose is False
        assert provider_override == "omniroute"
        assert models_override == {
            "fast": "fast-id",
            "smart": "smart-id",
            "planner": "planner-id",
        }
        config = AgentConfig(
            persist_logs=False,
            api_request_interval=0,
            stream=False,
        )
        agent = LocalAgent(
            FakeLLMClient(content="Kvist GUI is connected."),
            executor,
            config,
            ui=ui,
        )
        return agent, config, []

    output = io.StringIO()
    event_stdio.launch(
        str(tmp_path),
        "Say hello",
        build_agent,
        in_stream=io.StringIO(""),
        out_stream=output,
        provider_override="omniroute",
        models_override={
            "fast": "fast-id",
            "smart": "smart-id",
            "planner": "planner-id",
        },
        routed_role="chat",
        approval_mode="deny",
    )

    events = [json.loads(line) for line in output.getvalue().splitlines()]
    # effort and planning mirror the saved user preferences, so only their
    # shape is stable across machines.
    assert events[0].pop("effort") in {"false", "low", "medium", "high", "xhigh", "max"}
    assert isinstance(events[0].pop("planning"), bool)
    assert events[0] == {
        "type": "hello",
        "protocol_version": 1,
        "provider": "openai",
        "models": {
            "fast": "gpt-5-mini",
            "smart": "gpt-5-mini",
            "planner": "gpt-5-mini",
        },
        "role": "chat",
        "approval_mode": "deny",
        "session": "default",
        "has_checkpoint": False,
    }
    # session_title is emitted from a background thread once the first turn's
    # title generation finishes, independently of the turn's own event order —
    # it may or may not have landed by the time output is read here.
    turn_events = [event for event in events if event["type"] != "session_title"]
    assert turn_events[-1]["type"] in {"final", "message", "stream_commit"}
    assert any("Kvist GUI is connected." in json.dumps(event) for event in events)


def test_launch_hello_reports_resumable_checkpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "openai")
    from executor import SafeExecutor
    from tests.conftest import NullUI

    seed = LocalAgent(
        FakeLLMClient(),
        SafeExecutor(tmp_path),
        AgentConfig(persist_logs=False, api_request_interval=0),
        ui=NullUI(),
    )
    seed.new_session("feature")
    seed._save_checkpoint("interrupted work", 3)

    def build_agent(executor, *, ui, **kwargs):
        agent = LocalAgent(
            FakeLLMClient(content="ok"),
            executor,
            AgentConfig(persist_logs=False, api_request_interval=0, stream=False),
            ui=ui,
        )
        return agent, AgentConfig(persist_logs=False, api_request_interval=0), []

    output = io.StringIO()
    event_stdio.launch(
        str(tmp_path),
        "say ok",
        build_agent,
        in_stream=io.StringIO(""),
        out_stream=output,
        routed_role="chat",
    )

    hello = json.loads(output.getvalue().splitlines()[0])
    assert hello["session"] == "feature"
    assert hello["has_checkpoint"] is True
