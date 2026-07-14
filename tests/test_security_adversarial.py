from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import terminal as terminal_module
from sandbox import SandboxMode
from terminal import HardDenyError, TerminalExecutor


def _executor_with_approval(workspace: Path) -> TerminalExecutor:
    executor = TerminalExecutor(workspace)
    executor._request_user_approval = lambda command, reason: True
    return executor


@pytest.mark.parametrize(
    "command",
    [
        ["find", ".", "-delete"],
        ["find", ".", "-exec", "touch", "sentinel", ";"],
        ["awk", 'BEGIN { system ("touch sentinel") }'],
        ["gawk", 'BEGIN { system\n("touch sentinel") }'],
        ["sed", "s/a/b/w sentinel", "input.txt"],
        ["sed", "e touch sentinel", "input.txt"],
        ["sed", "1!w sentinel", "input.txt"],
        ["sed", "0~2w sentinel", "input.txt"],
        ["sed", r"\%a%w sentinel", "input.txt"],
        ["sed", r"p;\%a;b%Wsentinel", "input.txt"],
        ["sed", "/a/IWsentinel", "input.txt"],
        ["sed", "wsentinel", "input.txt"],
        ["sed", "-l", "80", "w sentinel", "input.txt"],
        ["sort", "-ro", "sentinel", "input.txt"],
        ["sort", "-T", "sentinel", "input.txt"],
        ["sort", "-bTsentinel", "input.txt"],
        ["sort", "--o=sentinel", "input.txt"],
        ["sort", "--comp=fixture-helper", "input.txt"],
        ["diff", "--output=sentinel", "input.txt", "input.txt"],
        ["diff", "--ou=sentinel", "input.txt", "input.txt"],
        ["uniq", "input.txt", "sentinel"],
        ["git", "diff", "--output=sentinel"],
        ["git", "log", "--output=sentinel"],
        ["git", "diff", "--ext-diff"],
        ["git", "stash", "show", "--output=sentinel"],
        ["git", "stash", "list", "--textconv"],
        ["git", "--no-pager", "diff", "--output=sentinel"],
        ["git", "log", "--show-signature"],
        ["git", "show", "--format=%G?"],
        ["git", "show", "--for=%G?"],
        ["git", "log", "--pretty=fixture-format"],
        ["git", "log", "--alternate-refs"],
        ["git", "show", "--alt"],
        ["git", "log", "--remerge-diff"],
        ["git", "show", "--remer"],
        ["git", "show", "--diff-merges=remerge"],
        ["git", "log", "--diff-m=r"],
        ["git", "diff", "--submodule=diff"],
        ["git", "diff", "--subm=log"],
        ["git", "diff", "--sub=diff", "--ignore-su=none"],
        ["git", "status", "--ignore-submodules=none"],
        ["git", "status", "--ignore-subm=dirty"],
        ["git", "status", "--ignore-su=none"],
        ["git", "tag", "-v", "v1"],
        ["git", "tag", "--sig", "v1"],
        ["git", "branch", "--format=%(signature:grade)"],
        ["git", "branch", "--for", "%(*signature:signer)"],
        ["git", "tag", "--sort=-signature:key"],
        ["git", "fixture-alias"],
        ["file", "--compile"],
        ["file", "--comp"],
        ["file", "--co"],
        ["file", "-bC"],
        ["tree", "-o", "sentinel"],
        ["tree", "-Cosentinel"],
        ["hostname", "fixture-host"],
        ["gawk", "-eBEGIN { system (\"touch sentinel\") }"],
        ["gawk", "--source=BEGIN { system (\"touch sentinel\") }"],
        ["gawk", "-F", ":", "BEGIN { system (\"touch sentinel\") }"],
        ["gawk", "-lfile", "{print $1}"],
        ["gawk", "--profile=sentinel", "{print $1}"],
        ["awk", "-W", "exec=fixture.awk"],
        ["awk", "-W", "source=fixture.awk"],
        ["gawk", "--sou=BEGIN { print 1 }"],
        ["gawk", "-Dsentinel"],
        ["gawk", 'BEGIN { ns::fn = "system"; @ns::fn("touch sentinel") }'],
        ["awk", "/don't/ { system (\"touch sentinel\") }"],
        ["awk", "-f.env"],
        ["sed", "-f.env", "input.txt"],
        ["cat", "@.env"],
        ["grep", "-R", "fixture", "."],
        ["ggrep", "-R", "fixture", "."],
        ["gfind", ".", "-delete"],
        ["gsort", "--temp=sentinel", "input.txt"],
        ["gdiff", "--output=sentinel", "input.txt", "input.txt"],
        ["guniq", "input.txt", "sentinel"],
        ["rg", "fixture", "."],
        ["cat", "input.txt", "|", "python", "-"],
        ["python", "-Bcfrom pathlib import Path;Path('sentinel').write_text('pwned')"],
        ["python", "-Bi", "safe.py"],
        ["node", "-rattack.js", "safe.js"],
        ["node", "--experimental-loader=attack.js", "safe.js"],
        ["node", "--run", "fixture"],
        ["php", "-r", "echo 'fixture';"],
        ["php", "-c", "fixture.ini", "safe.php"],
        ["ruby", "-Csub", "safe.rb"],
        ["perl", "-S", "safe.pl"],
        ["perl", "-we", "print 1"],
        ["ruby", "-we", "puts 1"],
        ["php", "-nr", "echo 1;"],
        ["zsh", "-c", "echo fixture"],
        ["/bin/rm", "-f", "sentinel"],
        ["make", "-sC../outside"],
        ["make", "--fil=Alt.mk"],
        ["make", "--direc=sub"],
        ["make", "--eval=all:; touch sentinel"],
        ["make", "-sEall:; touch sentinel"],
        ["make", "MAKEFILES=Extra.mk"],
        ["make", "MAKEFILES!=touch sentinel"],
        ["make", " MAKEFILES=Extra.mk"],
        ["make", "MAKEFILES =Extra.mk"],
        ["make", "--temp-stdin=Extra.mk"],
        ["make", "--temp=Extra.mk"],
        ["make", "-f", "-"],
        ["make", "-f-"],
        ["make", "--file=-"],
        ["node", "--version", "extra"],
    ],
)
def test_hard_denials_never_prompt_spawn_or_create_side_effects(tmp_path, monkeypatch, command):
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("preserve", encoding="utf-8")
    (tmp_path / "input.txt").write_text("a\n", encoding="utf-8")
    executor = TerminalExecutor(tmp_path, extra_allowed={"gawk": None})
    approvals = []
    monkeypatch.setattr(
        TerminalExecutor,
        "_request_user_approval",
        classmethod(lambda cls, c, reason: approvals.append((c, reason)) or True),
    )
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )

    with pytest.raises(HardDenyError, match="Hard security denial"):
        executor.run(command)

    assert approvals == []
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_make_unattested_input_selector_cannot_reach_mutating_approval_or_spawn(
    tmp_path, monkeypatch
):
    extra = tmp_path / "Extra.mk"
    extra.write_text("all:\n\t@echo safe\n", encoding="utf-8")
    approvals = []
    executor = TerminalExecutor(tmp_path)

    def mutate(command, reason):
        approvals.append((command, reason))
        extra.write_text("all:\n\t@touch sentinel\n", encoding="utf-8")
        return True

    executor._request_user_approval = mutate
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )

    with pytest.raises(HardDenyError, match="MAKEFILES"):
        executor.run(["make", "MAKEFILES=Extra.mk"])

    assert approvals == []
    assert "touch sentinel" not in extra.read_text(encoding="utf-8")
    assert not (tmp_path / "sentinel").exists()


