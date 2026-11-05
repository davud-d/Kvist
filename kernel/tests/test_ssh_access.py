# -*- coding: utf-8 -*-
"""Getting signed in the first time: the host key, a key file, and a password that is used once and forgotten."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from kernel.ssh import access
from kernel.ssh.command import Target
from kernel.ssh.paths import RemoteError

TARGET = Target("me@box", 2222, "/")


def done(stdout: str = "", stderr: str = "", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout, stderr)


class Programs:
    """A stand-in for subprocess.run that records what was run and answers from a rule per program."""

    def __init__(self, **rules) -> None:
        self.rules = rules
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **options):
        self.calls.append((list(command), options))
        program = Path(command[0]).name
        rule = self.rules.get(program)
        return rule(command, options) if callable(rule) else (rule or done())

    def ran(self, program: str) -> list[tuple[list[str], dict]]:
        return [call for call in self.calls if Path(call[0][0]).name == program]


def make_key_files(command, options):
    path = Path(command[command.index("-f") + 1])
    path.write_text("PRIVATE", encoding="utf-8")
    Path(f"{path}.pub").write_text("ssh-ed25519 AAAAPUBLIC kvist-work\n", encoding="utf-8")
    return done()


def test_a_name_from_ssh_config_is_followed_to_the_host_and_port_it_stands_for():
    run = Programs(ssh=done("user me\nhostname 10.1.2.3\nport 2200\nidentityfile ~/.ssh/id\n"))

    assert access.resolve_endpoint("work-alias", None, run) == access.Endpoint("10.1.2.3", 2200)


def test_an_address_ssh_cannot_describe_is_taken_as_written():
    run = Programs(ssh=done("", "fail", 255))

    assert access.resolve_endpoint("me@box.example", 2022, run) == access.Endpoint("box.example", 2022)
    assert access.resolve_endpoint("me@box.example", None, run) == access.Endpoint("box.example", 22)


def test_the_host_key_shown_is_the_best_one_the_machine_offers():
    lines = "[box]:22 ssh-rsa AAAArsa\n[box]:22 ssh-ed25519 AAAAed\n# comment\n"
    run = Programs(**{"ssh-keyscan": done(lines), "ssh-keygen": done("256 SHA256:abcd box (ED25519)\n")})

    scanned = access.scan_host_key(access.Endpoint("box", 22), run)

    assert scanned.kind == "ssh-ed25519" and scanned.fingerprint == "SHA256:abcd"
    assert run.ran("ssh-keygen")[0][1]["input"].split()[1] == "ssh-ed25519"
    assert len(scanned.lines) == 2


def test_a_machine_that_gives_no_key_is_reported_as_unreachable_not_trusted():
    with pytest.raises(RemoteError, match="did not answer"):
        access.scan_host_key(access.Endpoint("box", 22), Programs(**{"ssh-keyscan": done("")}))


def test_trusting_a_key_adds_it_where_ssh_looks_and_keeps_the_file_private(tmp_path):
    access.trust_host_key(access.ScannedKey(("box ssh-ed25519 AAAA", "box ssh-rsa BBBB"), "SHA256:x", "ssh-ed25519"))

    written = access.known_hosts()
    assert written.read_text().splitlines() == ["box ssh-ed25519 AAAA", "box ssh-rsa BBBB"]
    if os.name == "posix":  # NTFS has no such modes
        assert (written.stat().st_mode & 0o777) == 0o600


def test_a_failed_login_says_which_kind_of_failure_it_was():
    run = Programs(ssh=done("", "me@box: Permission denied (publickey).\n", 255))

    login = access.check_login(TARGET, run)

    assert (login.ok, login.problem) == (False, "auth")
    assert "Permission denied" in login.detail


def test_a_login_that_works_runs_nothing_but_true_on_the_machine():
    run = Programs()

    assert access.check_login(Target("me@box", None, "/", "/k"), run).ok is True
    command = run.calls[0][0]
    assert command[-1] == "true" and "BatchMode=yes" in command and command[command.index("-i") + 1] == "/k"


def test_a_password_makes_a_key_installs_it_and_is_seen_nowhere_but_the_environment_of_one_ssh():
    run = Programs(**{"ssh-keygen": make_key_files})

    key = access.install_key_with_password("work", TARGET, "s3cret-pass", run)

    assert key == str(access.key_path("work"))
    installs = [call for call in run.ran("ssh") if "PubkeyAuthentication=no" in call[0]]
    assert len(installs) == 1
    command, options = installs[0]
    assert options["input"] == "ssh-ed25519 AAAAPUBLIC kvist-work\n"
    assert options["env"]["KVIST_SSH_PASSWORD"] == "s3cret-pass"
    assert options["env"]["SSH_ASKPASS_REQUIRE"] == "force"
    assert all("s3cret-pass" not in part for call in run.calls for part in call[0])
    assert "StrictHostKeyChecking=yes" in command
    on_disk = "".join(path.read_text(errors="replace") for path in access.key_directory().iterdir() if path.is_file())
    assert "s3cret-pass" not in on_disk
    if os.name == "posix":  # NTFS has no such modes
        assert (access.key_directory().stat().st_mode & 0o777) == 0o700


def test_a_wrong_password_leaves_no_key_behind():
    run = Programs(**{"ssh-keygen": make_key_files, "ssh": done("", "Permission denied, please try again.\n", 255)})

    with pytest.raises(RemoteError, match="password"):
        access.install_key_with_password("work", TARGET, "wrong", run)

    assert not access.key_path("work").exists()
    assert not Path(f"{access.key_path('work')}.pub").exists()


def test_a_key_that_does_not_work_after_installing_is_not_kept():
    def ssh(command, options):
        return done() if "PubkeyAuthentication=no" in command else done("", "Permission denied (publickey).\n", 255)

    run = Programs(**{"ssh-keygen": make_key_files, "ssh": ssh})

    with pytest.raises(RemoteError, match="failed"):
        access.install_key_with_password("work", TARGET, "pw", run)

    assert not access.key_path("work").exists()


def test_a_key_that_was_there_before_is_reused_and_never_deleted_on_failure():
    access.key_directory().mkdir(parents=True)
    access.key_path("work").write_text("OLD")
    Path(f"{access.key_path('work')}.pub").write_text("ssh-ed25519 OLDPUB\n")
    run = Programs(ssh=done("", "Permission denied", 255))

    with pytest.raises(RemoteError):
        access.install_key_with_password("work", TARGET, "wrong", run)

    assert access.key_path("work").read_text() == "OLD"
    assert not run.ran("ssh-keygen")


@pytest.mark.skipif(os.name != "posix", reason="the helper is a shell script; on Windows a password goes through PuTTY")
def test_the_helper_that_hands_ssh_its_password_only_echoes_the_environment():
    helper = access._askpass_helper()

    if os.name == "posix":  # NTFS has no such modes

        assert (helper.stat().st_mode & 0o777) == 0o700
    answer = subprocess.run([str(helper)], env={"KVIST_SSH_PASSWORD": "pw with spaces"}, capture_output=True, text=True)
    assert answer.stdout == "pw with spaces\n"


def test_a_key_file_is_checked_before_it_is_used(tmp_path):
    good = tmp_path / "id"
    good.write_text("k")
    good.chmod(0o600)
    open_key = tmp_path / "open"
    open_key.write_text("k")
    open_key.chmod(0o644)

    assert access.usable_key_file(str(good)) is None
    assert "not a file" in access.usable_key_file(str(tmp_path / "missing"))
    if os.name == "posix":
        assert "chmod 600" in access.usable_key_file(str(open_key))
