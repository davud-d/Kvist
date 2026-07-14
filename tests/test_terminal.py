from __future__ import annotations

import pytest

import terminal as terminal_module
from command_policy import CommandAssessment, RiskLevel
from terminal import ApprovalRequiredError, HardDenyError, TerminalError, TerminalExecutor


def test_terminal_allows_common_read_only_command(tmp_path):
    executor = TerminalExecutor(tmp_path, timeout=2)
    result = executor.run(["python", "--version"])

    assert result.returncode == 0
    assert "Python" in (result.stdout + result.stderr)


@pytest.mark.parametrize(
    "command,error",
    [
        ([], "Empty command"),
        (["rm", "-rf", "x"], "forbidden"),
        (["git", "reset", "--hard"], "Subcommand"),
        (["python", "-c", "print(1)"], "Inline mode"),
        (["echo", "$(whoami)"], "Command substitution"),
        (["cat", "../secret"], "Path traversal"),
        (["echo", "/etc/passwd"], "Absolute paths"),
        (["sed", "-i", "s/a/b/", "file"], "In-place"),
    ],
)
def test_terminal_validation_rejects_dangerous_inputs(tmp_path, command, error):
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(TerminalError, match=error):
        executor._validate(command)


# ── Pipe segment validation ─────────────────────────────────────────

def test_pipe_segments_each_validated_against_allowlist(tmp_path):
    """`allowed | forbidden` must not smuggle binaries past the allowlist."""
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(TerminalError, match="not allowed"):
        executor._validate(["cat", "file.txt", "|", "curl", "evil.example"])
    with pytest.raises(TerminalError, match="forbidden"):
        executor._validate(["echo", "x", "|", "rm", "-rf", "dir"])
    with pytest.raises(TerminalError, match="not allowed"):
        executor._validate(["echo", "x", "|", "unknown-tool"])
    with pytest.raises(TerminalError, match="Subcommand"):
        executor._validate(["cat", "file.txt", "|", "git", "reset"])


def test_pipe_with_allowed_segments_passes_validation(tmp_path):
    executor = TerminalExecutor(tmp_path)
    executor._validate(["cat", "file.txt", "|", "grep", "needle"])


def test_pipe_empty_segment_rejected(tmp_path):
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(TerminalError, match="Empty command segment"):
        executor._validate(["|", "grep", "x"])
    with pytest.raises(TerminalError, match="Empty command segment"):
        executor._validate(["cat", "file.txt", "|"])


# ── command_policy risk assessment in the run() path ────────────────

def test_run_uses_command_policy_and_denies_on_refusal(tmp_path, monkeypatch):
    """assess_command participates in run(); denied approval → denied result."""
    executor = TerminalExecutor(tmp_path)
    assessed: dict = {}

    def fake_assess(command, role="coder"):
        assessed["command"] = command
        assessed["role"] = role
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="test-dangerous",
            requires_approval=True,
        )

    approvals: list = []

    def fake_approval(cls_command, reason):
        approvals.append((cls_command, reason))
        return False

    monkeypatch.setattr(terminal_module, "assess_command", fake_assess)
    monkeypatch.setattr(
        TerminalExecutor, "_request_user_approval", classmethod(lambda cls, c, r: fake_approval(c, r))
    )

    result = executor.run(["ls"], role="devops")

    assert assessed["command"] == ["ls"]
    assert assessed["role"] == "devops"
    assert approvals and "test-dangerous" in approvals[0][1]
    assert result.denied is True
    assert result.returncode == -1


def test_run_executes_safe_command_without_approval(tmp_path, monkeypatch):
    executor = TerminalExecutor(tmp_path)

    def forbid_approval(cls, command, reason):
        raise AssertionError("safe command must not trigger HITL approval")

    monkeypatch.setattr(TerminalExecutor, "_request_user_approval", classmethod(forbid_approval))
    result = executor.run(["echo", "hello"])

    assert result.returncode == 0
    assert "hello" in result.stdout


