# -*- coding: utf-8 -*-
"""The BSDs (FreeBSD, OpenBSD, NetBSD): what the core decides by platform, checked here by faking the platform.

Nothing in this file runs on a real BSD; the BSD job of platforms.yml does that. These tests keep the decisions
that differ from Linux (the child PATH, the system description, the process-tree fallback, the advice) from
regressing while only a Linux machine is at hand.
"""
from __future__ import annotations

import inspect
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from kernel.config import doctor
from kernel.context import system_context
from kernel.sandbox import process_tree, terminal_env

BSD_PLATFORMS = ["freebsd15", "openbsd8", "netbsd11"]


@pytest.mark.parametrize("platform_name", BSD_PLATFORMS)
def test_a_bsd_child_path_reaches_the_packages_directories_that_pass_the_trust_check(monkeypatch, tmp_path, platform_name):
    trusted, writable = tmp_path / "local-bin", tmp_path / "home-bin"
    trusted.mkdir()
    writable.mkdir()
    monkeypatch.setattr(sys, "platform", platform_name)
    monkeypatch.setattr(terminal_env, "_BSD_PACKAGE_DIRS", (str(trusted), str(writable), str(tmp_path / "absent")))
    monkeypatch.setattr(terminal_env, "_is_trusted_system_directory", lambda directory: directory == trusted)

    directories = terminal_env._system_exec_dirs()

    assert trusted in directories
    assert writable not in directories
    assert directories.index(trusted) > max(directories.index(Path(base)) for base in ("/usr/bin", "/bin")
                                              if Path(base) in directories)   # the base system comes first


def test_linux_keeps_its_child_path_without_usr_local(monkeypatch, tmp_path):
    other = tmp_path / "local-bin"
    other.mkdir()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(terminal_env, "_BSD_PACKAGE_DIRS", (str(other),))
    monkeypatch.setattr(terminal_env, "_is_trusted_system_directory", lambda directory: True)

    assert other not in terminal_env._system_exec_dirs()


@pytest.mark.parametrize(("system", "release"), [("FreeBSD", "15.1-RELEASE"), ("OpenBSD", "8.0"), ("NetBSD", "11.0")])
def test_the_system_is_described_by_its_own_name_and_the_shell_is_read(monkeypatch, system, release):
    monkeypatch.setattr(system_context.platform, "system", lambda: system)
    monkeypatch.setattr(system_context.platform, "release", lambda: release)
    monkeypatch.setenv("SHELL", "/usr/local/bin/bash")

    assert system_context.current_os_context() == f"{system} {release}"
    assert system_context.current_shell_context() == "bash"


def test_doctor_tells_a_system_without_bubblewrap_how_code_can_still_run(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "openbsd8")
    monkeypatch.setattr(os, "name", "posix")

    checks = doctor._check_environment(doctor._Say(False), tmp_path)

    sandbox = next(check for check in checks if check.name == "OS sandbox")
    assert sandbox.status == doctor.WARN
    assert "AGENT_SANDBOX_MODE=workspace" in sandbox.detail
    assert not any(check.name in ("bubblewrap", "cgroup v2") for check in checks)


# --- process trees without /proc ----------------------------------------------------------------

needs_ps = pytest.mark.skipif(shutil.which("ps") is None, reason="needs ps")


def test_a_ps_start_time_is_read_as_seconds():
    assert process_tree._started_at("Mon Nov  2 10:23:45 2026") == process_tree._started_at("Mon  Nov 2  10:23:45  2026")
    assert process_tree._started_at("Mon Nov  2 10:23:46 2026") - process_tree._started_at("Mon Nov  2 10:23:45 2026") == 1
    assert process_tree._started_at("garbage") is None


@pytest.fixture
def detached_tree():
    """A shell whose child sleeps in a process group of its own: what `setsid` leaves behind after a timeout."""
    shell = subprocess.Popen(["sh", "-c", "setsid sleep 60 & wait"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.4)
    yield shell
    shell.kill()
    shell.wait()


@needs_ps
def test_ps_finds_the_descendants_a_detached_process_leaves_and_their_start_times(detached_tree):
    found = process_tree._ps_descendant_snapshot(detached_tree.pid)

    assert found, "the sleeping descendant was not listed"
    for pid, started in found.items():
        assert process_tree._ps_process_start_time(pid) == started
    assert detached_tree.pid not in found


@needs_ps
def test_without_proc_the_tracker_records_descendants_and_kills_them_by_identity(monkeypatch, detached_tree):
    monkeypatch.setattr(sys, "platform", "freebsd15")      # the tracker picks ps, as it would on a BSD
    tracker = process_tree._DescendantTracker(detached_tree.pid)
    assert tracker._snapshot_of is process_tree._ps_descendant_snapshot

    tracker.start()
    time.sleep(0.3)
    recorded = tracker.stop_and_snapshot()
    assert recorded
    process_tree._kill_recorded_descendants(recorded)
    time.sleep(0.3)

    for pid in recorded:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)      # gone (the zombie of a child of init is reaped by its parent)


@needs_ps
def test_a_recycled_pid_is_not_killed(monkeypatch):
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        monkeypatch.setattr(sys, "platform", "netbsd11")
        real_start = process_tree._process_start_time(sleeper.pid)
        assert real_start is not None

        process_tree._kill_recorded_descendants({sleeper.pid: real_start - 1000})   # "another process" under this pid

        assert sleeper.poll() is None
    finally:
        sleeper.send_signal(signal.SIGKILL)
        sleeper.wait()


def test_the_cgroup_scope_looks_for_the_real_controllers_file():
    """The name was once misspelled, which made cgroup supervision unavailable on every Linux machine."""
    assert "cgroup.controllers" in inspect.getsource(process_tree._CgroupScope.create)
