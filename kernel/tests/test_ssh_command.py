# -*- coding: utf-8 -*-
"""The ssh command line, what its failures mean, and the lease that decides who writes."""
from __future__ import annotations

import plistlib
import shlex

import pytest

from kernel.ssh import service
from kernel.ssh.command import Target, classify, forward_args, parse_target, remote_command, ssh_argv
from kernel.ssh.lease import Lease
from kernel.ssh.paths import RemoteError


@pytest.mark.parametrize(
    ("spec", "host", "port"),
    [
        ("me@box", "me@box", None),
        ("me@box:2222", "me@box", 2222),
        ("box.example.org", "box.example.org", None),
        ("me@[::1]:2222", "me@::1", 2222),
        ("me@fe80::1", "me@fe80::1", None),
    ],
)
def test_a_host_is_read_with_its_optional_port(spec, host, port):
    target = parse_target(spec, "/srv/app")

    assert (target.host, target.port, target.workspace) == (host, port, "/srv/app")


@pytest.mark.parametrize("spec", ["-oProxyCommand=evil", "", "me@box; rm -rf", "me@box:port", "a b"])
def test_what_ssh_could_take_for_an_option_or_a_command_is_refused(spec):
    with pytest.raises(RemoteError):
        parse_target(spec, "/srv/app")


def test_a_folder_on_the_server_is_required():
    with pytest.raises(RemoteError):
        parse_target("me@box", " ")


def test_the_remote_command_survives_a_login_shell_that_is_not_posix():
    command = remote_command(["--remote-attach", "--workspace", "/srv/my app; echo hi"])

    outer = shlex.split(command)
    assert outer[:2] == ["sh", "-c"]
    assert "'/srv/my app; echo hi'" in outer[2]
    assert "$HOME/.local/share/kvist/kvist-core" in outer[2]
    # The copy Kvist put there wins over whatever else is in PATH.
    assert outer[2].index("$core") < outer[2].index("command -v kvist-core")


def test_ssh_never_asks_a_question_and_stops_option_parsing_before_the_host():
    argv = ssh_argv(Target("me@box", 2222, "/srv/app"), ["--remote-attach"])

    assert "BatchMode=yes" in argv
    assert argv[argv.index("-p") + 1] == "2222"
    assert argv[argv.index("--") + 1] == "me@box"


def test_the_stand_in_ssh_program_can_be_chosen(monkeypatch):
    monkeypatch.setenv("KVIST_SSH", "/opt/ssh-stub")

    assert ssh_argv(Target("h", None, "/w"), [])[0] == "/opt/ssh-stub"


def test_arguments_for_the_server_lose_the_remote_flags_and_get_its_own_folder():
    argv = ["--remote", "me@box", "--remote-workspace", "/srv/app", "--workspace", "/local/stand-in",
            "--session-op", "list", "--role=coder"]

    assert forward_args(argv, "/srv/app") == ["--session-op", "list", "--role=coder", "--workspace", "/srv/app"]


def test_the_equals_form_of_the_flags_is_dropped_too():
    assert forward_args(["--workspace=/x", "--remote=h", "--remote-workspace=/y", "--memory-op", "list"], "/z") == [
        "--memory-op", "list", "--workspace", "/z",
    ]


