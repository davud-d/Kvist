# -*- coding: utf-8 -*-
"""Cross-platform terminal module for safe process execution.

TerminalExecutor lives here; the types, rules, validation and executable
binding it composes are in kernel/terminal_*.py. Names that other modules
import from kernel.terminal are re-exported below.
"""
from __future__ import annotations

import functools
import os
import shlex
import shutil  # noqa: F401  re-exported: tests patch shutil.which through this module
import stat
import subprocess  # noqa: F401  spawn boundary lives in process_tree; kept as the patch target
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable

from kernel.command_policy import (
    CommandAssessment,
    RiskLevel,
    assess_command,
    canonical_executable_name,
    is_exact_version_probe,
)
from kernel.approvals import ApprovalGate, TerminalApprovalGate
from kernel.permission_policy import append_allow_rule, load_policy
from kernel.process_tree import (
    ProcessSupervisionError,
    cgroup_supervision_available,
    run_supervised,
)
from kernel.redaction import redact_text, sanitize_display_text
from kernel.sandbox import (
    SandboxMode,
    detect_sandbox_mode,
    normalize_sandbox_request,
    resolve_bubblewrap_path,
    sandbox_network_allowed,
    wrap_command_for_sandbox,
)
from kernel.secret_paths import UnsafeSecretPathError, enumerate_secret_targets
from kernel.self_info import ancestor_chain, detect_host
from kernel.terminal_bindings import ExecutableBindingMixin
from kernel.terminal_env import (
    _ENV_NAME_RE,
    _SAFE_CHILD_ENV_NAMES,
    _controlled_child_path,
    _explicit_child_env_names,
    _external_tool_paths,
    _is_blocked_child_env_name,
    _validate_windows_private_directory,
)
from kernel.terminal_git import GitValidationMixin
from kernel.terminal_hitl import _alt_screen, _enter_hitl, _exit_hitl, is_hitl_active  # noqa: F401  is_hitl_active is re-exported
from kernel.terminal_interpreters import InterpreterValidationMixin
from kernel.terminal_limits import (
    _MAX_COMMAND_CHARS,
    _MAX_COMMAND_ITEMS,
    _TIMEOUT_CEILINGS,
    ResourceLimits,  # noqa: F401  re-exported
    _unix_resource_limits,
    resolve_resource_limits,
)
from kernel.terminal_rules import (
    _COMMON_ALLOWED,
    _DEV_COMMON_ALLOWED,
    _DEV_UNIX_ALLOWED,
    _DEV_WINDOWS_ALLOWED,
    _FALLBACK_READ_ONLY_EXECUTABLES,
    _FORBIDDEN_EXECUTABLES,
    _RESTRICTED_EXTRA_FORBIDDEN,
    _TERMINAL_PROFILES,
    _UNIX_ALLOWED,
    _WINDOWS_ALLOWED,
)
from kernel.terminal_trust import (
    _is_trusted_system_executable,  # noqa: F401  re-exported
    _parse_shebang,  # noqa: F401  re-exported
    venv_interpreter_links,
    venv_interpreter_roots,
)
from kernel.terminal_types import (
    ApprovalRequiredError,  # noqa: F401  re-exported
    CommandNotAllowedError,
    ExecutableBinding,
    ExecutionContext,
    HardDenyError,
    TerminalError,
    TerminalResult,
)
from kernel.terminal_validation import ArgumentValidationMixin


# Sandbox note is printed at most once per process to avoid log spam
# when many TerminalExecutor instances are created (tests, delegation).
_sandbox_note_shown = False


