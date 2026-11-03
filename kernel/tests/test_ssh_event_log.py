# -*- coding: utf-8 -*-
"""The daemon's log: what is written, what a frontend starting from nothing is shown, when it starts over."""
from __future__ import annotations

import json

from kernel.ssh.event_log import EventLog, fresh_replay, lines_from


def line(**fields) -> str:
    return json.dumps(fields) + "\n"


def replayed(path, open_requests=()) -> list[dict]:
    return [json.loads(raw) for raw, _ in fresh_replay(path, set(open_requests)) if raw is not None]


def test_every_write_is_a_whole_line_in_the_file(tmp_path):
    log = EventLog(tmp_path / "events.ndjson")

    log.write(line(type="hello"))
    log.write(line(type="final", message="x"))

    assert [json.loads(raw)["type"] for raw, _ in lines_from(log.path, 0)] == ["hello", "final"]
    assert (log.path.stat().st_mode & 0o777) == 0o600


def test_the_offset_of_each_line_is_where_the_next_begins(tmp_path):
    log = EventLog(tmp_path / "events.ndjson")
    log.write(line(type="a"))
    log.write(line(type="b"))

    first, second = list(lines_from(log.path, 0))

    assert [raw for raw, _ in lines_from(log.path, first[1])] == [second[0]]


def test_a_half_written_line_is_not_handed_out(tmp_path):
    path = tmp_path / "events.ndjson"
    path.write_bytes(b'{"type": "a"}\n{"type": "b"')

    assert [raw for raw, _ in lines_from(path, 0)] == [b'{"type": "a"}\n']


def test_a_new_daemon_starts_a_new_segment_and_keeps_the_old_log(tmp_path):
    first = EventLog(tmp_path / "events.ndjson")
    first.write(line(type="hello"))

    second = EventLog(tmp_path / "events.ndjson")

    assert second.segment > first.segment
    assert (tmp_path / "events.ndjson.1").read_text() == line(type="hello")
    assert (tmp_path / "events.ndjson").read_text() == ""


def test_finished_turns_lose_their_live_only_events_but_the_running_turn_keeps_them(tmp_path):
    log = EventLog(tmp_path / "events.ndjson")
    for text in (
        line(type="hello"),
        line(type="stream_chunk", text="par"),
        line(type="final", message="one"),
        line(type="turn_end"),
        line(type="stream_chunk", text="tial"),
    ):
        log.write(text)

    assert [event["type"] for event in replayed(log.path)] == ["hello", "final", "turn_end", "stream_chunk"]


def test_only_dialogs_still_open_come_back(tmp_path):
    log = EventLog(tmp_path / "events.ndjson")
    log.write(line(type="input_request", request_id="answered"))
    log.write(line(type="input_request", request_id="open"))
    log.write(line(type="input_closed", request_id="gone", reason="timeout"))

    assert [event["request_id"] for event in replayed(log.path, {"open"})] == ["open"]


def test_the_replay_says_how_far_it_read_even_for_lines_it_skipped(tmp_path):
    log = EventLog(tmp_path / "events.ndjson")
    log.write(line(type="turn_end"))
    log.write(line(type="status", text="x"))

    ends = [end for _, end in fresh_replay(log.path, set())]

    assert ends[-1] == log.path.stat().st_size


def test_a_large_log_starts_over_at_a_turn_boundary_with_its_handshake(tmp_path):
    log = EventLog(tmp_path / "events.ndjson", limit=200)
    log.write(line(type="hello", v=1))
    log.write(line(type="manifest", m="x" * 150))
    log.write(line(type="session", name="s"))
    before = log.segment

    log.write(line(type="turn_end"))

    assert log.segment > before
    assert [event["type"] for event in replayed(log.path)] == ["hello", "manifest", "session"]


def test_a_log_somebody_is_reading_is_not_rotated_under_them(tmp_path):
    log = EventLog(tmp_path / "events.ndjson", limit=10)
    log.attach_reader()
    segment = log.segment
    log.write(line(type="hello"))

    log.write(line(type="turn_end"))

    assert log.segment == segment


def test_a_note_for_the_person_follows_the_manifest_of_a_restarted_daemon(tmp_path):
    note = line(type="message", markdown="restarted", source="notice")
    log = EventLog(tmp_path / "events.ndjson", after_manifest=note)

    log.write(line(type="hello"))
    log.write(line(type="manifest"))
    log.write(line(type="session"))

    assert [event["type"] for event in replayed(log.path)] == ["hello", "manifest", "message", "session"]
