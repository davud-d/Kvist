# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import io

import pytest

import terminal_image
from terminal_image import (
    EXIT_LOGO_PATH,
    image_protocol,
    iterm_image_sequence,
    kitty_image_sequence,
    print_exit_logo,
)


class _TtyStream(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"TERM": "xterm-kitty"}, "kitty"),
        ({"TERM": "xterm-256color", "KITTY_WINDOW_ID": "1"}, "kitty"),
        ({"TERM": "xterm-ghostty", "TERM_PROGRAM": "ghostty"}, "kitty"),
        ({"TERM": "xterm-256color", "KONSOLE_VERSION": "250401"}, "kitty"),
        ({"TERM_PROGRAM": "iTerm.app"}, "iterm"),
        ({"TERM_PROGRAM": "WezTerm"}, "iterm"),
        ({"TERM": "xterm-256color"}, None),
        ({"TERM": "alacritty"}, None),
        ({"TERM": "xterm-kitty", "TMUX": "/tmp/tmux-1000/default,1,0"}, None),
        ({"TERM": "screen-256color", "TERM_PROGRAM": "iTerm.app"}, None),
    ],
)
def test_image_protocol_detection(env, expected):
    assert image_protocol(env) == expected


def test_kitty_sequence_chunks_and_reassembles_png():
    png = bytes(range(256)) * 40
    sequence = kitty_image_sequence(png, rows=4)
    commands = [part for part in sequence.split("\x1b\\") if part]
    assert len(commands) > 1
    assert commands[0].startswith("\x1b_Gf=100,a=T,r=4,q=2,m=1;")
    assert commands[-1].startswith("\x1b_Gm=0;")
    payload = "".join(command.split(";", 1)[1] for command in commands)
    assert base64.standard_b64decode(payload) == png


def test_iterm_sequence_carries_png():
    png = b"\x89PNG fake"
    sequence = iterm_image_sequence(png, rows=4)
    assert sequence.startswith("\x1b]1337;File=inline=1;")
    assert "height=4" in sequence
    assert sequence.endswith(base64.standard_b64encode(png).decode() + "\x07")


def test_exit_logo_asset_is_a_png():
    assert EXIT_LOGO_PATH.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_print_exit_logo_writes_on_capable_tty():
    stream = _TtyStream()
    assert print_exit_logo(stream, {"TERM": "xterm-kitty"}) is True
    assert stream.getvalue().startswith("\x1b_G")
    assert stream.getvalue().endswith("\n")


def test_print_exit_logo_skips_plain_terminal_and_pipes():
    tty = _TtyStream()
    assert print_exit_logo(tty, {"TERM": "xterm-256color"}) is False
    assert tty.getvalue() == ""
    pipe = io.StringIO()
    assert print_exit_logo(pipe, {"TERM": "xterm-kitty"}) is False
    assert pipe.getvalue() == ""


def test_print_exit_logo_skips_missing_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal_image, "EXIT_LOGO_PATH", tmp_path / "absent.png")
    stream = _TtyStream()
    assert print_exit_logo(stream, {"TERM": "xterm-kitty"}) is False
    assert stream.getvalue() == ""
