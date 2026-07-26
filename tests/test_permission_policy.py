from __future__ import annotations

import concurrent.futures
import json
import stat
import threading

import pytest

from permission_policy import (
    PermissionPolicy,
    append_allow_rule,
    load_policy,
    policy_file_path,
)
from terminal import HardDenyError, TerminalError, TerminalExecutor, TerminalResult


def write_policy(workspace, allow=(), deny=(), ask=()):
    path = policy_file_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"allow": list(allow), "deny": list(deny), "ask": list(ask)}),
        encoding="utf-8",
    )


def stub_execution(monkeypatch):
    """Replace the final validated executor so no real process is spawned."""
    executed: list = []

    def fake_run(
        self,
        command,
        cwd=None,
        env_overrides=None,
        timeout=None,
        role="coder",
        *,
        expected_context=None,
        authorization_token=None,
    ):
        executed.append(list(command))
        return TerminalResult(command=command, returncode=0, stdout="ran", stderr="")

    monkeypatch.setattr(TerminalExecutor, "_execute_validated", fake_run)
    return executed


def forbid_approval(monkeypatch):
    def raise_on_prompt(cls, command, reason):
        raise AssertionError(f"HITL must not be triggered for {command}: {reason}")

    monkeypatch.setattr(
        TerminalExecutor, "_request_user_approval", classmethod(raise_on_prompt)
    )


def deny_approval(monkeypatch):
    requested: list = []

    def record(cls, command, reason):
        requested.append((command, reason))
        return False

    monkeypatch.setattr(
        TerminalExecutor,
        "_request_user_approval",
        classmethod(lambda cls, c, r: record(cls, c, r)),
    )
    return requested


# ── Pattern matching ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "pattern,command,expected",
    [
        ("git push", ["git", "push"], True),
        ("git push", ["git", "push", "origin", "main"], True),
        ("git push", ["git", "pull"], False),
        ("python *", ["python", "script.py"], True),
        ("python *", ["python"], True),
        ("docker compose up", ["docker", "compose", "up", "-d"], True),
        ("docker compose up", ["docker", "compose", "down"], False),
        ("pip install *", ["pip", "install", "requests"], True),
        ("git push --force", ["git", "push"], False),
    ],
)
def test_policy_pattern_matching(pattern, command, expected):
    policy = PermissionPolicy(allow=(pattern,))
    assert (policy.decide(command) == "allow") is expected


def test_policy_priority_deny_over_ask_over_allow():
    policy = PermissionPolicy(allow=("git *",), deny=("git push",), ask=("git commit",))
    assert policy.decide(["git", "push", "origin"]) == "deny"
    assert policy.decide(["git", "commit", "-m", "x"]) == "ask"
    assert policy.decide(["git", "status"]) == "allow"


# ── Loading ──────────────────────────────────────────────────────────

def test_load_policy_missing_file_is_empty(tmp_path):
    policy, note = load_policy(tmp_path)
    assert policy == PermissionPolicy()
    assert note == ""


def test_load_policy_invalid_json_is_empty_with_note(tmp_path):
    path = policy_file_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")

    policy, note = load_policy(tmp_path)

    assert policy == PermissionPolicy()
    assert "Invalid JSON" in note


def test_append_allow_rule_creates_and_deduplicates(tmp_path):
    append_allow_rule(tmp_path, "git push")
    append_allow_rule(tmp_path, "git push")
    append_allow_rule(tmp_path, "python *")

    policy, note = load_policy(tmp_path)
    assert note == ""
    assert policy.allow == ("git push", "python *")


def test_permission_policy_is_private_and_recovers_previous_generation(
    tmp_path,
):
    append_allow_rule(tmp_path, "git status")
    append_allow_rule(tmp_path, "python *")
    path = policy_file_path(tmp_path)
    path.write_text("{partial", encoding="utf-8")

    policy, note = load_policy(tmp_path)

    assert note == ""
    assert policy.allow == ("git status", "python *")
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_concurrent_allow_updates_do_not_lose_a_rule(tmp_path, monkeypatch):
    import permission_policy

    write_barrier = threading.Barrier(2)
    original_write = getattr(permission_policy, "write_json_object", None)

    def synchronized_write(*args, **kwargs):
        write_barrier.wait(timeout=5)
        assert original_write is not None
        return original_write(*args, **kwargs)

    if original_write is not None:
        monkeypatch.setattr(
            permission_policy,
            "write_json_object",
            synchronized_write,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(append_allow_rule, tmp_path, pattern)
            for pattern in ("git status", "python *")
        ]
        for future in futures:
            future.result(timeout=5)

    policy, note = load_policy(tmp_path)
    assert note == ""
    assert set(policy.allow) == {"git status", "python *"}