def test_linked_worktree_common_filter_config_is_hard_denied(tmp_path, monkeypatch):
    worktree = tmp_path / "linked"
    git_dir = tmp_path / ".git" / "worktrees" / "linked"
    worktree.mkdir()
    git_dir.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: ../.git/worktrees/linked\n", encoding="utf-8")
    (git_dir / "commondir").write_text("../..\n", encoding="utf-8")
    (tmp_path / ".git" / "config").write_text(
        '[filter "fixture"]\n\tprocess = ./helper\n', encoding="utf-8"
    )
    approvals = []
    executor = TerminalExecutor(tmp_path)
    executor._request_user_approval = lambda command, reason: approvals.append(command) or True
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )

    with pytest.raises(HardDenyError, match="worktree filters"):
        executor.run(["git", "status"], cwd="linked")

    assert approvals == []


def test_linked_worktree_common_config_mutation_invalidates_approval(tmp_path, monkeypatch):
    worktree = tmp_path / "linked"
    git_dir = tmp_path / ".git" / "worktrees" / "linked"
    worktree.mkdir()
    git_dir.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: ../.git/worktrees/linked\n", encoding="utf-8")
    (git_dir / "commondir").write_text("../..\n", encoding="utf-8")
    config = tmp_path / ".git" / "config"
    config.write_text("[core]\n\tbare = false\n", encoding="utf-8")
    executor = TerminalExecutor(tmp_path)

    def mutate(command, reason):
        config.write_text("[core]\n\tbare = true\n", encoding="utf-8")
        return True

    executor._request_user_approval = mutate
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )

    with pytest.raises(HardDenyError, match="context changed"):
        executor.run(["git", "tag", "v1"], cwd="linked")


