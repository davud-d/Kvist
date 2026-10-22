# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json
import threading

import pytest

from kernel.ide import completion

from kernel.ide import completion_server
from kernel.agent.agent_state import AgentConfig
from kernel.ide.completion_server import CompletionServer
from kernel.tests.conftest import FakeLLMClient


def _line(request_id: int, prefix: str = "x = 1\n") -> str:
    return json.dumps({"id": request_id, "file_path": "main.py", "language": "python", "prefix": prefix, "suffix": ""})


class _Out(io.StringIO):
    def messages(self) -> list[dict]:
        return [json.loads(line) for line in self.getvalue().splitlines()]


def _wait_for(server: CompletionServer, request_id: int) -> None:
    for _ in range(200):
        with server._running_lock:
            if request_id not in server._running:
                return
        threading.Event().wait(0.01)
    raise AssertionError("request did not finish")


@pytest.fixture
def server(tmp_path, monkeypatch):
    client = FakeLLMClient(stream_chunks=["return ", "1"])
    caller = completion._ModelCaller(client, AgentConfig(provider="openai"), completion._StderrUI())
    built = []
    monkeypatch.setattr(completion_server, "completion_target", lambda: ("openai", "m"))
    monkeypatch.setattr(completion_server, "_build_caller", lambda: built.append(1) or (caller, "m"))
    out = _Out()
    instance = CompletionServer(tmp_path, out)
    instance.built = built
    instance.out = out
    return instance


def test_request_streams_deltas_then_done(server):
    server.handle_line(_line(7))
    _wait_for(server, 7)
    assert server.out.messages() == [
        {"id": 7, "delta": "return "}, {"id": 7, "delta": "1"}, {"id": 7, "done": True},
    ]


def test_model_client_is_built_once_while_the_target_is_unchanged(server):
    for request_id in (1, 2):
        server.handle_line(_line(request_id))
        _wait_for(server, request_id)
    assert len(server.built) == 1


def test_new_target_rebuilds_the_model_client(server, monkeypatch):
    server.handle_line(_line(1))
    _wait_for(server, 1)
    monkeypatch.setattr(completion_server, "completion_target", lambda: ("openai", "another"))
    server.handle_line(_line(2))
    _wait_for(server, 2)
    assert len(server.built) == 2


def test_failure_is_reported_for_that_request_only(server):
    server.handle_line(json.dumps({"id": 3, "file_path": ".env", "language": "python", "prefix": "", "suffix": ""}))
    _wait_for(server, 3)
    [message] = server.out.messages()
    assert message["id"] == 3 and "error" in message
    server.handle_line(_line(4))
    _wait_for(server, 4)
    assert server.out.messages()[-1] == {"id": 4, "done": True}


def test_a_newer_request_cancels_the_running_one(server):
    server._running[1] = stop = threading.Event()
    server.handle_line(_line(2))
    assert stop.is_set()
    _wait_for(server, 2)


def test_cancel_message_silences_the_request(server):
    server._running[5] = stop = threading.Event()
    server.handle_line(json.dumps({"cancel": 5}))
    assert stop.is_set()


@pytest.mark.parametrize("line", ["", "not json", "[]", '{"id": "x"}', '{"cancel": "x"}'])
def test_malformed_lines_are_ignored(server, line):
    server.handle_line(line)
    assert server.out.getvalue() == ""
