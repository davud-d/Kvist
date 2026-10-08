# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json

import pytest

from kernel import event_stdio, main
from kernel.event_session import EventSession
from kernel.event_stream_ui import EventStreamUI
from kernel.frontend_protocol import FrontendChannel


def _interrupted(agent):
    agent.new_session("requested")
    agent._save_checkpoint("requested task", 3)
    agent.new_session("latest")
    agent._save_checkpoint("latest task", 5)


@pytest.mark.parametrize("multi", [False, True])
def test_named_resume_streams_requested_checkpoint(agent_factory, monkeypatch, multi):
    agent = agent_factory()
    _interrupted(agent)
    calls = []
    monkeypatch.setattr(agent, "_run_loop", lambda goal, start_step: calls.append(
        (goal, start_step, agent.current_session)
    ) or "done")
    channel = FrontendChannel()
    agent._ui = EventStreamUI(channel)
    output = io.StringIO()
    serve = event_stdio.serve_session_multi if multi else event_stdio.serve_session
    serve(EventSession(channel, agent), out_stream=output, in_reader=io.StringIO(""), resume="requested")
    assert calls == [("requested task", 4, "requested")]
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert events[0]["type"] == "hello"
    assert events[0]["session"] == "requested"
    assert events[0]["has_checkpoint"] is True
    assert not any(event["type"] == "error" for event in events)


@pytest.mark.parametrize("session_id", ["missing", "../requested", "empty"])
def test_explicit_resume_never_falls_back(agent_factory, session_id):
    agent = agent_factory()
    _interrupted(agent)
    agent.new_session("empty")
    agent.switch_session("latest")
    with pytest.raises(ValueError):
        agent.select_resume_session(session_id)
    assert agent.current_session == "latest"


@pytest.mark.parametrize("frontend", ["stdio", "stdio-multi"])
def test_cli_passes_id_and_reports_missing_session(monkeypatch, tmp_path, frontend, capsys):
    def launch(*args, **kwargs):
        assert kwargs["resume"] == "missing"
        raise ValueError("Unknown session: missing")

    monkeypatch.setattr(event_stdio, "launch" if frontend == "stdio" else "launch_multi", launch)
    monkeypatch.setattr(main.sys, "argv", ["kvist", "--workspace", str(tmp_path), "--frontend", frontend, "--resume", "missing"])
    with pytest.raises(SystemExit) as error:
        main.main()
    assert error.value.code == 2
    assert "Unknown session" in capsys.readouterr().err


def test_classic_resumes_before_first_input(agent_factory, monkeypatch):
    agent = agent_factory()
    _interrupted(agent)
    calls = []
    monkeypatch.setattr(agent, "_run_loop", lambda goal, start_step: calls.append((goal, start_step)) or "done")
    monkeypatch.setattr(main.sys, "argv", ["kvist", "--workspace", str(agent.executor.workspace), "--frontend", "terminal", "--no-banner", "--resume", "requested"])
    monkeypatch.setattr(main, "build_agent", lambda **kwargs: (agent, agent.config, []))
    monkeypatch.setattr(main, "render_startup_banner", lambda *args: None)
    monkeypatch.setattr(main, "sync_strime_suggestion", lambda *args: None)
    def first_input():
        assert calls == [("requested task", 4)]
        assert agent.current_session == "requested"
        return "/bye"
    monkeypatch.setattr(main, "multiline_input", first_input)
    main.main()


def test_classic_missing_session_exits(agent_factory, monkeypatch, capsys):
    agent = agent_factory()
    monkeypatch.setattr(main.sys, "argv", ["kvist", "--workspace", str(agent.executor.workspace), "--frontend", "terminal", "--no-banner", "--resume", "missing"])
    monkeypatch.setattr(main, "build_agent", lambda **kwargs: (agent, agent.config, []))
    monkeypatch.setattr(main, "sync_strime_suggestion", lambda *args: None)
    with pytest.raises(SystemExit) as error:
        main.main()
    assert error.value.code == 2
    assert "Unknown session" in capsys.readouterr().err