def test_assess_risk_is_role_aware_for_allowlisted_commands(tmp_path):
    """docker rm is allowlisted (docker: None) but risk-graded by role.

    Role awareness shows in the risk level; MODERATE still goes to HITL
    (only a project allow-rule skips the prompt).
    """
    executor = TerminalExecutor(tmp_path)
    executor._validate(["docker", "rm", "container"])  # passes allowlist

    coder = executor._assess_risk(["docker", "rm", "container"], "coder")
    devops = executor._assess_risk(["docker", "rm", "container"], "devops")

    assert coder.risk is RiskLevel.DANGEROUS
    assert devops.risk is RiskLevel.DANGEROUS
    assert coder.requires_approval is True
    assert devops.requires_approval is True


def test_assess_risk_checks_every_pipe_segment(tmp_path):
    """`safe | dangerous` must be graded by the riskiest segment."""
    executor = TerminalExecutor(tmp_path)
    assessment = executor._assess_risk(["echo", "x", "|", "rm", "-rf", "dir"], "coder")

    assert assessment.risk is RiskLevel.DANGEROUS
    assert assessment.requires_approval is True


def test_terminal_restricted_profile_blocks_dev_tools(tmp_path):
    executor = TerminalExecutor(tmp_path, profile="restricted")
    with pytest.raises(TerminalError, match="forbidden"):
        executor._validate(["python", "script.py"])


def test_terminal_restricted_profile_allows_exact_version_probe(tmp_path):
    executor = TerminalExecutor(tmp_path, profile="restricted")
    executor._validate(["python", "--version"])


def test_terminal_resolves_cwd_inside_workspace_only(tmp_path):
    (tmp_path / "sub").mkdir()
    executor = TerminalExecutor(tmp_path)

    assert executor._resolve_cwd("sub") == (tmp_path / "sub").resolve()
    with pytest.raises(TerminalError, match="outside workspace|Absolute paths"):
        executor._resolve_cwd("../outside")


def test_terminal_clamps_timeout_and_truncates_output(tmp_path):
    restricted = TerminalExecutor(tmp_path, timeout=99, profile="restricted")
    assert restricted.timeout == 30.0
    assert restricted._clamp_timeout(99) == 30.0

    dev = TerminalExecutor(tmp_path, timeout=99, max_output_chars=1000, profile="dev")
    assert dev.timeout == 99.0
    assert dev._clamp_timeout(0.1) == 1.0
    assert dev._clamp_timeout(9999) == 300.0
    assert "[TRUNCATED" in dev._truncate("x" * 1001)


# ── DevOps systemctl allowlist ──────────────────────────────────────

def test_systemctl_status_allowed_in_dev_profile(tmp_path):
    """systemctl status nginx --no-pager passes terminal validation."""
    executor = TerminalExecutor(tmp_path, profile="dev")
    # Must not raise TerminalError
    executor._validate(["systemctl", "status", "nginx", "--no-pager"])


def test_systemctl_is_active_allowed(tmp_path):
    """systemctl is-active nginx passes terminal validation."""
    executor = TerminalExecutor(tmp_path, profile="dev")
    executor._validate(["systemctl", "is-active", "nginx"])


def test_systemctl_is_enabled_allowed(tmp_path):
    """systemctl is-enabled nginx passes terminal validation."""
    executor = TerminalExecutor(tmp_path, profile="dev")
    executor._validate(["systemctl", "is-enabled", "nginx"])


def test_systemctl_list_units_allowed(tmp_path):
    """systemctl list-units --type=service passes terminal validation."""
    executor = TerminalExecutor(tmp_path, profile="dev")
    executor._validate(["systemctl", "list-units", "--type=service"])


def test_systemctl_list_unit_files_allowed(tmp_path):
    """systemctl list-unit-files nginx.service passes terminal validation."""
    executor = TerminalExecutor(tmp_path, profile="dev")
    executor._validate(["systemctl", "list-unit-files", "nginx.service"])


