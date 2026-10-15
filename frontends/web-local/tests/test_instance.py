# -*- coding: utf-8 -*-
from __future__ import annotations

import http.client
import json
import os
import stat

import pytest

from kvist_web.instance import InstanceDescriptor


class Response:
    status = 200

    def read(self, limit):
        return b'{"service":"kvist-web"}'


class Connection:
    def __init__(self):
        self.requests = []
        self.response = Response()
        self.closed = False

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 500])
def test_stop_does_not_follow_redirect_or_accept_error(tmp_path, monkeypatch, status):
    descriptor = InstanceDescriptor(tmp_path / "controller.json")
    descriptor.write("127.0.0.1", 1234, "test-token")
    connection = Connection()
    connection.response.status = status
    monkeypatch.setattr(http.client, "HTTPConnection", lambda *a, **kw: connection)
    assert descriptor.stop() is False
    assert len(connection.requests) == 1
    assert connection.closed


def test_health_and_stop_close_connections(tmp_path, monkeypatch):
    descriptor = InstanceDescriptor(tmp_path / "controller.json")
    descriptor.write("127.0.0.1", 1234, "test-token")
    connections = []

    def connect(*args, **kwargs):
        connection = Connection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(http.client, "HTTPConnection", connect)
    assert descriptor.health()
    assert descriptor.stop()
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize("value", [{"host": "evil.example/path", "port": 80},
                                   {"host": "127.0.0.1", "port": True},
                                   {"host": "127.0.0.1", "port": 65536}, {}])
def test_invalid_descriptor_does_not_make_request(tmp_path, monkeypatch, value):
    descriptor = InstanceDescriptor(tmp_path / "controller.json")
    descriptor.path.write_text(json.dumps(value))

    def unexpected(*args, **kwargs):
        pytest.fail("invalid descriptor reached network")

    monkeypatch.setattr(http.client, "HTTPConnection", unexpected)
    assert not descriptor.health()
    assert not descriptor.stop()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_descriptor_is_private_and_does_not_follow_predictable_temp_symlink(tmp_path):
    descriptor = InstanceDescriptor(tmp_path / "controller.json")
    target = tmp_path / "victim"
    target.write_text("untouched")
    old_temp = tmp_path / f".controller.json.{os.getpid()}.tmp"
    old_temp.symlink_to(target)
    descriptor.write("127.0.0.1", 1234, "test-token")
    assert target.read_text() == "untouched"
    assert stat.S_IMODE(descriptor.path.stat().st_mode) == 0o600
    assert descriptor.read()["admin_token"] == "test-token"


@pytest.mark.parametrize(("host", "expected"), [("0.0.0.0", "127.0.0.1"), ("::", "::1"),
                                                 ("192.168.1.2", "192.168.1.2")])
def test_connection_preserves_remote_bind_support(host, expected):
    connection = InstanceDescriptor._connection({"host": host, "port": 1234}, 1)
    assert connection.host == expected
