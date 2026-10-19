# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import stat

import httpx
import pytest

from kernel.request_log import REQUEST_LOG_ENV, wrap_transport


def _client(monkeypatch, tmp_path, handler) -> tuple[httpx.Client, object]:
    log = tmp_path / "logs" / "requests.jsonl"
    monkeypatch.setenv(REQUEST_LOG_ENV, str(log))
    return httpx.Client(transport=wrap_transport(httpx.MockTransport(handler))), log


def _records(log) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_off_without_the_variable_returns_the_transport_itself(monkeypatch):
    monkeypatch.delenv(REQUEST_LOG_ENV, raising=False)
    inner = httpx.MockTransport(lambda request: httpx.Response(200))
    assert wrap_transport(inner) is inner


def test_logs_request_body_status_headers_and_reply(monkeypatch, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True}, headers={"x-request-id": "req_42"})

    client, log = _client(monkeypatch, tmp_path, handler)
    reply = client.post(
        "https://gw.example/v1/chat/completions",
        json={"model": "cc/claude-opus-4-7", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-secret"},
    )

    assert reply.json() == {"ok": True}
    [record] = _records(log)
    assert record["request_body"]["model"] == "cc/claude-opus-4-7"
    assert record["status"] == 200
    assert record["response_headers"]["x-request-id"] == "req_42"
    assert "sk-secret" not in log.read_text(encoding="utf-8")
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_an_error_status_keeps_its_body(monkeypatch, tmp_path):
    body = {"error": {"message": "upstream overloaded"}}
    client, log = _client(monkeypatch, tmp_path, lambda request: httpx.Response(529, json=body))

    assert client.post("https://gw.example/v1/x", json={}).status_code == 529

    [record] = _records(log)
    assert record["status"] == 529
    assert "upstream overloaded" in record["response_body"]


def test_a_stream_cut_midway_is_logged_with_the_error(monkeypatch, tmp_path):
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield b"data: {\"choices\": []}\n\n"
            raise httpx.ReadError("connection reset")

    client, log = _client(
        monkeypatch, tmp_path, lambda request: httpx.Response(200, stream=Broken())
    )
    with pytest.raises(httpx.ReadError):
        client.post("https://gw.example/v1/x", json={})

    [record] = _records(log)
    assert record["status"] == 200
    assert "connection reset" in record["error"]
    assert record["response_body"].startswith("data:")


def test_a_long_reply_keeps_both_ends(monkeypatch, tmp_path):
    payload = b"A" * 10_000 + b"M" * 200_000 + b"Z" * 10_000
    client, log = _client(monkeypatch, tmp_path, lambda request: httpx.Response(200, content=payload))

    client.post("https://gw.example/v1/x", json={})

    [record] = _records(log)
    text = record["response_body"]
    assert record["response_bytes"] == len(payload)
    assert text.startswith("A") and text.endswith("Z")
    assert "bytes omitted" in text