def test_workspace_safe_name_executable_cannot_inherit_project_allow(tmp_path, monkeypatch):
    sentinel = tmp_path / "sentinel"
    local_ls = tmp_path / "ls"
    local_ls.write_text("harmless fixture", encoding="utf-8")
    (tmp_path / ".kvist").mkdir()
    (tmp_path / ".kvist" / "permissions.json").write_text(
        json.dumps({"allow": ["ls *"], "deny": [], "ask": []}), encoding="utf-8"
    )
    requested = []
    executor = TerminalExecutor(tmp_path)
    monkeypatch.setattr(
        TerminalExecutor,
        "_request_user_approval",
        classmethod(lambda cls, command, reason: requested.append((command, reason)) or False),
    )
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )

    result = executor.run(["./ls", "-l"])

    assert result.denied is True
    assert requested and "origin" in requested[0][1].casefold()
    assert not sentinel.exists()


def test_workspace_executable_symlink_escape_is_hard_denied_without_spawn(tmp_path, monkeypatch):
    (tmp_path / "ls").symlink_to("/usr/bin/ls")
    approvals = []
    executor = TerminalExecutor(tmp_path)
    monkeypatch.setattr(
        TerminalExecutor,
        "_request_user_approval",
        classmethod(lambda cls, command, reason: approvals.append((command, reason)) or True),
    )
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )

    with pytest.raises(HardDenyError, match="executable symlink"):
        executor.run(["./ls"])
    assert approvals == []


def test_untrusted_executable_mutation_invalidates_approval(tmp_path, monkeypatch):
    executable = tmp_path / "ls"
    executable.write_text("first harmless fixture", encoding="utf-8")
    executor = TerminalExecutor(tmp_path)

    def mutate(command, reason):
        executable.write_text("second harmless fixture", encoding="utf-8")
        return True

    executor._request_user_approval = mutate
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match="context changed"):
        executor.run(["./ls"])


def test_parent_path_cannot_shadow_bare_system_executable(tmp_path, monkeypatch):
    sentinel = tmp_path / "sentinel"
    fake_ls = tmp_path / "ls"
    fake_ls.write_text("#!/bin/sh\ntouch sentinel\n", encoding="utf-8")
    fake_ls.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    result = TerminalExecutor(tmp_path).run(["ls"])

    assert result.returncode == 0
    assert not sentinel.exists()


