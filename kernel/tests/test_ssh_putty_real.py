# -*- coding: utf-8 -*-
"""PuTTY itself, not a stand-in: the real ``puttygen`` everywhere PuTTY is installed, and the real ``plink`` against a real sshd where there is one.

Skipped when PuTTY is not installed; CI installs it on every system it can.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kernel.ssh import access
from kernel.ssh.command import Target, call_op
from kernel.tests import test_ssh_real_sshd as real
from kernel.tests.test_ssh_real_sshd import server, trust  # noqa: F401  (fixture and helper)

needs_sshd = real.pytestmark  # the same condition as the other real-sshd tests
needs_putty = pytest.mark.skipif(not (shutil.which("plink") and shutil.which("puttygen")), reason="needs PuTTY (plink and puttygen)")


@pytest.fixture
def plink(monkeypatch):
    monkeypatch.setenv("KVIST_SSH", shutil.which("plink"))


@pytest.fixture
def putty_server(server, plink, tmp_path):  # noqa: F811
    """The test sshd, reached with plink and a ``.ppk`` made from the key the server accepts."""
    ppk = tmp_path / "client.ppk"
    subprocess.run(["puttygen", str(server.client_key), "-o", str(ppk)], check=True, capture_output=True)
    return server, str(ppk)


@pytest.mark.skipif(sys.platform == "win32", reason="puttygen.exe is a window")
@needs_putty
def test_puttygen_makes_a_key_with_no_passphrase_and_the_public_half_for_authorized_keys(plink):
    key = access._make_key("work", access._run)

    assert key.name == "work.ppk" and key.read_text(encoding="utf-8").startswith("PuTTY-User-Key-File")
    assert Path(f"{key}.pub").read_text(encoding="utf-8").startswith("ssh-ed25519 ")
    assert not list(key.parent.glob("*.nopass"))


@needs_sshd
@needs_putty
def test_the_host_key_plink_shows_is_the_one_the_server_has(putty_server):
    server, _ = putty_server
    endpoint = access.Endpoint("127.0.0.1", server.port)
    assert access.host_is_known(endpoint) is False

    scanned = access.scan_host_key(endpoint)

    expected = subprocess.run(["ssh-keygen", "-lf", f"{server.host_key}.pub"], capture_output=True, text=True).stdout.split()[1]
    assert scanned.fingerprint == expected
    access.trust_host_key(scanned)
    assert access.host_is_known(endpoint) is True


@needs_sshd
@needs_putty
def test_plink_gets_in_with_the_key_the_server_knows_and_not_with_another_or_an_unconfirmed_host(putty_server, tmp_path):
    server, ppk = putty_server
    target = Target(server.address, server.port, "/", ppk)
    untrusted = access.check_login(target)
    assert (untrusted.ok, untrusted.problem) == (False, "host_key"), f"plink without a confirmed host key: {untrusted}"

    access.trust_host_key(access.scan_host_key(access.Endpoint("127.0.0.1", server.port)))
    assert access.check_login(target).ok is True

    no_passphrase = tmp_path / "none"
    no_passphrase.write_text("", encoding="utf-8")
    stranger = tmp_path / "stranger.ppk"
    subprocess.run(["puttygen", "-t", "ed25519", "-o", str(stranger), "--new-passphrase", str(no_passphrase)], check=True, capture_output=True)
    assert access.check_login(Target(server.address, server.port, "/", str(stranger))).problem == "auth"


@needs_sshd
@needs_putty
def test_the_core_on_the_server_answers_a_probe_through_plink(putty_server):
    server, ppk = putty_server
    trust(server)

    code, answer, stderr = call_op(Target(server.address, server.port, str(server.home), ppk), "probe")

    assert code == 0, stderr
    assert answer["ok"] is True and answer["workspace_exists"] is True
