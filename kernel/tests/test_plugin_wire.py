# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import socket
import struct
import threading

import pytest

from kernel.plugins.runner.kvist_wire import Channel


def _frame(message: dict) -> bytes:
    body = json.dumps(message).encode("utf-8")
    return struct.pack(">I", len(body)) + body


def _receive_in_thread(channel: Channel, timeout: float) -> tuple[threading.Thread, list]:
    answers: list = []
    thread = threading.Thread(target=lambda: answers.append(channel.receive(timeout=timeout)), daemon=True)
    thread.start()
    return thread, answers


@pytest.fixture
def pair():
    # The default family: AF_UNIX where it exists, a loopback TCP pair on Windows; framing is the same.
    core, runner = socket.socketpair()
    yield Channel(core), runner
    core.close()
    runner.close()


@pytest.mark.parametrize("cut", [1, 4, 10], ids=["inside-header", "after-header", "inside-body"])
def test_an_unfinished_frame_returns_at_the_timeout_and_completes_on_a_later_call(pair, cut):
    channel, runner = pair
    frame = _frame({"kind": "result", "call_id": "c1", "status": "success", "value": "x" * 40})
    runner.sendall(frame[:cut])

    thread, answers = _receive_in_thread(channel, 0.05)
    thread.join(1.0)

    assert not thread.is_alive()
    assert answers == [None]
    runner.sendall(frame[cut:])
    assert channel.receive(timeout=1.0)["call_id"] == "c1"


def test_a_frame_sent_byte_by_byte_never_holds_the_reader_past_its_timeout(pair):
    channel, runner = pair
    frame = _frame({"kind": "event", "call_id": "c1", "name": "n"})
    received = None
    for index in range(len(frame)):
        runner.sendall(frame[index:index + 1])
        thread, answers = _receive_in_thread(channel, 0.02)
        thread.join(1.0)
        assert not thread.is_alive()
        received = answers[0]
    assert received is not None and received["kind"] == "event"


def test_two_frames_in_one_read_come_out_one_per_call(pair):
    channel, runner = pair
    runner.sendall(_frame({"kind": "event", "name": "a"}) + _frame({"kind": "event", "name": "b"}))

    assert channel.receive(timeout=1.0)["name"] == "a"
    assert channel.receive(timeout=1.0)["name"] == "b"
    assert channel.receive(timeout=0.02) is None