# ── Integration with TerminalExecutor.run ────────────────────────────

def test_allow_rule_skips_hitl_for_moderate_command(
    tmp_path,
    monkeypatch,
    trusted_python_name,
):
    write_policy(tmp_path, allow=[f"{trusted_python_name} *"])
    executor = TerminalExecutor(tmp_path)
    executed = stub_execution(monkeypatch)
    forbid_approval(monkeypatch)

    result = executor.run([trusted_python_name, "script.py"])

    assert result.returncode == 0
    assert executed == [[trusted_python_name, "script.py"]]


def test_allow_rule_covers_command_missing_from_builtin_allowlist(tmp_path, monkeypatch):
    """An eligible moderate custom command can be project-allowed."""
    write_policy(tmp_path, allow=["custom-tool inspect"])
    executor = TerminalExecutor(tmp_path)
    executed = stub_execution(monkeypatch)
    forbid_approval(monkeypatch)

    result = executor.run(["custom-tool", "inspect", "target"])

    assert result.returncode == 0
    assert executed == [["custom-tool", "inspect", "target"]]


def test_without_allow_rule_moderate_command_goes_to_hitl(tmp_path, monkeypatch):
    executor = TerminalExecutor(tmp_path)
    requested = deny_approval(monkeypatch)

    result = executor.run(["python", "script.py"])

    assert requested
    assert result.denied is True


def test_deny_rule_blocks_without_prompt_even_with_allow(tmp_path, monkeypatch):
    write_policy(tmp_path, allow=["git *"], deny=["git push"])
    executor = TerminalExecutor(tmp_path)
    forbid_approval(monkeypatch)

    with pytest.raises(HardDenyError, match="deny rule"):
        executor.run(["git", "push", "origin", "main"])


def test_deny_rule_applies_to_canonical_path_qualified_executable(tmp_path, monkeypatch):
    write_policy(tmp_path, deny=["cat *"])
    executor = TerminalExecutor(tmp_path)
    requested = deny_approval(monkeypatch)
    with pytest.raises(HardDenyError, match="deny rule"):
        executor.run(["/usr/bin/cat", "fixture"])
    assert requested == []


def test_ask_rule_applies_to_canonical_path_qualified_executable(tmp_path, monkeypatch):
    write_policy(tmp_path, ask=["cat *"])
    executor = TerminalExecutor(tmp_path)
    requested = deny_approval(monkeypatch)
    result = executor.run(["/usr/bin/cat", "fixture"])
    assert result.denied is True
    assert requested and "ask rule" in requested[0][1]


def test_ask_rule_forces_hitl_for_safe_command(tmp_path, monkeypatch):
    write_policy(tmp_path, ask=["git status"])
    executor = TerminalExecutor(tmp_path)
    requested = deny_approval(monkeypatch)

    result = executor.run(["git", "status"])

    assert requested and "ask rule" in requested[0][1]
    assert result.denied is True


def test_allow_rule_cannot_override_forbidden_executable(tmp_path, monkeypatch):
    write_policy(tmp_path, allow=["rm *", "sudo *"])
    executor = TerminalExecutor(tmp_path)
    requested = deny_approval(monkeypatch)
    with pytest.raises(HardDenyError, match="forbidden"):
        executor.run(["rm", "-rf", "dir"])
    assert requested == []


def test_allow_rule_cannot_override_secret_guard(tmp_path, monkeypatch):
    write_policy(tmp_path, allow=["cat *"])
    executor = TerminalExecutor(tmp_path)
    requested = deny_approval(monkeypatch)

    with pytest.raises(HardDenyError, match="secret files"):
        executor.run(["cat", ".ssh/id_rsa"])
    assert requested == []


def test_allow_rule_cannot_override_find_action_guard(tmp_path, monkeypatch):
    write_policy(tmp_path, allow=["find *"])
    executor = TerminalExecutor(tmp_path)
    requested = deny_approval(monkeypatch)

    with pytest.raises(HardDenyError, match="action predicate"):
        executor.run(["find", ".", "-delete"])
    assert requested == []


def test_pipe_requires_allow_for_every_segment(tmp_path, monkeypatch):
    write_policy(tmp_path, allow=["cat *"])
    executor = TerminalExecutor(tmp_path)
    requested = deny_approval(monkeypatch)

    result = executor.run(["cat", "x.txt", "|", "python", "filter.py"])

    assert requested
    assert result.denied is True


