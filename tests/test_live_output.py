# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import time

import pytest

from event_stream_ui import EventStreamUI
from frontend_protocol import ActionOutputEvent, ActionResultEvent, FrontendChannel
from process_tree import run_supervised

preexec_unsupported_on_windows = pytest.mark.xfail(
    sys.platform == "win32",
    reason="preexec_fn not supported on windows, see executor",
)


def _run(script: str, sink, tmp_path):
    return run_supervised(
        [sys.executable, "-u", "-c", script],
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin"},
        timeout=20,
        max_capture_bytes=100_000,
        rlimit_setter=None,
        is_windows=False,
        use_cgroup=False,
        on_output=sink,
    )


@preexec_unsupported_on_windows
def test_output_is_delivered_while_the_command_runs(tmp_path):
    seen: list[tuple[float, str]] = []
    started = time.monotonic()
    result = _run(
        "import time,sys\nprint('first');time.sleep(0.6);print('second')",
        lambda text: seen.append((time.monotonic() - started, text)),
        tmp_path,
    )
    assert "".join(text for _, text in seen).replace("\r", "") == "first\nsecond\n"
    # The first line reached the observer well before the command ended.
    assert seen[0][0] < 0.5
    # Capture is unchanged by observing.
    assert result.stdout == b"first\nsecond\n"


@preexec_unsupported_on_windows
def test_stderr_is_observed_and_multibyte_text_survives_a_split(tmp_path):
    seen: list[str] = []
    _run(
        "import sys,time\nb='ж'.encode()\nsys.stdout.buffer.write(b[:1]);sys.stdout.flush()\n"
        "time.sleep(0.2)\nsys.stdout.buffer.write(b[1:]);sys.stdout.flush()\n"
        "print('err',file=sys.stderr)",
        seen.append,
        tmp_path,
    )
    joined = "".join(seen)
    assert "ж" in joined
    assert "err" in joined
    assert "�" not in joined


@preexec_unsupported_on_windows
def test_a_failing_observer_does_not_stop_the_command(tmp_path):
    calls: list[str] = []

    def broken(text: str) -> None:
        calls.append(text)
        raise RuntimeError("observer bug")

    result = _run("print('a');print('b')", broken, tmp_path)
    assert result.returncode == 0
    assert result.stdout == b"a\nb\n"
    assert len(calls) == 1


def _events(channel: FrontendChannel):
    return channel.drain()


def test_ui_emits_a_rolling_snapshot_per_step():
    channel = FrontendChannel()
    ui = EventStreamUI(channel)
    ui.output_interval = 0
    ui.print_action_output(3, "one\n")
    ui.print_action_output(3, "two\n")
    first, second = _events(channel)
    assert isinstance(first, ActionOutputEvent)
    assert (first.step, first.output) == (3, "one\n")
    assert second.output == "one\ntwo\n"


def test_ui_coalesces_bursts_but_never_loses_the_last_chunk():
    channel = FrontendChannel()
    ui = EventStreamUI(channel)
    ui.output_interval = 0.05
    ui.print_action_output(1, "a")
    ui.print_action_output(1, "b")
    ui.print_action_output(1, "c")
    time.sleep(0.2)
    outputs = [event.output for event in _events(channel)]
    assert outputs[0] == "a"
    assert outputs[-1] == "abc"
    assert len(outputs) <= 3


def test_ui_redacts_a_secret_split_across_chunks():
    channel = FrontendChannel()
    ui = EventStreamUI(channel)
    ui.output_interval = 0
    secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    ui.print_action_output(1, secret[:12])
    ui.print_action_output(1, secret[12:])
    assert secret not in _events(channel)[-1].output


def test_the_result_ends_the_live_output_and_a_new_step_starts_clean():
    channel = FrontendChannel()
    ui = EventStreamUI(channel)
    ui.output_interval = 0.05
    ui.print_action_output(1, "x")
    ui.print_action_output(1, "y")
    ui.print_action_result({"step": 1, "action_type": "run_shell", "ok": True, "summary": "ok"})
    time.sleep(0.2)
    events = _events(channel)
    assert isinstance(events[-1], ActionResultEvent)
    assert not any(isinstance(e, ActionOutputEvent) and e.step < 0 for e in events)
    ui.output_interval = 0
    ui.print_action_output(2, "fresh")
    (event,) = _events(channel)
    assert (event.step, event.output) == (2, "fresh")