class TerminalExecutor(
    ArgumentValidationMixin,
    GitValidationMixin,
    InterpreterValidationMixin,
    ExecutableBindingMixin,
):
    """Cross-platform safe process execution within workspace.

    Capabilities:
    - Command allowlists with platform profiles
    - Argument validation without false positives
    - Blocking dangerous commands and shell operators
    - Working directory strictly within workspace
    - Env overrides for child processes
    - Configurable timeout and stdout/stderr limits
    - Resource limits on Unix (CPU, memory, files)
    - CREATE_NO_WINDOW on Windows
    """

    # Human-in-the-Loop callbacks — set by SafeExecutor at init time.
    _hitl_suspend_fn: Callable[[], None] | None = None
    _hitl_resume_fn: Callable[[], None] | None = None

    @classmethod
    def set_hitl_callbacks(
        cls,
        suspend_fn: Callable[[], None],
        resume_fn: Callable[[], None],
    ) -> None:
        """Register UI pause/resume hooks for HITL prompts."""
        cls._hitl_suspend_fn = suspend_fn
        cls._hitl_resume_fn = resume_fn

    def __init__(
        self,
        workspace: Path,
        timeout: float = 15.0,
        max_output_chars: int = 12000,
        profile: str = "dev",
        extra_allowed: dict[str, set[str] | None] | None = None,
        extra_forbidden: set[str] | None = None,
        approvals: ApprovalGate | None = None,
    ) -> None:
        self._approvals = approvals or TerminalApprovalGate()
        # Set by the caller around one command to watch its output live; the
        # captured, bounded result is unaffected.
        self.output_sink: Callable[[str], None] | None = None
        self.workspace = workspace.resolve()
        self.max_output_chars = max(1000, min(max_output_chars, 200_000))
        self._is_windows = os.name == "nt"
        normalized_profile = (profile or "dev").strip().lower()
        if normalized_profile not in _TERMINAL_PROFILES:
            normalized_profile = "dev"
        self.profile = normalized_profile
        self._timeout_ceiling = _TIMEOUT_CEILINGS[self.profile]
        self.timeout = max(1.0, min(timeout, self._timeout_ceiling))
        self._resource_limits = resolve_resource_limits(self.profile)
        self._authorization_lock = threading.Lock()
        self._authorized_context_ids: set[int] = set()
        self._run_lock = threading.RLock()
        self._child_home_manager: tempfile.TemporaryDirectory[str] | None = None
        self._child_home = Path()
        self._rotate_child_home()

        # Project permission policy (.kvist/permissions.json). A broken
        # file yields an empty policy plus a note surfaced by the UI layer.
        self._policy, self._policy_note = load_policy(self.workspace)

        # Build allowlist: common + platform-specific + user-provided
        self._allowed = self._build_allowed_commands()
        if extra_allowed:
            self._allowed.update(extra_allowed)

        # Build forbidden set
        self._forbidden: set[str] = set(_FORBIDDEN_EXECUTABLES)
        if self.profile == "restricted":
            self._forbidden.update(_RESTRICTED_EXTRA_FORBIDDEN)
        if extra_forbidden:
            self._forbidden.update(extra_forbidden)

        # Capture requested/effective sandbox state.  Auto selects bwrap when
        # available; its no-bwrap fallback gets stricter approval semantics.
        global _sandbox_note_shown
        self.sandbox_request = normalize_sandbox_request()
        self.sandbox_mode, self._sandbox_note = detect_sandbox_mode()
        self._bwrap_path = resolve_bubblewrap_path() if self.sandbox_mode is SandboxMode.BUBBLEWRAP else None
        self._sandbox_required_unavailable = (
            self.sandbox_request == "bubblewrap" and self.sandbox_mode is not SandboxMode.BUBBLEWRAP
        )
        self._automatic_restricted_fallback = (
            self.sandbox_request in {"auto", "invalid"}
            and self.sandbox_mode is SandboxMode.WORKSPACE_ONLY
        )
        self._external_tool_paths = _external_tool_paths(self.workspace)
        # The processes Kvist runs inside, fixed for the life of this process.
        self.host_chain = ancestor_chain()
        self.host = detect_host(self.host_chain)
        # Process-tree teardown of detached (setsid) descendants needs either
        # the Bubblewrap PID namespace or a cgroup v2 scope.
        self._cgroup_available = cgroup_supervision_available()
        self._tree_control_available = (
            self.sandbox_mode is SandboxMode.BUBBLEWRAP or self._cgroup_available
        )
        if self._sandbox_note and _sandbox_note_shown:
            self._sandbox_note = ""
        elif self._sandbox_note:
            _sandbox_note_shown = True

    @property
    def allowed_commands(self) -> list[str]:
        """Sorted list of allowed commands."""
        return sorted(self._allowed)

    def run(
        self,
        command: list[str],
        cwd: str | None = None,
        env_overrides: dict[str, str] | None = None,
        timeout: float | None = None,
        role: str = "coder",
    ) -> TerminalResult:
        """Execute one serialized run with a fresh, private child HOME."""
        with self._run_lock:
            self._rotate_child_home()
            return self._run_once(command, cwd, env_overrides, timeout, role)

    def _run_once(
        self,
        command: list[str],
        cwd: str | None = None,
        env_overrides: dict[str, str] | None = None,
        timeout: float | None = None,
        role: str = "coder",
    ) -> TerminalResult:
        """Execute through the non-bypassable validation and approval pipeline."""
        if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
            raise HardDenyError("Hard security denial: command must be a non-empty list of strings")
        if (
            len(command) > _MAX_COMMAND_ITEMS
            or sum(len(item) for item in command) + len(command) - 1
            > _MAX_COMMAND_CHARS
        ):
            raise HardDenyError(
                "Hard security denial: command is too large for safe validation and approval"
            )
        # Own the mutable argv so callers cannot race our checks by retaining
        # and mutating their list from another thread.
        command = list(command)
        role = role if role in {"coder", "devops", "chat", "universal"} else "coder"
        self._refresh_policy()

        # Project deny has first precedence and is itself non-approvable.
        decision = self._policy_decision(command)
        if decision == "deny":
            raise HardDenyError(
                "Hard security denial: command blocked by the project permission policy (deny rule)"
            )
        # Before any approval dialog: approving this would cut the turn off
        # mid-step, so the step goes to the person, never to a yes/no prompt.
        self._deny_self_termination(command)

        work_dir = self._resolve_cwd(cwd)
        if self._sandbox_required_unavailable:
            raise HardDenyError(
                "Hard security denial: mandatory Bubblewrap sandbox is unavailable"
            )
        environment = self._build_env(env_overrides)
        bindings = self._resolve_executable_bindings(command, work_dir, environment)
        if (
            self._automatic_restricted_fallback
            and not self._cgroup_available
            and self._command_needs_arbitrary_code(command, bindings)
        ):
            raise HardDenyError(
                "Hard security denial: automatic fallback cannot control detached "
                "descendants without a Bubblewrap PID namespace or cgroup v2 scope; "
                "arbitrary-code execution is refused"
            )
        self._validate_executable_boundaries(command, work_dir, bindings)
        self._hard_validate(command, work_dir, bindings)
        initial_context = self._build_execution_context(
            command,
            work_dir,
            role,
            environment,
            bindings,
            sandbox_mode=self.sandbox_mode,
            network_allowed=sandbox_network_allowed(),
            bwrap_path=self._bwrap_path,
        )
        assessment = self._assess_risk(command, role)

        allowlist_error: CommandNotAllowedError | None = None
        try:
            self._validate_allowlist(command, bindings)
        except CommandNotAllowedError as error:
            allowlist_error = error

        fallback_approval = self._fallback_requires_approval(command, bindings)
        origin_approval = self._requires_executable_origin_approval(command, bindings)
        authorization_token = object()

        def execute_authorized(expected_context: ExecutionContext) -> TerminalResult:
            """Mint a one-shot spawn capability inside this validated run only."""
            token_id = id(authorization_token)
            with self._authorization_lock:
                self._authorized_context_ids.add(token_id)
            try:
                return self._execute_validated(
                    command,
                    cwd,
                    env_overrides,
                    timeout,
                    role,
                    expected_context=expected_context,
                    authorization_token=authorization_token,
                )
            finally:
                with self._authorization_lock:
                    self._authorized_context_ids.discard(token_id)

        # Intrinsic DANGEROUS always wins over ask/allow and is never
        # persistable as a repository allow rule.
        if assessment.risk is RiskLevel.DANGEROUS:
            return self._approve_or_deny(
                command,
                f"Risk assessment: {assessment.reason}",
                lambda: execute_authorized(initial_context),
                offer_rule=False,
            )

        if decision == "ask":
            return self._approve_or_deny(
                command,
                "Project permission policy requires confirmation (ask rule)",
                lambda: execute_authorized(initial_context),
                offer_rule=False,
            )

        if fallback_approval:
            return self._approve_or_deny(
                command,
                "Automatic sandbox fallback (no Bubblewrap: no secret masking or "
                ".kvist isolation) requires per-execution approval for arbitrary-code "
                "commands",
                lambda: execute_authorized(initial_context),
                offer_rule=False,
            )

        if origin_approval:
            return self._approve_or_deny(
                command,
                "Executable origin is not eligible for trusted-name or project-allow suppression",
                lambda: execute_authorized(initial_context),
                offer_rule=False,
            )

        # A project allow may suppress approval only after hard validation and
        # only for non-dangerous, non-fallback execution.
        if decision == "allow":
            return execute_authorized(initial_context)

        if allowlist_error is not None:
            return self._approve_or_deny(
                command,
                str(allowlist_error),
                lambda: execute_authorized(initial_context),
                offer_rule=False,
            )

        if assessment.requires_approval:
            return self._approve_or_deny(
                command,
                f"Risk assessment: {assessment.reason}",
                lambda: execute_authorized(initial_context),
                offer_rule=assessment.risk is RiskLevel.MODERATE,
            )

        return execute_authorized(initial_context)

    def _approve_or_deny(
        self,
        command: list[str],
        reason: str,
        execute_authorized: Callable[[], TerminalResult],
        offer_rule: bool,
    ) -> TerminalResult:
        """HITL gate: run on approval, denied TerminalResult otherwise.

        ``offer_rule`` — after an approval of a risk-graded command, offer
        to persist a project allow-rule. Never offered for hard validation
        failures: those are non-interactive hard denials.
        """
        if not self._request_user_approval(command, reason):
            return TerminalResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr=(
                    "Error: Command execution denied by user for security reasons. "
                    f"{reason}. Find an alternative way to solve the task without this command."
                ),
                denied=True,
            )
        result = execute_authorized()
        if offer_rule:
            self._offer_allow_rule(command)
        return result

    def _command_needs_arbitrary_code(
        self, command: list[str], bindings: tuple[ExecutableBinding, ...] | None = None
    ) -> bool:
        for index, segment in enumerate(self._split_pipe_segments(command)):
            binding = bindings[index] if bindings is not None else None
            if is_exact_version_probe(segment) and (
                binding is None or binding.trusted_system
            ):
                continue
            if canonical_executable_name(segment[0]) not in _FALLBACK_READ_ONLY_EXECUTABLES:
                return True
        return False

    def _fallback_requires_approval(
        self, command: list[str], bindings: tuple[ExecutableBinding, ...] | None = None
    ) -> bool:
        if not self._automatic_restricted_fallback:
            return False
        for index, segment in enumerate(self._split_pipe_segments(command)):
            binding = bindings[index] if bindings is not None else None
            if is_exact_version_probe(segment) and (
                binding is None or binding.trusted_system
            ):
                continue
            if canonical_executable_name(segment[0]) not in _FALLBACK_READ_ONLY_EXECUTABLES:
                return True
        return False

    def _policy_decision(self, command: list[str]) -> str | None:
        """Worst-per-segment policy decision: deny > ask > allow.

        "allow" requires EVERY pipe segment to match an allow-rule; one
        deny/ask segment decides for the whole pipeline. A malformed pipe
        is left for _validate to report.
        """
        if not command:
            return None
        try:
            segments = self._split_pipe_segments(command)
        except TerminalError:
            return None
        decisions: list[str | None] = []
        for segment in segments:
            normalized = [canonical_executable_name(segment[0]), *segment[1:]]
            candidates = {self._policy.decide(segment), self._policy.decide(normalized)}
            if "deny" in candidates:
                decisions.append("deny")
            elif "ask" in candidates:
                decisions.append("ask")
            elif "allow" in candidates:
                decisions.append("allow")
            else:
                decisions.append(None)
        if "deny" in decisions:
            return "deny"
        if "ask" in decisions:
            return "ask"
        if all(item == "allow" for item in decisions):
            return "allow"
        return None

    def _refresh_policy(self) -> None:
        policy, note = load_policy(self.workspace)
        self._policy = policy
        self._policy_note = note

    def _offer_allow_rule(self, command: list[str]) -> None:
        """After an interactive approval, offer "always allow in this repo"."""
        if not sys.stdin.isatty() or "|" in command:
            return
        pattern = _suggest_allow_pattern(command)
        try:
            accepted = self._approvals.confirm(
                title="Permission policy",
                body=(
                    f"Save allow rule {pattern} to .kvist/permissions.json?\n"
                    "Matching commands will run without approval in this repo."
                ),
                question=f"Always allow '{pattern}' in this repo?",
            )
        except TerminalError:
            return
        if not accepted:
            return
        try:
            append_allow_rule(self.workspace, pattern)
        except (OSError, ValueError) as error:
            print(f"Failed to save allow rule: {error}", file=sys.stderr)
            return
        self._policy, self._policy_note = load_policy(self.workspace)

    def _assess_risk(self, command: list[str], role: str) -> CommandAssessment:
        """Risk-grade a validated command via command_policy.

        Piped commands are assessed per segment so `safe | dangerous` cannot
        hide behind the first executable; the riskiest segment wins.
        """
        worst: CommandAssessment | None = None
        requires_approval = False
        for segment in self._split_pipe_segments(command):
            candidate = assess_command(segment, role=role)
            requires_approval = requires_approval or candidate.requires_approval
            if worst is None or _risk_rank(candidate.risk) > _risk_rank(worst.risk):
                worst = candidate
        assert worst is not None  # _split_pipe_segments rejects empty commands
        if requires_approval and not worst.requires_approval:
            worst = CommandAssessment(
                command=command,
                risk=worst.risk,
                reason=worst.reason,
                requires_approval=True,
            )
        return worst

    def _execute_validated(
        self,
        command: list[str],
        cwd: str | None = None,
        env_overrides: dict[str, str] | None = None,
        timeout: float | None = None,
        role: str = "coder",
        *,
        expected_context: ExecutionContext,
        authorization_token: object,
    ) -> TerminalResult:
        """Repeat hard validation/context construction immediately before spawn."""
        token_id = id(authorization_token)
        with self._authorization_lock:
            if token_id not in self._authorized_context_ids:
                raise HardDenyError(
                    "Hard security denial: final execution lacks a one-shot authorization capability"
                )
            self._authorized_context_ids.remove(token_id)
        command_snapshot = list(command)
        sandbox_mode = self.sandbox_mode
        network_allowed = sandbox_network_allowed()
        bwrap_path = self._bwrap_path
        workspace = self.workspace
        self._refresh_policy()
        if self._policy_decision(command_snapshot) == "deny":
            raise HardDenyError(
                "Hard security denial: command became blocked by a project deny rule before execution"
            )
        work_dir = self._resolve_cwd(cwd)
        effective_timeout = self._clamp_timeout(timeout)
        env = self._build_env(env_overrides)
        bindings = self._resolve_executable_bindings(command_snapshot, work_dir, env)
        self._validate_executable_boundaries(command_snapshot, work_dir, bindings)
        self._hard_validate(command_snapshot, work_dir, bindings)
        try:
            secret_targets = enumerate_secret_targets(workspace)
        except UnsafeSecretPathError as error:
            raise HardDenyError(
                f"Hard security denial: cannot build a complete secret mask: {error}"
            ) from error
        context = self._build_execution_context(
            command_snapshot,
            work_dir,
            role,
            env,
            bindings,
            sandbox_mode=sandbox_mode,
            network_allowed=network_allowed,
            bwrap_path=bwrap_path,
            secret_targets=secret_targets,
        )
        if context != expected_context:
            raise HardDenyError(
                "Hard security denial: approved execution context changed before process creation"
            )
        missing = next(
            (binding.token for binding in bindings[: len(self._split_pipe_segments(command_snapshot))] if not binding.resolved_path),
            None,
        )
        if missing is not None:
            raise TerminalError(f"Executable not found before process creation: {missing}")

        actual_command = self._materialize_executables(command_snapshot, bindings)
        actual_command = self._wrap_for_platform(actual_command, bindings)

        # Auto-detect pipe usage: if command contains "|", run via sh -c.
        # Each non-pipe token is shell-quoted so arguments with spaces or
        # metacharacters cannot be reinterpreted by sh.
        if "|" in actual_command:
            joined = " ".join(
                token if token == "|" else shlex.quote(token)
                for token in actual_command
            )
            shell_binding = bindings[-1]
            if not shell_binding.resolved_path or not shell_binding.trusted_system:
                raise HardDenyError("Hard security denial: trusted pipeline shell is unavailable")
            actual_command = [shell_binding.resolved_path, "-c", joined]

        try:
            actual_command = wrap_command_for_sandbox(
                actual_command,
                sandbox_mode,
                workspace,
                work_dir,
                bwrap_path=bwrap_path,
                network_allowed=network_allowed,
                tool_paths=self._external_tool_paths + venv_interpreter_roots(bindings),
                links=venv_interpreter_links(bindings, workspace),
                secret_targets=secret_targets,
            )
        except ValueError as error:
            raise HardDenyError(f"Hard security denial: {error}") from error

        rlimit_setter = (
            None
            if self._is_windows
            else functools.partial(_unix_resource_limits, self._resource_limits)
        )
        try:
            outcome = run_supervised(
                actual_command,
                cwd=work_dir,
                env=env,
                timeout=effective_timeout,
                max_capture_bytes=self.max_output_chars * 4,
                rlimit_setter=rlimit_setter,
                is_windows=self._is_windows,
                use_cgroup=self._cgroup_available,
                on_output=self.output_sink,
            )
        except FileNotFoundError:
            raise TerminalError(f"Executable not found: {command_snapshot[0]}")
        except ProcessSupervisionError as exc:
            raise TerminalError(f"Process supervision error: {exc}") from exc
        except OSError as exc:
            raise TerminalError(f"Process launch error: {exc}")

        stdout = self._bound_output(outcome.stdout, outcome.stdout_truncated)
        stderr = self._bound_output(outcome.stderr, outcome.stderr_truncated)
        if outcome.timed_out:
            stderr = (
                f"Timeout: command did not complete within {effective_timeout} sec\n"
                + stderr
            )
        return TerminalResult(
            command=command_snapshot,
            returncode=outcome.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=outcome.timed_out,
        )

    def _request_user_approval(self, command: list[str], reason: str) -> bool:
        """Dangerous-command HITL gate, delegated to the injected approval gate.

        Kept as a method with this exact ``(command, reason)`` signature so
        the call site (``self._request_user_approval(command, reason)``) and
        the test patch points remain stable. The default gate reproduces the
        historical terminal prompt and stays fail-closed off a TTY.
        """
        return self._approvals.confirm_command(command, reason)

    def _build_allowed_commands(self) -> dict[str, set[str] | None]:
        allowed: dict[str, set[str] | None] = dict(_COMMON_ALLOWED)
        platform_cmds = _WINDOWS_ALLOWED if self._is_windows else _UNIX_ALLOWED
        allowed.update(platform_cmds)
        if self.profile == "dev":
            allowed.update(_DEV_COMMON_ALLOWED)
            allowed.update(_DEV_WINDOWS_ALLOWED if self._is_windows else _DEV_UNIX_ALLOWED)
        return allowed

    def _clamp_timeout(self, timeout: float | None) -> float:
        if timeout is None:
            return self.timeout
        return max(1.0, min(timeout, self._timeout_ceiling))

    def _build_env(self, overrides: dict[str, str] | None) -> dict[str, str]:
        """Build a child environment from an allowlist; never inherit wholesale."""
        self._validate_child_home()
        env: dict[str, str] = {
            "PATH": _controlled_child_path(self.workspace, self._external_tool_paths),
            "HOME": str(self._child_home),
            "TMPDIR": "/tmp",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "PIP_NO_INPUT": "1",
        }
        explicitly_allowed = _explicit_child_env_names()
        for name, value in os.environ.items():
            upper = name.upper()
            if _is_blocked_child_env_name(upper):
                continue
            if upper in _SAFE_CHILD_ENV_NAMES or upper.startswith("LC_") or upper in explicitly_allowed:
                if redact_text(value) != value:
                    continue
                env[name] = value

        if overrides is None:
            return env
        if not isinstance(overrides, dict):
            raise HardDenyError("Hard security denial: environment overrides must be a string mapping")
        for name, value in overrides.items():
            if not isinstance(name, str) or not _ENV_NAME_RE.fullmatch(name):
                raise HardDenyError("Hard security denial: invalid environment override name")
            if not isinstance(value, str):
                raise HardDenyError(f"Hard security denial: environment override '{name}' is not a string")
            upper = name.upper()
            if _is_blocked_child_env_name(upper) or upper in {"PATH", "HOME", "TMPDIR"}:
                raise HardDenyError(
                    f"Hard security denial: environment override '{name}' is secret-bearing or execution-controlling"
                )
            if upper not in _SAFE_CHILD_ENV_NAMES and not upper.startswith("LC_") and upper not in explicitly_allowed:
                raise HardDenyError(
                    f"Hard security denial: environment override '{name}' is not in AGENT_CHILD_ENV_ALLOW"
                )
            if redact_text(value) != value:
                raise HardDenyError(
                    f"Hard security denial: environment override '{name}' contains credential-bearing data"
                )
            env[name] = value
        return env

    def _rotate_child_home(self) -> None:
        previous = self._child_home_manager
        manager = tempfile.TemporaryDirectory(
            prefix="kvist-home-", ignore_cleanup_errors=True
        )
        path = Path(manager.name)
        try:
            path.chmod(0o700)
        except OSError as error:
            manager.cleanup()
            raise HardDenyError(
                f"Hard security denial: cannot secure child HOME: {error}"
            ) from error
        self._child_home_manager = manager
        self._child_home = path
        if previous is not None:
            previous.cleanup()

    def _validate_child_home(self) -> None:
        try:
            info = self._child_home.lstat()
            entries = list(self._child_home.iterdir())
        except OSError as error:
            raise HardDenyError(
                f"Hard security denial: child HOME is unavailable: {error}"
            ) from error
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise HardDenyError("Hard security denial: child HOME is not a real directory")
        if os.name == "nt":
            _validate_windows_private_directory(self._child_home)
        else:
            if info.st_mode & 0o077:
                raise HardDenyError("Hard security denial: child HOME permissions are not private")
            if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                raise HardDenyError("Hard security denial: child HOME has the wrong owner")
        if entries:
            raise HardDenyError("Hard security denial: child HOME is not empty")

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_output_chars:
            return text
        return (
            text[: self.max_output_chars]
            + f"\n[TRUNCATED: showing first {self.max_output_chars} of {len(text)} characters]"
        )

    def _bound_output(self, data: bytes, byte_truncated: bool) -> str:
        """Decode captured bytes and apply the character display cap.

        ``byte_truncated`` marks output already clipped at the supervisor's
        in-memory byte ceiling; surface that even when the decoded prefix fits
        the character cap so truncation is never silent.
        """
        text = _decode_process_output(data)
        if len(text) > self.max_output_chars:
            return (
                text[: self.max_output_chars]
                + f"\n[TRUNCATED: showing first {self.max_output_chars} of "
                f"{len(text)}+ characters]"
            )
        if byte_truncated:
            return text + "\n[TRUNCATED: output exceeded the capture byte limit]"
        return text