def test_missing_policy_file_keeps_current_behavior(tmp_path, monkeypatch):
    """Backward compatibility: no policy file → plain risk gradation."""
    executor = TerminalExecutor(tmp_path)
    forbid_approval(monkeypatch)

    result = executor.run(["git", "status"])
    assert result.denied is False


@pytest.mark.parametrize("force_ask", [False, True])
def test_policy_change_before_spawn_invalidates_silent_allow(
    tmp_path,
    monkeypatch,
    trusted_python_name,
    force_ask,
):
    pattern = f"{trusted_python_name} *"
    write_policy(tmp_path, allow=[pattern])
    (tmp_path / "script.py").write_text("print('fixture')\n", encoding="utf-8")
    executor = TerminalExecutor(tmp_path)
    original = executor._execute_validated

    def mutate_policy_then_execute(*args, **kwargs):
        write_policy(
            tmp_path,
            allow=[],
            deny=[],
            ask=[pattern] if force_ask else [],
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(executor, "_execute_validated", mutate_policy_then_execute)
    with pytest.raises(HardDenyError, match="context changed"):
        executor.run([trusted_python_name, "script.py"])


def test_auto_fallback_policy_tamper_cannot_suppress_per_run_approval(
    tmp_path,
    monkeypatch,
    trusted_python_name,
):
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "auto")
    monkeypatch.setattr("sandbox.resolve_bubblewrap_path", lambda: None)
    monkeypatch.setattr("terminal.resolve_bubblewrap_path", lambda: None)
    pattern = f"{trusted_python_name} *"
    write_policy(tmp_path)
    (tmp_path / "script.py").write_text("print('fixture')\n", encoding="utf-8")
    executor = TerminalExecutor(tmp_path)
    executor._cgroup_available = True
    requested = []
    executions = 0

    def approve(cls, command, reason):
        requested.append(reason)
        return True

    def mutate_policy_on_first_execution(
        command,
        cwd=None,
        env_overrides=None,
        timeout=None,
        role="coder",
        *,
        expected_context,
        authorization_token,
    ):
        nonlocal executions
        executions += 1
        if executions == 1:
            write_policy(tmp_path, allow=[pattern])
        return TerminalResult(
            command=command,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(
        TerminalExecutor,
        "_request_user_approval",
        classmethod(approve),
    )
    monkeypatch.setattr(
        executor,
        "_execute_validated",
        mutate_policy_on_first_execution,
    )

    executor.run([trusted_python_name, "script.py"])
    executor.run([trusted_python_name, "script.py"])

    assert executions == 2
    assert len(requested) == 2
    assert all("no Bubblewrap" in reason for reason in requested)


def test_dangerous_allow_rule_never_silences_or_persists(tmp_path, monkeypatch):
    write_policy(tmp_path, allow=["docker run *"])
    executor = TerminalExecutor(tmp_path)
    requested = deny_approval(monkeypatch)
    monkeypatch.setattr(
        TerminalExecutor,
        "_offer_allow_rule",
        lambda self, command: (_ for _ in ()).throw(AssertionError("dangerous rule offered")),
    )

    result = executor.run(
        ["docker", "run", "--privileged=true", "--volume=/:/host", "alpine"]
    )

    assert requested and "dangerous" in requested[0][1].casefold()
    assert result.denied is True


def test_hard_denied_pipeline_segment_never_prompts(tmp_path, monkeypatch):
    write_policy(tmp_path, allow=["cat *", "find *"])
    executor = TerminalExecutor(tmp_path)
    requested = deny_approval(monkeypatch)
    with pytest.raises(HardDenyError, match="action predicate"):
        executor.run(["cat", "input", "|", "find", ".", "-delete"])
    assert requested == []


def test_malformed_rule_field_fails_closed_with_note(tmp_path):
    path = policy_file_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"allow": [], "deny": "rm *", "ask": []}', encoding="utf-8")

    policy, note = load_policy(tmp_path)

    assert policy == PermissionPolicy()
    assert "field 'deny'" in note
    with pytest.raises(ValueError, match="deny"):
        append_allow_rule(tmp_path, "git status")


def test_permission_file_symlink_is_rejected(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside-policy"
    outside.mkdir()
    (tmp_path / ".kvist").symlink_to(outside, target_is_directory=True)

    policy, note = load_policy(tmp_path)

    assert policy == PermissionPolicy()
    assert "symbolic links" in note
    with pytest.raises(ValueError, match="symbolic links"):
        append_allow_rule(tmp_path, "git status")