@pytest.mark.parametrize(
    ("code", "stderr", "shim", "kind", "final"),
    [
        (255, "Host key verification failed.", None, "host_key", True),
        (255, "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!", None, "host_key", True),
        (255, "me@box: Permission denied (publickey).", None, "auth", True),
        (127, "sh: 1: exec: /root/.local/share/kvist/kvist-core: not found", None, "not_found", True),
        (2, "main.py: error: unrecognized arguments: --remote-op probe", None, "old_kvist", True),
        (2, "main.py: error: unrecognized arguments: --remote-attach --workspace /x", None, "old_kvist", True),
        (3, "", None, "no_service", True),
        (0, "", "no_service", "no_service", True),
        # A server that is rebooting refuses connections for a while: waiting is the right answer.
        (255, "ssh: connect to host box port 22: Connection refused", None, "unreachable", False),
        (255, "ssh: Could not resolve hostname box", None, "unreachable", False),
        (255, "Connection timed out during banner exchange", None, "unreachable", False),
        (0, "", None, "unreachable", False),
    ],
)
def test_what_a_finished_attempt_means(code, stderr, shim, kind, final):
    failure = classify(code, stderr, shim_state=shim)

    assert (failure.kind, failure.definitive) == (kind, final)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_the_first_frontend_drives_and_a_later_one_watches():
    lease = Lease(20, Clock())

    assert lease.claim_if_free("a") is True
    assert lease.claim_if_free("b") is False
    assert (lease.is_driver("a"), lease.is_driver("b")) == (True, False)


def test_a_driver_that_stops_pinging_loses_the_lease_to_whoever_asks_next():
    clock = Clock()
    lease = Lease(20, clock)
    lease.claim_if_free("a")
    clock.now = 21

    assert lease.is_driver("a") is False
    assert lease.claim_if_free("b") is True


def test_a_ping_extends_the_lease():
    clock = Clock()
    lease = Lease(20, clock)
    lease.claim_if_free("a")
    clock.now = 15
    lease.touch("a")
    clock.now = 30

    assert lease.is_driver("a") is True


def test_an_observer_cannot_keep_the_driver_alive_by_pinging():
    clock = Clock()
    lease = Lease(20, clock)
    lease.claim_if_free("a")
    clock.now = 15
    lease.touch("b")
    clock.now = 30

    assert lease.is_driver("a") is False


def test_taking_over_names_who_lost_it_and_reaping_names_who_went_silent():
    clock = Clock()
    lease = Lease(20, clock)
    lease.claim_if_free("a")

    assert lease.take("b") == "a"
    assert lease.take("b") is None
    clock.now = 25
    assert lease.reap() == "b"
    assert lease.reap() is None


def test_releasing_is_only_for_the_driver():
    lease = Lease(20, Clock())
    lease.claim_if_free("a")

    assert lease.release("b") is False
    assert lease.release("a") is True
    assert lease.claim_if_free("b") is True


def test_a_systemd_unit_quotes_what_systemd_would_expand():
    unit = service.render_systemd_unit(["/opt/kv ist/core", "--workspace", "/srv/100%/$HOME"], "/srv/x", {"KVIST_CONFIG_DIR": "/c"})

    assert 'ExecStart="/opt/kv ist/core" "--workspace" "/srv/100%%/$$HOME"' in unit
    assert "Restart=on-failure" in unit
    assert "RestartPreventExitStatus=2" in unit
    assert 'Environment="KVIST_CONFIG_DIR=/c"' in unit
    assert "WantedBy=default.target" in unit


def test_a_launch_agent_restarts_after_a_crash_but_not_after_a_clean_stop():
    document = plistlib.loads(service.render_launchd_plist("site.kvist.remote.x", ["/bin/kv", "--remote-serve"], {}))

    assert document["ProgramArguments"] == ["/bin/kv", "--remote-serve"]
    assert document["RunAtLoad"] is True
    assert document["KeepAlive"] == {"SuccessfulExit": False}


def test_the_cron_line_is_recognised_by_its_marker_and_quotes_its_arguments(tmp_path):
    workspace = str(tmp_path / "my project")

    line = service.render_cron_line(["/bin/kv", "--workspace", workspace], workspace)

    assert line.startswith("@reboot ")
    assert line.endswith(service.cron_marker(workspace))
    assert shlex.quote(workspace) in line


def test_the_daemon_command_is_this_program_with_the_resolved_folder(tmp_path):
    argv = service.daemon_argv(str(tmp_path))

    assert argv[-3:] == ["--remote-serve", "--workspace", str(tmp_path.resolve())]
