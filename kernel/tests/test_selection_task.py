# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json

import pytest

from kernel.ide import completion

from kernel.agent import selection_task
from kernel.agent.agent_state import AgentConfig
from kernel.ide.completion import CompletionError
from kernel.agent.selection_task import generate_selection_reply, parse_payload
from kernel.tests.conftest import FakeLLMClient


def _payload(system: str = "Rewrite it.", user: str = "x = 1") -> str:
    return json.dumps({"system": system, "user": user})


@pytest.fixture
def fake_caller(monkeypatch):
    client = FakeLLMClient(content="y = 2")
    caller = completion._ModelCaller(
        client=client,
        config=AgentConfig(provider="openai", fast_model="gpt-5-mini", stream=False),
        _ui=completion._StderrUI(),
    )
    monkeypatch.setattr(selection_task, "_build_caller", lambda: (caller, "gpt-5-mini"))
    return client


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "[]",
        '{"system": "s"}',
        '{"user": "u"}',
        '{"system": "  ", "user": "u"}',
        '{"system": "s", "user": 1}',
    ],
)
def test_parse_payload_rejects_malformed_input(raw):
    with pytest.raises(CompletionError):
        parse_payload(raw)


def test_parse_payload_caps_fields():
    system, user = parse_payload(
        _payload(
            system="s" * (selection_task.MAX_SYSTEM_CHARS + 10),
            user="u" * (selection_task.MAX_USER_CHARS + 10),
        )
    )
    assert len(system) == selection_task.MAX_SYSTEM_CHARS
    assert len(user) == selection_task.MAX_USER_CHARS


def test_generate_sends_both_prompts_verbatim_to_the_fast_model(fake_caller):
    assert generate_selection_reply(_payload("SYS", "USR")) == "y = 2"

    request = fake_caller.requests[0]
    assert request["model"] == "gpt-5-mini"
    assert "response_format" not in request
    assert request["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]


def test_provider_failure_becomes_a_completion_error(fake_caller):
    def fail(**_kwargs):
        raise ConnectionError("socket closed")

    fake_caller.chat.completions.create = fail
    with pytest.raises(CompletionError):
        generate_selection_reply(_payload())


def _utf8_stdin(text: str) -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(text.encode("utf-8")), encoding="cp1252")


def test_cli_prints_json_reply(monkeypatch, capsys):
    from kernel import main
    monkeypatch.setattr("sys.stdin", _utf8_stdin(_payload()))
    monkeypatch.setattr(selection_task, "generate_selection_reply", lambda text: "перевод")
    main._run_selection_task_op()
    out = capsys.readouterr().out
    assert out.isascii()
    assert json.loads(out) == {"ok": True, "text": "перевод"}


def test_cli_reports_failure_as_json(monkeypatch, capsys):
    from kernel import main
    def fail(_text):
        raise CompletionError("No fast model is configured")

    monkeypatch.setattr("sys.stdin", _utf8_stdin(_payload()))
    monkeypatch.setattr(selection_task, "generate_selection_reply", fail)
    with pytest.raises(SystemExit) as exit_info:
        main._run_selection_task_op()
    assert exit_info.value.code == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": "No fast model is configured",
    }
