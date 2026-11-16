"""``/council`` through the real classic loop in a PTY: the line is a turn, and the prompt is back afterwards."""
from __future__ import annotations

from test_cli_pty import _spawn, pytestmark  # noqa: F401 - pytestmark: the same slow PTY markers


def test_cli_pty_council_runs_as_a_turn_and_the_repl_stays_alive(tmp_path):
    child = _spawn(tmp_path / "workspace", tmp_path / "home")
    try:
        child.wait_for("❯", timeout=20)

        # The offline fixture has no model, so no member can hand in a card: the council says so as its answer.
        start = child.mark()
        child.send("/council which one is simpler?\r")
        answer = child.wait_for("The council could not meet", start=start, timeout=60)
        assert "Traceback (most recent call last)" not in answer

        # Back at the prompt: an ordinary request is answered, and a bare command only explains itself.
        ready = child.mark()
        child.wait_for("❯ ", start=ready)
        start = child.mark()
        child.send("/council\r")
        child.wait_for("Usage: /council <question>", start=start)
        start = child.mark()
        child.send("say something plain\r")
        child.wait_for("OFFLINE-RESPONSE: say something plain", start=start)

        child.send("/bye\r")
        assert child.wait() == 0
        assert "Traceback (most recent call last)" not in child.visible()
    finally:
        child.close()
