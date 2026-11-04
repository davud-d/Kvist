# -*- coding: utf-8 -*-
"""The end-to-end runs of the remote core (real daemons, real interfaces' proxy, a stand-in ssh, a fake provider) as tests.

They were scripts that somebody had to remember to run, and two of them went stale unnoticed. They are slow (tens of seconds each),
so they run with ``-m ""`` and in CI's full run, not in the quick subset.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.slow, pytest.mark.skipif(os.name != "posix", reason="the remote core is POSIX-only")]


def run_script(name: str, *arguments: str) -> None:
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / name), *arguments], cwd=ROOT, capture_output=True, text=True, timeout=240
    )
    assert done.returncode == 0, f"{name} failed:\n{done.stdout[-3000:]}\n{done.stderr[-1500:]}"


def test_a_frontend_killed_mid_turn_the_next_one_takes_over_and_the_provider_stays_off_the_servers_disk():
    run_script("e2e_remote_smoke.py")


def test_two_sessions_work_in_one_folder_at_once_and_work_done_while_away_is_shown_not_hidden():
    run_script("e2e_remote_lanes.py")


def test_a_new_interface_meeting_a_daemon_of_an_older_commit_restarts_it_and_works():
    older = "c3602f4"
    if subprocess.run(["git", "cat-file", "-e", older], cwd=ROOT, capture_output=True).returncode:
        pytest.skip(f"commit {older} is not in this clone (a shallow checkout)")
    run_script("e2e_remote_skew.py", older)