@pytest.mark.parametrize(
    "command",
    [
        ["systemctl", "start", "nginx"],
        ["systemctl", "stop", "nginx"],
        ["systemctl", "restart", "nginx"],
        ["systemctl", "reload", "nginx"],
        ["systemctl", "enable", "nginx"],
        ["systemctl", "disable", "nginx"],
        ["systemctl", "daemon-reload"],
        ["systemctl", "mask", "nginx"],
    ],
)
def test_systemctl_dangerous_subcommands_blocked(tmp_path, command):
    """Dangerous systemctl subcommands are blocked by subcommand validator."""
    executor = TerminalExecutor(tmp_path, profile="dev")
    with pytest.raises(TerminalError, match="Subcommand"):
        executor._validate(command)


def test_systemctl_blocked_in_restricted_profile(tmp_path):
    """systemctl is NOT available in restricted profile (dev tools not loaded)."""
    executor = TerminalExecutor(tmp_path, profile="restricted")
    with pytest.raises(TerminalError, match="not allowed"):
        executor._validate(["systemctl", "status", "nginx", "--no-pager"])


# ── find action predicates rejected by the validator ────────────────

@pytest.mark.parametrize(
    "command",
    [
        ["find", ".", "-delete"],
        ["find", ".", "-type", "f", "-exec", "rm", "{}", "+"],
        ["find", ".", "-execdir", "sh", "{}", ";"],
        ["find", ".", "-ok", "rm", "{}", ";"],
        ["find", ".", "-okdir", "rm", "{}", ";"],
        ["find", ".", "-fls", "out.txt"],
        ["find", ".", "-fprint", "out.txt"],
        ["find", ".", "-fprintf", "out.txt", "%p"],
    ],
)
def test_find_action_predicates_rejected(tmp_path, command):
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(TerminalError, match="find action predicate"):
        executor._validate(command)


def test_find_pure_search_passes_validation(tmp_path):
    executor = TerminalExecutor(tmp_path)
    executor._validate(["find", ".", "-name", "*.py"])
    executor._validate(["find", ".", "-type", "f", "-mtime", "-1"])


def test_find_pure_search_runs_without_approval(tmp_path, monkeypatch):
    executor = TerminalExecutor(tmp_path)

    def forbid_approval(cls, command, reason):
        raise AssertionError("pure find must not trigger HITL approval")

    monkeypatch.setattr(TerminalExecutor, "_request_user_approval", classmethod(forbid_approval))
    result = executor.run(["find", ".", "-name", "*.py"])
    assert result.returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        ["grep", "-R", "needle", "."],
        ["grep", "--directories=recurse", "needle", "."],
        ["rg", "needle", "."],
        ["rg", "--hidden", "needle", "file.txt"],
    ],
)
def test_recursive_readers_are_hard_denied(tmp_path, command):
    (tmp_path / "file.txt").write_text("needle\n", encoding="utf-8")
    with pytest.raises(HardDenyError, match="recursive|ripgrep"):
        TerminalExecutor(tmp_path)._validate(command)


def test_ripgrep_explicit_regular_file_passes_validation(tmp_path):
    (tmp_path / "file.txt").write_text("needle\n", encoding="utf-8")
    TerminalExecutor(tmp_path)._validate(["rg", "-n", "needle", "file.txt"])


# ── Secret-path guard applies to every command token ─────────────────

@pytest.mark.parametrize(
    "command",
    [
        ["cat", ".env"],
        ["cat", "./config/.env.local"],
        ["cat", "/home/u/.ssh/id_rsa"],
        ["grep", "x", "server.pem"],
        ["head", "/home/u/.aws/credentials"],
        ["find", "/home", "-name", "id_ed25519"],
        ["ls", ".gnupg"],
        ["tail", "cert.key"],
        ["grep", "-r", "password", "/home/u/.ssh"],
        ["cat", "/proc/self/environ"],
        ["cat", "/proc/1/root/home/u/.ssh/id_rsa"],
        ["cat", ".git-credentials"],
    ],
)
def test_secret_paths_rejected_for_read_commands(tmp_path, command):
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(TerminalError, match="secret files"):
        executor._validate(command)


@pytest.mark.parametrize(
    "command",
    [
        ["cat", "main.py"],
        ["cat", "/etc/nginx/nginx.conf"],
        ["grep", "needle", "src/environment.py"],
        ["cat", "requirements.txt"],
    ],
)
def test_non_secret_paths_pass_validation(tmp_path, command):
    executor = TerminalExecutor(tmp_path)
    executor._validate(command)


