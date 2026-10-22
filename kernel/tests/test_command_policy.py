from __future__ import annotations

import pytest

from kernel.sandbox.command_policy import RiskLevel, assess_command


def test_assess_command_classifies_safe_read_only_command():
    result = assess_command(["git", "status"])
    assert result.risk is RiskLevel.SAFE
    assert result.requires_approval is False


def test_assess_command_blocks_empty_and_unconditionally_dangerous_commands():
    assert assess_command([]).risk is RiskLevel.DANGEROUS
    rm = assess_command(["rm", "-rf", "build"])
    assert rm.risk is RiskLevel.DANGEROUS
    assert rm.requires_approval is True


def test_assess_command_raises_risk_for_dangerous_flags():
    result = assess_command(["git", "reset", "--hard"])
    assert result.risk is RiskLevel.DANGEROUS
    assert "dangerous flags" in result.reason


def test_assess_command_does_not_downgrade_host_administration_for_devops():
    coder = assess_command(["apt", "install", "pkg"], role="coder")
    devops = assess_command(["apt", "install", "pkg"], role="devops")
    assert coder.risk is RiskLevel.DANGEROUS
    assert devops.risk is RiskLevel.DANGEROUS


def test_assess_command_unknown_command_is_moderate_and_requires_approval():
    """MODERATE now always goes to HITL; only a policy allow-rule skips it."""
    result = assess_command(["custom-tool", "arg"])
    assert result.risk is RiskLevel.MODERATE
    assert result.requires_approval is True


# ── find: pure search is safe, action predicates are dangerous ───────

def test_find_pure_search_is_safe():
    result = assess_command(["find", ".", "-name", "*.py"])
    assert result.risk is RiskLevel.SAFE
    assert result.requires_approval is False


@pytest.mark.parametrize(
    "command",
    [
        ["find", ".", "-delete"],
        ["find", ".", "-type", "f", "-exec", "rm", "{}", "+"],
        ["find", "/home", "-execdir", "cat", "{}", "+"],
        ["find", ".", "-ok", "rm", "{}", ";"],
        ["find", ".", "-fprintf", "/tmp/out", "%p"],
    ],
)
def test_find_with_action_predicates_is_dangerous(command):
    result = assess_command(command)
    assert result.risk is RiskLevel.DANGEROUS
    assert result.requires_approval is True


# ── Interpreters and build runners require approval ─────────────────

@pytest.mark.parametrize(
    "command",
    [
        ["python", "script.py"],
        ["python3", "manage.py", "migrate"],
        ["node", "server.js"],
        ["make", "install"],
        ["cargo", "build"],
        ["go", "run", "main.go"],
        ["awk", "{print $1}", "file"],
        ["gawk", "-f", "prog.awk"],
    ],
)
def test_interpreter_execution_requires_approval(command):
    result = assess_command(command)
    assert result.risk is RiskLevel.MODERATE
    assert result.requires_approval is True


def test_interpreter_version_probe_stays_safe():
    result = assess_command(["python", "--version"])
    assert result.risk is RiskLevel.SAFE
    assert result.requires_approval is False


# ── Exact executable-specific version probes ────────────────────────

@pytest.mark.parametrize(
    "command",
    [
        ["curl", "--version"],
        ["ssh", "-V"],
        ["dd", "--version"],
        ["go", "version"],
        ["node", "--version"],
    ],
)
def test_exact_version_probe_is_safe_even_for_dangerous_executables(command):
    result = assess_command(command)
    assert result.risk is RiskLevel.SAFE
    assert result.requires_approval is False


def test_version_flag_with_extra_args_is_not_a_version_probe():
    """`ssh -v host` is a connection, not a version check."""
    result = assess_command(["ssh", "-v", "example.com"])
    assert result.risk is RiskLevel.DANGEROUS
    assert result.requires_approval is True


@pytest.mark.parametrize(
    "command",
    [
        ["node", "version"],
        ["node", "--version", "extra"],
        ["python", "version"],
        ["python", "-v"],
        ["custom-tool", "version"],
    ],
)
def test_non_probe_version_tokens_are_not_safe(command):
    result = assess_command(command)
    assert result.risk is not RiskLevel.SAFE
    assert result.requires_approval is True


def test_versioned_interpreter_alias_uses_same_policy_without_trusting_paths():
    assert assess_command(["python3.14", "--version"]).risk is RiskLevel.SAFE
    assert assess_command(["/usr/bin/python3.14", "--version"]).risk is RiskLevel.MODERATE
    script = assess_command(["/usr/bin/python3.14", "script.py"])
    assert script.risk is RiskLevel.MODERATE
    assert script.requires_approval is True


