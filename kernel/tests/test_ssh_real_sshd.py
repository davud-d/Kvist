# -*- coding: utf-8 -*-
"""The remote core through genuine ssh: a real sshd run by this user, a real key, a real host key, the real shim.

Only the model is fake. Skipped where there is no sshd or it cannot listen.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from kernel.ssh import access
from kernel.ssh.command import Target, call_op
from kernel.ssh.proxy import Proxy
from kernel.tests.ssh_server import LocalSshd, sshd_program
from kernel.tests.ssh_support import FakeCoreDaemon
from kernel.tests.test_ssh_proxy import Frontend

pytestmark = pytest.mark.skipif(
    os.name != "posix" or sshd_program() is None or not all(shutil.which(tool) for tool in ("ssh", "ssh-keygen", "ssh-keyscan")),
    reason="needs sshd and the ssh tools",
)


@pytest.fixture
def server(tmp_path, monkeypatch):
    config_dir = Path(tempfile.mkdtemp(prefix="kv", dir="/tmp")).resolve()  # short: the daemon's socket lives under it
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(config_dir))  # the test side finds the daemon where the "server" put it
    (tmp_path / "sshd").mkdir()
    try:
        sshd = LocalSshd(tmp_path / "sshd", tmp_path, config_dir)
    except (OSError, RuntimeError) as error:
        pytest.skip(f"sshd could not run here: {error}")
    wrapper = sshd.ssh_wrapper(tmp_path, tmp_path / ".ssh" / "known_hosts")
    monkeypatch.setenv("KVIST_SSH", str(wrapper))
    yield sshd
    sshd.stop()
    shutil.rmtree(config_dir, ignore_errors=True)


def target_for(server: LocalSshd, workspace: str = "/") -> Target:
    return Target(server.address, server.port, workspace, str(server.client_key))


def trust(server: LocalSshd) -> None:
    endpoint = access.Endpoint("127.0.0.1", server.port)
    access.trust_host_key(access.scan_host_key(endpoint))


def test_the_machines_host_key_is_found_and_shown_before_it_is_trusted(server):
    endpoint = access.resolve_endpoint(server.address, server.port)
    assert access.host_is_known(endpoint) is False

    scanned = access.scan_host_key(endpoint)

    expected = subprocess.run(["ssh-keygen", "-lf", f"{server.host_key}.pub"], capture_output=True, text=True).stdout.split()[1]
    assert scanned.fingerprint == expected
    access.trust_host_key(scanned)
    assert access.host_is_known(endpoint) is True


def test_a_key_the_server_knows_gets_in_and_another_does_not(server, tmp_path):
    trust(server)
    assert access.check_login(target_for(server)).ok is True

    stranger = tmp_path / "stranger"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(stranger)], check=True)
    refused = access.check_login(Target(server.address, server.port, "/", str(stranger)))

    assert refused.ok is False
    assert refused.problem == "auth"


def test_an_untrusted_host_is_refused_not_asked_about(server):
    refused = access.check_login(target_for(server))

    assert refused.ok is False
    assert refused.problem == "host_key"


def test_the_core_on_the_server_answers_a_probe_through_real_ssh(server):
    trust(server)

    code, answer, stderr = call_op(target_for(server, str(server.home)), "probe")

    assert code == 0, stderr
    assert answer["ok"] is True
    assert answer["protocol_version"] == 1
    assert answer["workspace_exists"] is True


def test_a_frontend_works_with_the_agent_through_real_ssh_and_the_agent_outlives_it(server, tmp_path):
    trust(server)
    daemon = FakeCoreDaemon(tmp_path)
    frontend = Frontend()
    proxy = Proxy(
        target_for(server, daemon.workspace), stdin=frontend.stdin, stdout=frontend, stderr=frontend.stderr,
        language="en", give_up_seconds=30, max_delay=0.2, prepare_machine=lambda *a, **k: True,
    )
    exit_code: list[int] = []
    thread = threading.Thread(target=lambda: exit_code.append(proxy.run()), daemon=True)
    thread.start()

    try:
        frontend.wait_for(lambda line: line["type"] == "control", timeout=30)
        frontend.say("over the real wire")
        final = frontend.wait_for(lambda line: line["type"] == "final", timeout=30)
        frontend.leave()
        thread.join(timeout=15)
    finally:
        daemon.close()

    assert final["message"] == "done over the real wire"
    assert exit_code == [0]
    assert not {"remote_hello", "remote_attached", "remote_offset", "remote_ping"} & set(frontend.types())


def test_a_login_shell_that_prints_a_banner_does_not_break_the_stream(server, tmp_path, monkeypatch):
    trust(server)
    daemon = FakeCoreDaemon(tmp_path)
    noisy = tmp_path / "noisy-ssh"
    real = os.environ["KVIST_SSH"]
    # What a server whose shell startup file prints does: text (even bytes that are not text) ahead of the core's own lines.
    noisy.write_text(f'#!/bin/sh\nprintf "Welcome to the server\\n\\211PNG junk {{not json\\n"\nexec "{real}" "$@"\n', encoding="utf-8")
    noisy.chmod(0o755)
    monkeypatch.setenv("KVIST_SSH", str(noisy))
    frontend = Frontend()
    proxy = Proxy(
        target_for(server, daemon.workspace), stdin=frontend.stdin, stdout=frontend, stderr=io.StringIO(),
        language="en", give_up_seconds=30, max_delay=0.2, prepare_machine=lambda *a, **k: True,
    )
    thread = threading.Thread(target=proxy.run, daemon=True)
    thread.start()

    try:
        frontend.wait_for(lambda line: line["type"] == "control", timeout=30)
        frontend.say("still fine")
        final = frontend.wait_for(lambda line: line["type"] == "final", timeout=30)
        frontend.leave()
        thread.join(timeout=15)
    finally:
        daemon.close()

    assert final["message"] == "done still fine"