# ── awk / sed hardened argument validation ──────────────────────────

@pytest.mark.parametrize("executable", ["awk", "gawk"])
@pytest.mark.parametrize(
    "program",
    ['BEGIN{system("id")}', 'BEGIN{system ("id")}', 'BEGIN{system\n("id")}'],
)
def test_awk_system_call_rejected(tmp_path, executable, program):
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(TerminalError, match="system\\(\\) calls are forbidden"):
        executor._validate([executable, program, "file"])


def test_awk_getline_with_pipe_rejected(tmp_path):
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(TerminalError, match="getline with a pipe"):
        executor._validate(["awk", '{"id" | getline line; print line}'])


def test_awk_plain_program_passes_validation(tmp_path):
    executor = TerminalExecutor(tmp_path)
    executor._validate(["awk", "{print $1}", "file.txt"])


def test_awk_program_file_and_dynamic_load_are_inspected(tmp_path):
    (tmp_path / "attack.awk").write_text('BEGIN { system ("id") }', encoding="utf-8")
    executor = TerminalExecutor(tmp_path, extra_allowed={"gawk": None})
    with pytest.raises(HardDenyError, match="system"):
        executor._validate(["awk", "-f", "attack.awk"])
    with pytest.raises(HardDenyError, match="extension"):
        executor._validate(["gawk", "--load=evil", "{print $1}"])
    with pytest.raises(HardDenyError, match="system"):
        executor._validate(["gawk", "--exec", "attack.awk"])


@pytest.mark.parametrize(
    "command",
    [
        ["gawk", "-eBEGIN { system (\"id\") }"],
        ["gawk", "--source=BEGIN { system (\"id\") }"],
        ["gawk", "--source", "BEGIN { system (\"id\") }"],
        ["gawk", "-F", ":", "BEGIN { system (\"id\") }"],
        ["gawk", "-lfile", "{print $1}"],
        ["gawk", "-ihelper.awk", "{print $1}"],
        ["gawk", "--profile=out", "{print $1}"],
        ["awk", "-W", "exec=helper.awk"],
        ["awk", "-W", "source=helper.awk"],
        ["gawk", "--sou=BEGIN { print 1 }"],
        ["gawk", "-Dout"],
        ["gawk", '@include "helper.awk"'],
        ["gawk", 'BEGIN { ns::fn = "system"; @ns::fn("id") }'],
        ["awk", "/don't/ { system (\"id\") }"],
        ["gawk", "-f", "missing.awk"],
    ],
)
def test_awk_option_and_lexer_variants_fail_closed(tmp_path, command):
    executor = TerminalExecutor(tmp_path, extra_allowed={"gawk": None})
    with pytest.raises(HardDenyError):
        executor._validate(command)


def test_awk_attached_exec_file_is_inspected(tmp_path):
    (tmp_path / "attack.awk").write_text('BEGIN { system ("id") }', encoding="utf-8")
    executor = TerminalExecutor(tmp_path, extra_allowed={"gawk": None})
    with pytest.raises(HardDenyError, match="system"):
        executor._validate(["gawk", "-Eattack.awk"])


@pytest.mark.parametrize(
    "command",
    [["awk", "-f", "-"], ["gawk", "-E", "-"], ["sed", "-f", "-", "input"]],
)
def test_language_programs_from_stdin_are_hard_denied(tmp_path, command):
    executor = TerminalExecutor(tmp_path, extra_allowed={"gawk": None})
    with pytest.raises(HardDenyError, match="stdin"):
        executor._validate(command)