@pytest.mark.parametrize(
    "command",
    [
        ["sort", "--compress-program=sh", "input"],
        ["sort", "-o", "output", "input"],
        ["sort", "-ro", "output", "input"],
        ["sort", "-T", "temp", "input"],
        ["sort", "-bT/tmp/temp", "input"],
        ["sort", "--o=output", "input"],
        ["sort", "--comp=helper", "input"],
        ["diff", "--output=output", "left", "right"],
        ["diff", "--ou=output", "left", "right"],
        ["uniq", "input", "output"],
        ["git", "diff", "--output=output"],
        ["git", "log", "--output=output"],
        ["git", "stash", "show", "--output=output"],
        ["git", "stash", "list", "--textconv"],
        ["tree", "-o", "output"],
        ["tree", "-Cooutput"],
        ["file", "-bC"],
        ["file", "--comp"],
        ["file", "--co"],
        ["gsort", "--temp=output", "input"],
        ["gdiff", "--output=output", "left", "right"],
        ["guniq", "input", "output"],
    ],
)
def test_read_only_command_output_modes_are_dangerous(command):
    assert assess_command(command).risk is RiskLevel.DANGEROUS


# ── docker run isolation-breaking flags ──────────────────────────────

@pytest.mark.parametrize(
    "command",
    [
        ["docker", "run", "--privileged", "-v", "/:/host", "alpine", "sh"],
        ["docker", "run", "--volume", "/etc:/etc", "alpine"],
        ["docker", "run", "--mount", "type=bind,src=/,dst=/host", "alpine"],
        ["docker", "run", "--network=host", "alpine"],
        ["docker", "run", "--network", "host", "alpine"],
        ["docker", "run", "--pid=host", "alpine"],
        ["docker", "run", "--privileged=true", "alpine"],
        ["docker", "run", "-v=/:/host", "alpine"],
        ["docker", "--context", "prod", "run", "--privileged", "alpine"],
        ["docker", "--tlscert", "client.crt", "run", "--network=host", "alpine"],
        ["docker", "run", "-itv", "/:/host", "alpine"],
        ["docker", "run", "--cap-add=SYS_ADMIN", "alpine"],
        ["docker", "run", "--cap-add", "SYS_ADMIN", "alpine"],
        ["docker", "container", "run", "--privileged", "alpine"],
        ["docker", "--context", "prod", "container", "run", "--pid=host", "alpine"],
        ["docker", "create", "--network=host", "alpine"],
        ["docker", "container", "create", "--privileged", "alpine"],
        ["docker", "build", "--network=host", "."],
        ["docker", "build", "--allow=security.insecure", "."],
        ["docker", "buildx", "build", "--allow", "network.host", "."],
        ["docker", "service", "create", "--mount", "type=bind,src=/,dst=/host", "image"],
        ["docker", "image", "build", "--network=host", "."],
        ["docker", "builder", "build", "--secret=id=x,src=/host", "."],
        ["docker", "buildx", "b", "--ssh=default=/host/socket", "."],
        ["docker", "buildx", "bake", "--allow=fs.read=/", "."],
        ["docker", "build", "--build-context=host=/", "."],
        ["docker", "build", "--cache-to=type=local,dest=/outside", "."],
    ],
)
def test_docker_run_isolation_breaking_flags_are_dangerous(command):
    for role in ("coder", "devops"):
        result = assess_command(command, role=role)
        assert result.risk is RiskLevel.DANGEROUS
        assert result.requires_approval is True


@pytest.mark.parametrize(
    "command",
    [
        ["docker", "ps"],
        ["docker", "images"],
        ["docker", "logs", "web"],
        ["docker", "inspect", "web"],
        ["docker", "stats", "--no-stream"],
        ["docker", "info"],
        ["docker", "version"],
    ],
)
def test_docker_daemon_reads_require_approval(command):
    assessment = assess_command(command)
    assert assessment.risk is RiskLevel.MODERATE
    assert assessment.requires_approval is True


def test_docker_client_only_version_probe_stays_safe():
    for flag in ("--version", "-v"):
        assessment = assess_command(["docker", flag])
        assert assessment.risk is RiskLevel.SAFE
        assert assessment.requires_approval is False


@pytest.mark.parametrize(
    "command",
    [
        ["docker", "--host", "tcp://daemon.invalid:2375", "ps"],
        ["docker", "--host=tcp://daemon.invalid:2375", "images"],
        ["docker", "-Htcp://daemon.invalid:2375", "info"],
        ["docker", "--context", "production", "version"],
        ["docker", "-cproduction", "ps"],
        ["docker", "--config", "docker-config", "logs", "web"],
        ["docker", "--tlsverify", "ps"],
        ["docker", "--tlscert=client-fixture", "inspect", "web"],
    ],
)
def test_docker_daemon_access_overrides_are_dangerous(command):
    for role in ("coder", "devops"):
        assessment = assess_command(command, role=role)
        assert assessment.risk is RiskLevel.DANGEROUS
        assert assessment.requires_approval is True