@pytest.mark.parametrize(
    "command",
    [
        ["docker", "--tlscert", "client-fixture", "run", "--network=host", "image"],
        ["docker", "run", "-itv", "fixture:/host", "image"],
        ["docker", "run", "--cap-add=SYS_ADMIN", "image"],
        ["docker", "container", "run", "--privileged", "image"],
        ["docker", "create", "--network=host", "image"],
        ["docker", "container", "create", "--privileged", "image"],
        ["docker", "build", "--network=host", "."],
        ["docker", "buildx", "build", "--allow=security.insecure", "."],
        ["docker", "service", "create", "--mount", "type=bind,src=/,dst=/host", "image"],
        ["docker", "image", "build", "--network=host", "."],
        ["docker", "builder", "build", "--secret=id=x,src=/host", "."],
        ["docker", "buildx", "b", "--ssh=default=/host/socket", "."],
        ["docker", "buildx", "bake", "--allow=fs.read=/", "."],
        ["docker", "build", "--build-context=host=/", "."],
        ["docker", "build", "--output=type=local,dest=/outside", "."],
        ["docker", "run", "--gpus=all", "image"],
        ["docker", "run", "-p8080:80", "image"],
        ["docker", "create", "-P", "image"],
        ["docker", "service", "create", "--publish=8080:80", "image"],
        ["docker", "service", "update", "--publish-add=8080:80", "fixture"],
        ["docker", "build", "--push", "."],
        ["docker", "buildx", "build", "--push", "."],
        ["docker", "buildx", "build", "-otype=registry", "."],
        ["docker", "buildx", "imagetools", "create", "-t", "fixture/image", "source"],
        ["docker", "buildx", "bake"],
        ["docker", "buildx", "bake", "--set=*.push=true"],
        ["docker", "stack", "deploy", "-c", "compose.yml", "fixture"],
        ["docker", "stack", "up", "-c", "compose.yml", "fixture"],
        ["docker", "push", "fixture/image"],
        ["docker", "image", "push", "fixture/image"],
        ["docker", "volume", "rm", "fixture"],
        ["docker", "network", "prune"],
        ["docker", "buildx", "prune"],
    ],
)
def test_docker_escape_variants_never_spawn_under_project_allow(tmp_path, monkeypatch, command):
    (tmp_path / ".kvist").mkdir()
    (tmp_path / ".kvist" / "permissions.json").write_text(
        json.dumps({"allow": ["docker run *", "docker *"], "deny": [], "ask": []}),
        encoding="utf-8",
    )
    requested = []
    executor = TerminalExecutor(tmp_path)
    monkeypatch.setattr(
        TerminalExecutor,
        "_request_user_approval",
        classmethod(lambda cls, argv, reason: requested.append((argv, reason)) or False),
    )
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )

    result = executor.run(command)

    assert result.denied is True
    assert requested and "risk assessment" in requested[0][1].casefold()


@pytest.mark.parametrize(
    "command",
    [
        ["systemctl", "--no-pager", "restart", "fixture"],
        ["hostnamectl", "hostname", "fixture-host"],
        ["sysctl", "-w", "kernel.hostname=fixture-host"],
        ["sysctl", "--sys"],
        ["docker-compose", "up"],
    ],
)
def test_host_administration_cannot_be_silenced_by_project_allow(
    tmp_path, monkeypatch, command
):
    (tmp_path / ".kvist").mkdir()
    (tmp_path / ".kvist" / "permissions.json").write_text(
        json.dumps({"allow": [f"{command[0]} *"], "deny": [], "ask": []}),
        encoding="utf-8",
    )
    requested = []
    executor = TerminalExecutor(tmp_path)
    monkeypatch.setattr(
        TerminalExecutor,
        "_request_user_approval",
        classmethod(lambda cls, argv, reason: requested.append((argv, reason)) or False),
    )
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )

    result = executor.run(command)

    assert result.denied is True
    assert requested and "risk assessment" in requested[0][1].casefold()


def test_no_unvalidated_execution_method_remains():
    assert not hasattr(TerminalExecutor, "_run_unvalidated")


def test_final_execution_entry_requires_an_authorized_context(tmp_path):
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(TypeError, match="expected_context"):
        executor._execute_validated(["echo", "fixture"])
    with pytest.raises(HardDenyError, match="authorization capability"):
        executor._execute_validated(
            ["echo", "fixture"],
            expected_context=None,  # type: ignore[arg-type]
            authorization_token=object(),
        )
    assert not hasattr(executor, "_execute_authorized")


def test_approved_unchanged_script_executes_and_is_hashed(tmp_path):
    script = tmp_path / "safe.py"
    script.write_text("from pathlib import Path\nPath('sentinel').write_text('ok')\n", encoding="utf-8")
    executor = _executor_with_approval(tmp_path)

    result = executor.run(["python", "safe.py"])

    assert result.returncode == 0
    assert (tmp_path / "sentinel").read_text(encoding="utf-8") == "ok"


def test_script_mutation_during_confirmation_invalidates_approval(tmp_path):
    script = tmp_path / "safe.py"
    sentinel = tmp_path / "sentinel"
    script.write_text("print('safe')\n", encoding="utf-8")
    executor = TerminalExecutor(tmp_path)

    def mutate(command, reason):
        script.write_text("from pathlib import Path\nPath('sentinel').write_text('pwned')\n", encoding="utf-8")
        return True

    executor._request_user_approval = mutate
    with pytest.raises(HardDenyError, match="context changed"):
        executor.run(["python", "safe.py"])
    assert not sentinel.exists()