@pytest.mark.parametrize(
    "command",
    [
        ["sed", "s/a/b/e", "file"],
        ["sed", "1e touch pwned", "file"],
        ["sed", "w /tmp/out", "file"],
        ["sed", "--in-place=.bak", "s/a/b/", "file"],
        ["sed", "-ni", "s/a/b/", "file"],
        ["sed", "1s/a/b/e", "file"],
        ["sed", "s/a/b/w out", "file"],
        ["sed", "W out", "file"],
        ["sed", "-e", "p", "-e", "w out", "file"],
        ["sed", "1!w out", "file"],
        ["sed", "0~2w out", "file"],
        ["sed", "1,+2W out", "file"],
        ["sed", "/a/!e echo bad", "file"],
        ["sed", r"\%a%w out", "file"],
        ["sed", r"\%a;b%wout", "file"],
        ["sed", r"p;\%a;b%Wout", "file"],
        ["sed", "/a/IWout", "file"],
        ["sed", "wout", "file"],
        ["sed", "-l", "80", "e echo bad", "file"],
        ["sed", "-nl", "80", "w out", "file"],
    ],
)
def test_sed_execute_write_and_inplace_rejected(tmp_path, command):
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(TerminalError):
        executor._validate(command)


def test_sed_read_only_usage_passes_validation(tmp_path):
    executor = TerminalExecutor(tmp_path)
    executor._validate(["sed", "-n", "1,10p", "file.txt"])
    executor._validate(["sed", "s/old/new/", "file.txt"])


def test_sed_script_file_is_inspected(tmp_path):
    (tmp_path / "attack.sed").write_text("p\nw sentinel\n", encoding="utf-8")
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(HardDenyError, match="execute/write"):
        executor._validate(["sed", "-f", "attack.sed", "input.txt"])


@pytest.mark.parametrize(
    "command",
    [["curl", "--version"], ["ssh", "-V"], ["dd", "--version"], ["node", "--version"]],
)
def test_exact_version_probes_have_consistent_terminal_validation(tmp_path, command):
    TerminalExecutor(tmp_path)._validate(command)


@pytest.mark.parametrize("command", [["node", "version"]])
def test_node_non_probe_forms_require_approval(tmp_path, monkeypatch, command):
    executor = TerminalExecutor(tmp_path)
    requested = []
    monkeypatch.setattr(
        TerminalExecutor,
        "_request_user_approval",
        classmethod(lambda cls, c, reason: requested.append((c, reason)) or False),
    )
    result = executor.run(command)
    assert requested
    assert result.denied is True


@pytest.mark.parametrize(
    "command",
    [
        ["sort", "-o", "output.txt", "input.txt"],
        ["sort", "--compress-program=sh", "input.txt"],
        ["sort", "-ro", "output.txt", "input.txt"],
        ["sort", "-T", "temp", "input.txt"],
        ["sort", "-bT/tmp/temp", "input.txt"],
        ["sort", "--o=output.txt", "input.txt"],
        ["sort", "--comp=helper", "input.txt"],
        ["diff", "--output=output.txt", "left", "right"],
        ["diff", "--ou=output.txt", "left", "right"],
        ["uniq", "input.txt", "output.txt"],
        ["git", "diff", "--output=output.txt"],
        ["git", "log", "--output=output.txt"],
        ["git", "diff", "--ext-diff"],
        ["git", "log", "--textconv"],
        ["git", "stash", "show", "--output=output.txt"],
        ["git", "stash", "show", "--textconv"],
        ["git", "stash", "list", "--ext-diff"],
        ["git", "log", "--show-signature"],
        ["git", "show", "--format=%G?"],
        ["git", "show", "--for=%G?"],
        ["git", "log", "--pretty=fixture-format"],
        ["git", "log", "--alternate-refs"],
        ["git", "show", "--alt"],
        ["git", "log", "--remerge-diff"],
        ["git", "show", "--diff-merges=remerge"],
        ["git", "log", "--diff-merges=r"],
        ["git", "diff", "--submodule=diff"],
        ["git", "diff", "--sub=diff", "--ignore-su=none"],
        ["git", "status", "--ignore-submodules=none"],
        ["git", "status", "--ignore-su=none"],
        ["git", "tag", "-v", "v1"],
        ["git", "tag", "--sig", "v1"],
        ["git", "branch", "--format=%(signature:grade)"],
        ["git", "tag", "--sort=signature:signer"],
        ["git", "fixture-alias"],
        ["git", "--no-pager", "diff", "--output=output.txt"],
        ["file", "--compile"],
        ["file", "--comp"],
        ["file", "--co"],
        ["file", "-bC"],
        ["tree", "-o", "output.txt"],
        ["tree", "-Cooutput.txt"],
        ["hostname", "new-hostname"],
        ["gfind", ".", "-delete"],
        ["gsort", "--temp=output.txt", "input.txt"],
        ["gdiff", "--output=output.txt", "left", "right"],
        ["guniq", "input.txt", "output.txt"],
        ["ggrep", "-R", "fixture", "."],
        ["docker", "--config=/outside", "ps"],
        ["make", "-sC../outside"],
        ["make", "--fil=Alt.mk"],
        ["make", "--direc=sub"],
        ["make", "--eval=all:; touch sentinel"],
        ["make", "-sEall:; touch sentinel"],
        ["make", "MAKEFILES=Extra.mk"],
        ["make", "MAKEFILES:=Extra.mk"],
        ["make", " MAKEFILES=Extra.mk"],
        ["make", "MAKEFILES =Extra.mk"],
        ["make", "--temp-stdin=Extra.mk"],
        ["make", "--temp=Extra.mk"],
        ["make", "-f", "-"],
        ["make", "-f-"],
        ["make", "--file=-"],
        ["node", "--version", "extra"],
        ["busybox", "rm", "victim"],
        ["toybox", "sh", "script.sh"],
        ["", "arg"],
        ["echo", "nul\x00byte"],
    ],
)
def test_indirect_execution_and_malformed_argv_are_hard_denied(tmp_path, command):
    with pytest.raises(HardDenyError):
        TerminalExecutor(tmp_path)._validate(command)


