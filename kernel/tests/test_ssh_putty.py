# -*- coding: utf-8 -*-
"""PuTTY's plink as the ssh client: its command lines, the host keys Kvist keeps for it, and a password that never reaches argv."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from kernel.ssh import access, putty
from kernel.ssh.command import Target, classify, ssh_prefix
from kernel.ssh.paths import RemoteError

FINGERPRINT = "SHA256:" + "A" * 43
PLINK_PROMPT = f"""The host key is not cached for this server:
  box (port 22)
The server's ssh-ed25519 key fingerprint is:
  ssh-ed25519 255 {FINGERPRINT}
Cannot confirm a host key in batch mode
"""


@pytest.fixture
def plink(monkeypatch):
    monkeypatch.setenv("KVIST_SSH", "plink")


def done(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_the_client_is_what_kvist_ssh_names_else_openssh_and_plink_only_on_windows_without_ssh(monkeypatch):
    monkeypatch.delenv("KVIST_SSH", raising=False)
    installed: set[str] = {"ssh", "plink"}
    monkeypatch.setattr(putty.shutil, "which", lambda name: f"C:/{name}.exe" if name in installed else None)

    monkeypatch.setattr(sys, "platform", "linux")
    assert not putty.uses_putty()
    monkeypatch.setattr(sys, "platform", "win32")
    assert not putty.uses_putty() and putty.ssh_program() == "ssh"  # Windows has OpenSSH; PuTTY being installed changes nothing

    installed.discard("ssh")
    assert putty.uses_putty() and putty.ssh_program() == "plink"

    installed.discard("plink")
    assert not putty.uses_putty() and putty.ssh_program() == "ssh"

    monkeypatch.setenv("KVIST_SSH", r"C:\Program Files\PuTTY\plink.exe")
    assert putty.uses_putty()
    monkeypatch.setenv("KVIST_SSH", "/usr/bin/ssh")
    assert not putty.uses_putty()


def test_plink_runs_without_a_terminal_or_questions_and_with_only_the_chosen_key(plink):
    assert ssh_prefix(Target("me@box", None, "/w")) == ["plink", "-batch", "-ssh", "-T", "-hostkey", putty.UNCONFIRMED, "me@box"]
    assert ssh_prefix(Target("me@box", 2222, "/w", "C:/k.ppk")) == [
        "plink", "-batch", "-ssh", "-T", "-P", "2222", "-i", "C:/k.ppk", "-noagent", "-hostkey", putty.UNCONFIRMED, "me@box",
    ]


def test_a_host_nobody_confirmed_is_not_reached_at_all(plink):
    def run(command, **options):
        pytest.fail("plink must not be started for a host nobody confirmed")

    login = access.check_login(Target("me@box", None, "/w"), run)

    assert (login.ok, login.problem) == (False, "host_key")


def test_the_host_key_the_person_confirmed_is_handed_to_plink_every_time(plink):
    assert putty.known_host_key("me@box", 2222) is None

    putty.remember_host_key("me@box", 2222, FINGERPRINT)

    assert putty.known_host_key("other@box", 2222) == FINGERPRINT  # the login does not matter, the machine does
    assert putty.known_host_key("me@box", None) is None  # another port is another machine
    assert ssh_prefix(Target("me@box", 2222, "/w"))[-3:-1] == ["-hostkey", FINGERPRINT]


def test_the_fingerprint_is_read_out_of_what_plink_says_about_an_unknown_host(plink):
    assert putty.fingerprint_in(PLINK_PROMPT) == ("ssh-ed25519", FINGERPRINT)
    assert putty.fingerprint_in("Network error: Connection refused") is None


def test_plinks_failures_are_told_apart_like_opensshs():
    assert classify(1, PLINK_PROMPT, shim_state=None).kind == "host_key"
    assert classify(1, "FATAL ERROR: Host key did not match", shim_state=None).kind == "host_key"
    assert classify(1, "Unable to authenticate", shim_state=None).kind == "auth"
    assert classify(1, "Disconnected: No supported authentication methods available (server sent: publickey)", shim_state=None).kind == "auth"
    assert classify(1, "Network error: Connection timed out", shim_state=None).kind == "unreachable"


def test_a_host_is_found_by_asking_plink_for_its_key_and_trusted_by_keeping_the_fingerprint(plink):
    calls = []

    def run(command, **options):
        calls.append(command)
        return done(1, stderr=PLINK_PROMPT)

    endpoint = access.resolve_endpoint("me@box", 2222, run)
    assert endpoint == access.Endpoint("box", 2222) and not calls  # no ssh -G, no ~/.ssh/config
    assert not access.host_is_known(endpoint)

    scanned = access.scan_host_key(endpoint, run)
    assert (scanned.fingerprint, scanned.kind) == (FINGERPRINT, "ssh-ed25519")
    assert calls == [["plink", "-batch", "-v", "-ssh", "-P", "2222", "box", "exit"]]

    access.trust_host_key(scanned)
    assert access.host_is_known(endpoint)


def test_a_host_that_shows_no_key_is_said_so(plink):
    with pytest.raises(RemoteError, match="did not show a host key"):
        access.scan_host_key(access.Endpoint("box", 22), lambda command, **options: done(1, stderr="Network error: Connection refused"))


def test_the_password_reaches_plink_in_a_file_that_is_gone_afterwards_and_never_in_argv(plink):
    seen = {}

    def run(command, **options):
        seen["command"] = command
        seen["input"] = options.get("input")
        pwfile = Path(command[command.index("-pwfile") + 1])
        seen["pwfile"] = pwfile
        seen["content"] = pwfile.read_text(encoding="utf-8")
        return done()

    key = access.key_path("work.ppk")
    key.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{key}.pub").write_text("ssh-ed25519 AAAA kvist-work\n", encoding="utf-8")

    access._copy_public_key(key, Target("me@box", 22, "/"), "s3cret", run)

    assert "s3cret" not in " ".join(seen["command"])
    assert seen["content"] == "s3cret" and not seen["pwfile"].exists()
    assert seen["input"] == "ssh-ed25519 AAAA kvist-work\n"
    assert seen["command"][:4] == ["plink", "-batch", "-ssh", "-T"] and seen["command"][-2] == "me@box"
    assert "-hostkey" in seen["command"]
    assert "authorized_keys" in seen["command"][-1]


def test_a_refused_password_says_so_and_still_leaves_no_file(plink):
    key = access.key_path("work.ppk")
    key.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{key}.pub").write_text("k\n", encoding="utf-8")

    with pytest.raises(RemoteError, match="Could not sign in with that password: Access denied"):
        access._copy_public_key(key, Target("me@box", None, "/"), "wrong", lambda command, **options: done(1, stderr="Access denied"))

    assert not [entry for entry in key.parent.iterdir() if entry.name.startswith("pw-")]


def test_a_putty_key_is_made_without_a_passphrase_and_its_openssh_half_is_put_beside_it(plink, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")  # on Windows no key is made, see the test below
    calls = []

    def run(command, **options):
        calls.append(command)
        if "-o" in command and command[0] == "puttygen" and "-O" not in command:
            Path(command[command.index("-o") + 1]).write_text("PuTTY-User-Key-File-3", encoding="utf-8")
            assert Path(command[command.index("--new-passphrase") + 1]).read_text(encoding="utf-8") == ""
        return done()

    key = access._make_key("work", run)

    assert key.name == "work.ppk" and key.exists()
    assert calls[0][:4] == ["puttygen", "-t", "ed25519", "-C"]
    assert calls[1][1:] == [str(key), "-O", "public-openssh", "-o", f"{key}.pub"]
    assert not list(key.parent.glob("*.nopass"))


def test_on_windows_no_key_is_made_because_puttygen_there_is_a_window(plink, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    with pytest.raises(RemoteError, match="make a .ppk key in it"):
        access._make_key("work", lambda command, **options: pytest.fail("puttygen must not be started"))

    assert not list(access.key_directory().glob("work*"))


def test_a_failing_puttygen_is_reported_and_leaves_no_half_made_key(plink, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RemoteError, match="puttygen failed: no entropy"):
        access._make_key("work", lambda command, **options: done(1, stderr="no entropy"))

    assert not list(access.key_directory().glob("work*"))


def test_the_tools_that_must_be_installed_are_plinks_when_plink_is_the_client(plink, monkeypatch):
    monkeypatch.setattr(access.shutil, "which", lambda name: None)

    assert access.tools_missing() == ["plink", "puttygen"]