def test_argv_mutation_during_confirmation_is_revalidated(tmp_path):
    (tmp_path / "safe.py").write_text("print('safe')\n", encoding="utf-8")
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("preserve", encoding="utf-8")
    command = ["python", "safe.py"]
    executor = TerminalExecutor(tmp_path)

    def mutate(argv, reason):
        argv[:] = ["find", ".", "-delete"]
        return True

    executor._request_user_approval = mutate
    with pytest.raises(HardDenyError, match="action predicate"):
        executor.run(command)
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_cwd_change_during_confirmation_invalidates_approval(tmp_path):
    original = tmp_path / "one"
    replacement = tmp_path / "two"
    original.mkdir()
    replacement.mkdir()
    (original / "safe.py").write_text("print('one')\n", encoding="utf-8")
    (replacement / "safe.py").write_text("print('two')\n", encoding="utf-8")
    executor = TerminalExecutor(original)

    def mutate(command, reason):
        executor.workspace = replacement.resolve()
        return True

    executor._request_user_approval = mutate
    with pytest.raises(HardDenyError, match="context changed"):
        executor.run(["python", "safe.py"])


def test_cwd_directory_identity_change_invalidates_approval(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "safe.py").write_text("print('fixture')\n", encoding="utf-8")
    executor = TerminalExecutor(tmp_path)

    def replace_directory(command, reason):
        work.rename(tmp_path / "old-work")
        work.mkdir()
        (work / "safe.py").write_text("print('fixture')\n", encoding="utf-8")
        return True

    executor._request_user_approval = replace_directory
    with pytest.raises(HardDenyError, match="context changed"):
        executor.run(["python", "safe.py"], cwd="work")


@pytest.mark.parametrize("property_name", ["sandbox", "network", "environment"])
def test_runtime_context_change_during_confirmation_invalidates_approval(
    tmp_path, monkeypatch, property_name
):
    (tmp_path / "safe.py").write_text("print('safe')\n", encoding="utf-8")
    executor = TerminalExecutor(tmp_path)

    def mutate(command, reason):
        if property_name == "sandbox":
            executor.sandbox_mode = SandboxMode.BUBBLEWRAP
        elif property_name == "network":
            monkeypatch.setenv("AGENT_SANDBOX_NET", "allow")
        else:
            monkeypatch.setenv("LANG", "context-changed.UTF-8")
        return True

    executor._request_user_approval = mutate
    with pytest.raises(HardDenyError, match="context changed"):
        executor.run(["python", "safe.py"])


def test_child_environment_does_not_inherit_parent_secrets(tmp_path, monkeypatch):
    secret_names = [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "CUSTOM_PASSWORD",
    ]
    for name in secret_names:
        monkeypatch.setenv(name, "super-secret")
    monkeypatch.setenv("LANG", "C.UTF-8")
    script = tmp_path / "inspect_env.py"
    script.write_text(
        "import json, os\n"
        f"names = {secret_names!r}\n"
        "print(json.dumps({'secrets': {n: os.getenv(n) for n in names}, "
        "'path': bool(os.getenv('PATH')), 'lang': os.getenv('LANG'), "
        "'home': os.getenv('HOME'), 'tmpdir': os.getenv('TMPDIR'), "
        "'git_no_lazy_fetch': os.getenv('GIT_NO_LAZY_FETCH')}))\n",
        encoding="utf-8",
    )
    executor = _executor_with_approval(tmp_path)

    result = executor.run(["python", "inspect_env.py"])
    observed = json.loads(result.stdout)

    assert observed["secrets"] == {name: None for name in secret_names}
    assert observed["path"] is True
    assert observed["lang"] == "C.UTF-8"
    assert Path(observed["home"]).parent == Path("/tmp")
    assert Path(observed["home"]).name.startswith("kvist-home-")
    assert observed["tmpdir"] == "/tmp"
    assert observed["git_no_lazy_fetch"] == "1"


def test_environment_overrides_cannot_reintroduce_secrets(tmp_path):
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(HardDenyError, match="secret-bearing"):
        executor.run(["echo", "safe"], env_overrides={"GITHUB_TOKEN": "secret"})