@pytest.mark.parametrize(
    "command,needs_diff_guards",
    [
        (["git", "status"], False),
        (["git", "diff"], True),
        (["git", "log", "-n", "1"], True),
        (["git", "stash", "list"], True),
    ],
)
def test_git_spawn_argv_disables_implicit_hooks_and_helpers(
    tmp_path, monkeypatch, command, needs_diff_guards
):
    captured: list[list[str]] = []

    class FakeProcess:
        returncode = 0

        def communicate(self, timeout=None):
            return b"", b""

    def fake_popen(argv, **kwargs):
        captured.append(argv)
        return FakeProcess()

    monkeypatch.setattr(terminal_module.subprocess, "Popen", fake_popen)
    result = TerminalExecutor(tmp_path).run(command)

    assert result.returncode == 0
    assert len(captured) == 1
    actual = captured[0]
    assert actual[1:20] == [
        "-c", "core.fsmonitor=false", "-c", "log.showSignature=false",
        "-c", "format.pretty=medium",
        "-c", "log.diffMerges=off",
        "-c", "diff.submodule=short",
        "-c", "diff.ignoreSubmodules=all",
        "-c", "core.alternateRefsCommand=",
        "-c", "branch.sort=refname",
        "-c", "tag.sort=refname",
        "--no-optional-locks",
    ]
    assert "--ignore-submodules=all" in actual
    if needs_diff_guards:
        assert "--no-ext-diff" in actual
        assert "--no-textconv" in actual


@pytest.mark.parametrize(
    "command",
    [
        ["python", "-"],
        ["python", "-i", "safe.py"],
        ["python", "-Bi", "safe.py"],
        ["node", "--interactive"],
        ["node", "-rfixture", "safe.js"],
        ["node", "--experimental-loader=fixture", "safe.js"],
        ["node", "--run", "fixture"],
        ["php", "-r", "echo 1;"],
        ["php", "-B", "echo 1;"],
        ["perl", "-E", "say 1"],
        ["ruby", "-rfixture", "safe.rb"],
        ["ruby", "-Csub", "safe.rb"],
        ["ruby", "-S", "safe.rb"],
        ["perl", "-S", "safe.pl"],
        ["perl", "-we", "print 1"],
        ["ruby", "-we", "puts 1"],
        ["php", "-nr", "echo 1;"],
        ["php", "-c", "fixture.ini", "safe.php"],
        ["php", "-S", "127.0.0.1:8000", "safe.php"],
        ["zsh", "-c", "echo bad"],
        ["dash", "-c", "echo bad"],
    ],
)
def test_interpreter_stdin_inline_preload_and_shell_aliases_are_hard_denied(tmp_path, command):
    (tmp_path / "safe.py").write_text("print('fixture')\n", encoding="utf-8")
    (tmp_path / "safe.rb").write_text("puts 'fixture'\n", encoding="utf-8")
    with pytest.raises(HardDenyError):
        TerminalExecutor(tmp_path)._validate(command)