def test_docker_run_without_risky_flags_is_moderate():
    result = assess_command(["docker", "run", "alpine", "echo", "hi"])
    assert result.risk is RiskLevel.MODERATE


# ── git push publishes to a remote → approval required ──────────────

def test_git_push_requires_approval_but_read_commands_do_not():
    push = assess_command(["git", "push", "origin", "main"])
    assert push.risk is RiskLevel.DANGEROUS
    assert push.requires_approval is True
    assert assess_command(["git", "status"]).requires_approval is False
    assert assess_command(["git", "diff"]).requires_approval is False
    assert assess_command(
        ["git", "diff", "--no-ext-diff", "--no-textconv"]
    ).requires_approval is False


def test_hostname_mutation_and_hostnamectl_administration_are_dangerous():
    assert assess_command(["hostname"]).risk is RiskLevel.SAFE
    assert assess_command(["hostname", "--fqdn"]).risk is RiskLevel.SAFE
    assert assess_command(["hostname", "new-name"]).risk is RiskLevel.DANGEROUS
    for role in ("coder", "devops"):
        assert assess_command(["hostnamectl", "set-hostname", "new-name"], role=role).risk is RiskLevel.DANGEROUS
        assert assess_command(["hostnamectl", "hostname", "new-name"], role=role).risk is RiskLevel.DANGEROUS


@pytest.mark.parametrize(
    "command",
    [
        ["systemctl", "--no-pager", "restart", "fixture"],
        ["pip", "--quiet", "uninstall", "fixture"],
        ["npm", "--silent", "uninstall", "fixture"],
        ["sysctl", "kernel.hostname=fixture"],
        ["sysctl", "-pfixture.conf"],
        ["sysctl", "--sys"],
        ["docker-compose", "up"],
    ],
)
def test_hidden_subcommands_and_host_admin_forms_are_dangerous(command):
    assert assess_command(command).risk is RiskLevel.DANGEROUS


def test_gnu_find_alias_preserves_action_guard():
    assert assess_command(["gfind", ".", "-delete"]).risk is RiskLevel.DANGEROUS


@pytest.mark.parametrize(
    "command",
    [
        ["./ls", "-l"],
        ["tools/cat", "file"],
        [r"C:node.exe", "--version"],
        ["/usr/bin/git", "status"],
    ],
)
def test_path_qualified_executables_never_inherit_safe_classification(command):
    result = assess_command(command)
    assert result.risk is not RiskLevel.SAFE
    assert result.requires_approval is True


@pytest.mark.parametrize(
    "command",
    [
        ["git", "branch", "new-branch"],
        ["git", "tag", "v1"],
        ["git", "remote", "set-url", "origin", "example"],
        ["git", "stash", "drop"],
        ["git", "fetch", "origin"],
    ],
)
def test_mutating_git_forms_are_not_classified_read_only(command):
    assert assess_command(command).requires_approval is True


@pytest.mark.parametrize(
    "command",
    [
        ["git", "tag", "-v", "v1"],
        ["git", "tag", "--sign", "v1"],
        ["git", "tag", "--local-u=fixture", "v1"],
        ["docker", "push", "fixture/image"],
        ["docker", "image", "push", "fixture/image"],
        ["docker", "run", "-p8080:80", "image"],
        ["docker", "create", "--publish-all", "image"],
        ["docker", "service", "create", "--publish=8080:80", "image"],
        ["docker", "service", "update", "--publish-add=8080:80", "fixture"],
        ["docker", "build", "--push", "."],
        ["docker", "buildx", "build", "--push", "."],
        ["docker", "buildx", "imagetools", "create", "-t", "fixture/image", "source"],
        ["docker", "buildx", "bake"],
        ["docker", "stack", "deploy", "-c", "compose.yml", "fixture"],
        ["docker", "volume", "rm", "fixture"],
        ["docker", "network", "prune"],
    ],
)
def test_helper_publication_and_nested_docker_forms_are_dangerous(command):
    assert assess_command(command).risk is RiskLevel.DANGEROUS


@pytest.mark.parametrize(
    "command",
    [
        ["git", "log", "--remerge-diff"],
        ["git", "show", "--diff-merges=remerge"],
        ["git", "log", "--diff-merges=r"],
        ["git", "diff", "--submodule=diff"],
        ["git", "diff", "--sub=diff"],
        ["git", "status", "--ignore-submodules=none"],
        ["git", "status", "--ignore-su=none"],
        ["git", "log", "--alternate-refs"],
        ["git", "branch", "--format=%(signature:grade)"],
        ["git", "tag", "--sort=signature:signer"],
    ],
)
def test_git_recursive_helper_modes_are_dangerous(command):
    assert assess_command(command).risk is RiskLevel.DANGEROUS