def test_child_home_is_private_empty_and_bound_across_approval(tmp_path, monkeypatch):
    script = tmp_path / "safe.py"
    script.write_text("print('fixture')\n", encoding="utf-8")
    executor = TerminalExecutor(tmp_path)
    initial_env = executor._build_env(None)
    home = Path(initial_env["HOME"])
    assert home.parent == Path("/tmp")
    assert home.name.startswith("kvist-home-")
    assert home.stat().st_mode & 0o077 == 0
    assert list(home.iterdir()) == []

    def contaminate_home(command, reason):
        executor._child_home.joinpath("startup.pth").write_text(
            "harmless fixture\n", encoding="utf-8"
        )
        return True

    executor._request_user_approval = contaminate_home
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match="child HOME is not empty"):
        executor.run(["python", "safe.py"])


def test_explicit_custom_environment_allow_mechanism(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_CHILD_ENV_ALLOW", "PROJECT_MODE")
    executor = TerminalExecutor(tmp_path)
    env = executor._build_env({"PROJECT_MODE": "test"})
    assert env["PROJECT_MODE"] == "test"
    with pytest.raises(HardDenyError, match="not in AGENT_CHILD_ENV_ALLOW"):
        executor._build_env({"UNLISTED_PROJECT_MODE": "test"})


@pytest.mark.parametrize(
    "name",
    [
        "GCONV_PATH",
        "JAVA_TOOL_OPTIONS",
        "_JAVA_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "CLASSPATH",
        "DOTNET_STARTUP_HOOKS",
        "PHPRC",
        "RUSTFLAGS",
        "ZDOTDIR",
        "RIPGREP_CONFIG_PATH",
        "AWKPATH",
        "AWKLIBPATH",
        "MAKEFILES",
        "LESSOPEN",
        "CUSTOM_CREDENTIALFILE",
    ],
)
def test_explicit_env_allow_cannot_enable_loader_or_secret_controls(tmp_path, monkeypatch, name):
    monkeypatch.setenv("AGENT_CHILD_ENV_ALLOW", name)
    monkeypatch.setenv(name, "fixture")
    executor = TerminalExecutor(tmp_path)
    assert name not in executor._build_env(None)
    with pytest.raises(HardDenyError, match="secret-bearing|execution-controlling"):
        executor._build_env({name: "fixture"})


def test_node_version_workspace_file_is_not_silently_executed(tmp_path, monkeypatch):
    sentinel = tmp_path / "sentinel"
    (tmp_path / "version").write_text(
        "require('fs').writeFileSync('sentinel', 'pwned')\n", encoding="utf-8"
    )
    executor = TerminalExecutor(tmp_path)
    requested = []
    monkeypatch.setattr(
        TerminalExecutor,
        "_request_user_approval",
        classmethod(lambda cls, command, reason: requested.append((command, reason)) or False),
    )

    result = executor.run(["node", "version"])

    assert result.denied is True
    assert requested
    assert not sentinel.exists()


def test_sensitive_argument_symlink_is_hard_denied_without_prompt_or_spawn(tmp_path, monkeypatch):
    protected = tmp_path / ".ssh"
    protected.mkdir()
    (protected / "fixture").write_text("not a real secret", encoding="utf-8")
    (tmp_path / "alias").symlink_to(protected / "fixture")
    approvals = []
    executor = TerminalExecutor(tmp_path)
    monkeypatch.setattr(
        TerminalExecutor,
        "_request_user_approval",
        classmethod(lambda cls, command, reason: approvals.append((command, reason)) or True),
    )
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )

    with pytest.raises(HardDenyError, match="secret"):
        executor.run(["cat", "alias"])
    assert approvals == []


def test_sensitive_cwd_is_hard_denied_without_prompt_or_spawn(tmp_path, monkeypatch):
    (tmp_path / ".ssh").mkdir()
    approvals = []
    executor = TerminalExecutor(tmp_path)
    monkeypatch.setattr(
        TerminalExecutor,
        "_request_user_approval",
        classmethod(lambda cls, command, reason: approvals.append((command, reason)) or True),
    )
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match="working directory"):
        executor.run(["cat", "fixture"], cwd=".ssh")
    assert approvals == []


def test_direct_script_symlink_to_sensitive_target_is_hard_denied(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    target = tmp_path / "config" / ".env"
    target.write_text("fixture only", encoding="utf-8")
    (tmp_path / "safe.py").symlink_to(target)
    executor = TerminalExecutor(tmp_path)
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match="secret"):
        executor.run(["python", "safe.py"])


