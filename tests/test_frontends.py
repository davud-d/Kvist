# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

import frontends
import main
from agent import LocalAgent
from agent_state import AgentConfig
from event_session import EventSession
from event_stream_ui import EventStreamUI
from tests.conftest import FakeLLMClient


def test_cli_parser_accepts_frontend_flag():
    args = main._build_cli_parser().parse_args(["--workspace", "x", "--frontend", "stdio"])
    assert args.frontend == "stdio"
    default = main._build_cli_parser().parse_args(["--workspace", "x"])
    assert default.frontend == "terminal"


def test_load_launcher_builtin_and_unknown():
    assert callable(frontends.load_launcher("stdio"))
    with pytest.raises(RuntimeError):
        frontends.load_launcher("definitely_not_a_frontend")


def test_make_session_injects_event_stream_ui(tmp_path, monkeypatch):
    captured = {}

    def fake_build_agent(executor, **kwargs):
        captured["ui"] = kwargs.get("ui")
        agent = LocalAgent(
            FakeLLMClient(),
            executor,
            AgentConfig(persist_logs=False, api_request_interval=0),
            ui=kwargs["ui"],
        )
        return agent, agent.config, []

    monkeypatch.setattr(main, "build_agent", fake_build_agent)

    session = frontends.make_session(str(tmp_path))

    assert isinstance(session, EventSession)
    assert isinstance(captured["ui"], EventStreamUI)