@pytest.mark.parametrize("cwd", [".kvist", ".ssh", ".aws", ".gnupg"])
def test_sensitive_working_directories_are_hard_denied(tmp_path, cwd):
    (tmp_path / cwd).mkdir()
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(HardDenyError, match="working directory"):
        executor._resolve_cwd(cwd)


def test_sensitive_working_directory_symlink_alias_is_hard_denied(tmp_path):
    (tmp_path / ".kvist").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / ".kvist", target_is_directory=True)
    with pytest.raises(HardDenyError, match="internal"):
        TerminalExecutor(tmp_path)._resolve_cwd("alias")


@pytest.mark.parametrize(
    "command",
    [
        ["awk", "-f.env"],
        ["sed", "-f.env", "input"],
        ["cat", "--config=.kvist/permissions.json"],
        ["cat", "@.env"],
    ],
)
def test_attached_sensitive_path_forms_are_hard_denied(tmp_path, command):
    with pytest.raises(HardDenyError):
        TerminalExecutor(tmp_path)._validate(command)


# ── MODERATE commands go to HITL; non-TTY denial is deterministic ────

def test_interpreter_script_run_triggers_approval(tmp_path, monkeypatch):
    executor = TerminalExecutor(tmp_path)
    requested: list = []

    def fake_approval(command, reason):
        requested.append((command, reason))
        return False

    monkeypatch.setattr(
        TerminalExecutor, "_request_user_approval", classmethod(lambda cls, c, r: fake_approval(c, r))
    )
    result = executor.run(["python", "script.py"])

    assert requested, "python script.py must trigger HITL approval"
    assert result.denied is True


def test_moderate_command_without_tty_raises_deterministically(tmp_path):
    """pytest runs without a TTY: approval cannot be asked → TerminalError."""
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(TerminalError):
        executor.run(["python", "script.py"])


# ── Profile-based resource limits ────────────────────────────────────

def test_resource_limits_profiles_differ(monkeypatch):
    for name in ("AGENT_RLIMIT_CPU", "AGENT_RLIMIT_AS_MB", "AGENT_RLIMIT_NPROC"):
        monkeypatch.delenv(name, raising=False)

    dev = terminal_module.resolve_resource_limits("dev")
    restricted = terminal_module.resolve_resource_limits("restricted")

    assert dev.cpu_seconds == 120
    assert dev.address_space_bytes == 2048 * 1024 * 1024
    assert restricted.cpu_seconds == 8
    assert restricted.address_space_bytes == 256 * 1024 * 1024
    assert dev.nproc > 0 and restricted.nproc > 0


def test_resource_limits_env_overrides(monkeypatch):
    monkeypatch.setenv("AGENT_RLIMIT_CPU", "42")
    monkeypatch.setenv("AGENT_RLIMIT_AS_MB", "512")
    monkeypatch.setenv("AGENT_RLIMIT_NPROC", "128")

    limits = terminal_module.resolve_resource_limits("dev")

    assert limits.cpu_seconds == 42
    assert limits.address_space_bytes == 512 * 1024 * 1024
    assert limits.nproc == 128


def test_unix_resource_limits_set_nproc(monkeypatch):
    import resource

    calls: dict = {}

    def record(kind, value):
        calls[kind] = value

    monkeypatch.setattr(resource, "setrlimit", record)
    limits = terminal_module.ResourceLimits(
        cpu_seconds=10,
        address_space_bytes=1024 * 1024 * 1024,
        nproc=99,
        fsize_bytes=1024,
        nofile=32,
    )
    terminal_module._unix_resource_limits(limits)

    assert calls[resource.RLIMIT_NPROC] == (99, 99)
    assert calls[resource.RLIMIT_CPU] == (10, 10)