@pytest.mark.parametrize("kind", ["outside-package", "internal-cmake"])
def test_implicit_build_file_symlink_cannot_escape_hash_boundary(tmp_path, monkeypatch, kind):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    if kind == "outside-package":
        outside = tmp_path / "outside-package.json"
        outside.write_text("{}\n", encoding="utf-8")
        (workspace / "package.json").symlink_to(outside)
        command = ["npm", "run", "build"]
        expected = "outside the workspace"
    else:
        internal = workspace / ".kvist"
        internal.mkdir()
        target = internal / "fixture.txt"
        target.write_text("fixture\n", encoding="utf-8")
        source = workspace / "src"
        source.mkdir()
        (source / "CMakeLists.txt").symlink_to(target)
        command = ["cmake", "-Ssrc"]
        expected = "internal"
    executor = TerminalExecutor(workspace)
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match=expected):
        executor.run(command)


@pytest.mark.parametrize(
    "command,control_file",
    [
        (["make", "-C", "sub"], "sub/Makefile"),
        (["make", "-fFirst.mk", "-f", "Second.mk"], "Second.mk"),
        (["cmake", "-Ssrc"], "src/CMakeLists.txt"),
        (["ninja", "-Cbuild", "-fRules.ninja"], "build/Rules.ninja"),
        (["npm", "--prefix", "web", "run", "build"], "web/package.json"),
        (["go", "run", "main.go"], "main.go"),
        (["powershell", "-File", "safe.ps1"], "safe.ps1"),
        (["cmd", "/c", "safe.cmd"], "safe.cmd"),
        (["sed", "-nfscript.sed", "input.txt"], "script.sed"),
    ],
)
def test_selected_execution_input_mutation_invalidates_approval(
    tmp_path, monkeypatch, command, control_file
):
    path = tmp_path / control_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("first harmless fixture\n", encoding="utf-8")
    if command[0] == "make" and "First.mk" in command:
        (tmp_path / "First.mk").write_text("first make fixture\n", encoding="utf-8")
    executor = TerminalExecutor(tmp_path)

    def mutate(argv, reason):
        path.write_text("second harmless fixture\n", encoding="utf-8")
        return True

    executor._request_user_approval = mutate
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match="context changed"):
        executor.run(command)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_read_operations_do_not_launch_repository_helpers_or_index_hooks(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    setup_env = {
        "PATH": os.defpath,
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "LANG": "C",
    }

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            env=setup_env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    git("init", "--template=")
    (tmp_path / ".gitattributes").write_text("*.fixture diff=fixture\n", encoding="utf-8")
    sample = tmp_path / "sample.fixture"
    sample.write_text("before\n", encoding="utf-8")
    git("add", ".gitattributes", "sample.fixture")
    git(
        "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--no-gpg-sign", "-m", "initial",
    )

    textconv_sentinel = tmp_path / "textconv-sentinel"
    helper = tmp_path / "textconv-helper.sh"
    helper.write_text(
        "#!/bin/sh\ntouch textconv-sentinel\ncat \"$1\"\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    git("config", "diff.fixture.textconv", "./textconv-helper.sh")

    hook_sentinel = tmp_path / "status-hook-sentinel"
    hook = tmp_path / ".git" / "hooks" / "post-index-change"
    hook.parent.mkdir()
    hook.write_text("#!/bin/sh\ntouch status-hook-sentinel\n", encoding="utf-8")
    hook.chmod(0o755)
    sample.write_text("after\n", encoding="utf-8")

    executor = TerminalExecutor(tmp_path)
    executor._request_user_approval = lambda command, reason: (_ for _ in ()).throw(
        AssertionError("hardened Git reads must remain non-interactive")
    )
    diff_result = executor.run(["git", "diff"])
    status_result = executor.run(["git", "status", "--short"])

    assert diff_result.returncode == 0
    assert status_result.returncode == 0
    assert not textconv_sentinel.exists()
    assert not hook_sentinel.exists()

    git("config", "filter.fixture.process", "./textconv-helper.sh")
    (tmp_path / ".gitattributes").write_text(
        "*.fixture diff=fixture filter=fixture\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess reached")),
    )
    with pytest.raises(HardDenyError, match="worktree filters"):
        executor.run(["git", "status", "--short"])
    assert not textconv_sentinel.exists()