def _decode_process_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _risk_rank(risk: RiskLevel) -> int:
    return {RiskLevel.SAFE: 0, RiskLevel.MODERATE: 1, RiskLevel.DANGEROUS: 2}[risk]


def _suggest_allow_pattern(command: list[str]) -> str:
    """Derive the allow-rule pattern shown in the "always allow" prompt."""
    if len(command) >= 2 and not command[1].startswith("-"):
        return f"{command[0]} {command[1]}"
    return f"{command[0]} *"


def request_user_confirmation(title: str, body: str, question: str) -> bool:
    """Generic Human-in-the-Loop confirmation with Live-TUI pause.

    Shared by the terminal layer and the file-write approval gate in
    SafeExecutor. Raises TerminalError when stdin is not a TTY so
    non-interactive callers get a deterministic failure.
    """
    if not sys.stdin.isatty():
        raise TerminalError("Interactive confirmation required, but stdin is not a TTY")

    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    if TerminalExecutor._hitl_suspend_fn is not None:
        TerminalExecutor._hitl_suspend_fn()
    _enter_hitl()
    try:
        console = Console()
        with _alt_screen(console):
            console.print(
                Panel(
                    Text(sanitize_display_text(body)),
                    border_style="yellow",
                    title=Text(sanitize_display_text(title)),
                    title_align="left",
                    padding=(1, 2),
                )
            )
            try:
                safe_question = sanitize_display_text(question)
                choice = input(f"{safe_question} [y/N]: ").strip().lower()
            except EOFError:
                return False
        return choice in ("y", "yes")
    finally:
        _exit_hitl()
        if TerminalExecutor._hitl_resume_fn is not None:
            TerminalExecutor._hitl_resume_fn()
