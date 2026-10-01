# -*- coding: utf-8 -*-
"""Argument, token and path validation for terminal commands."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from kernel.command_policy import (
    _diff_has_output_option,
    _file_has_compile_option,
    _hostname_is_read_only,
    _sort_has_output_or_program_option,
    _tree_has_output_option,
    _uniq_has_output_operand,
    canonical_executable_name,
    is_exact_version_probe,
    is_find_action_token,
)
from kernel.redaction import redact_text
from kernel.secret_paths import UnsafeSecretPathError, count_workspace_inode_aliases
from kernel.self_info import self_termination_denial
from kernel.terminal_limits import _MAX_COMMAND_CHARS, _MAX_COMMAND_ITEMS
from kernel.terminal_paths import _is_relative_to
from kernel.terminal_rules import (
    _ABSOLUTE_PATH_SAFE_COMMANDS,
    _CMD_DANGEROUS_INLINE_RE,
    _FORBIDDEN_OPERATORS,
    _INTERNAL_TOKEN_RE,
    _POWERSHELL_COMMAND_FLAGS,
    _POWERSHELL_FILE_FLAGS,
    _POWERSHELL_SAFE_FLAGS,
    _POWERSHELL_SAFE_FLAGS_WITH_VALUE,
    _SCRIPT_EXTENSIONS,
    _SECRET_TOKEN_RE,
    _SUBST_RE,
    _WINDOWS_CMD_BUILTINS,
    _WINDOWS_CMD_BUILTIN_RE,
)
from kernel.terminal_types import CommandNotAllowedError, ExecutableBinding, HardDenyError


def _argument_path_fragments(token: str) -> set[str]:
    """Return direct and common attached option/response path candidates."""
    fragments = {token}
    if "=" in token:
        fragments.add(token.partition("=")[2])
    if token.startswith("@") and len(token) > 1:
        fragments.add(token[1:])
    for prefix in ("-f", "-E", "-C", "-S"):
        if token.startswith(prefix) and token != prefix:
            fragments.add(token[len(prefix) :])
    return fragments


def _docker_path_is_risk_graded(tokens: list[str], index: int) -> bool:
    """Host-path Docker options are authorized by DANGEROUS risk, not path hiding."""
    path_flags = {
        "-v", "--volume", "--mount", "--mount-add", "--volumes-from", "--device",
        "--build-context", "--secret", "--ssh", "--output", "-o", "--cache-from",
        "--cache-to", "--file", "-f",
    }
    token = tokens[index]
    flag = token.partition("=")[0]
    if flag in path_flags or (
        token.startswith("-o") and not token.startswith("--")
    ):
        return True
    if token.startswith("-") and not token.startswith("--") and "v" in token[1:]:
        return True
    if index == 0:
        return False
    previous = tokens[index - 1]
    previous_flag = previous.partition("=")[0]
    return previous_flag in path_flags or (
        previous.startswith("-")
        and not previous.startswith("--")
        and "v" in previous[1:]
    )


class ArgumentValidationMixin:
    """Command, token, path and hardlink validation for TerminalExecutor.

    Instance attributes expected from TerminalExecutor.__init__: ``workspace``,
    ``_allowed``, ``_forbidden``, ``host`` and ``host_chain``. Calls
    ``_validate_git_arguments`` from GitValidationMixin and
    ``_validate_interpreter_modes`` from InterpreterValidationMixin.
    """

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, command: list[str], cwd: str | None = None) -> None:
        """Backward-compatible full validator used by tests and callers."""
        work_dir = self._resolve_cwd(cwd)
        self._hard_validate(command, work_dir)
        self._validate_allowlist(command)

    def _hard_validate(
        self,
        command: list[str],
        work_dir: Path,
        bindings: tuple[ExecutableBinding, ...] | None = None,
    ) -> None:
        """Authoritative non-overridable validation for every pipe segment."""
        if not command:
            raise HardDenyError("Hard security denial: Empty command")
        if not command[0].strip():
            raise HardDenyError("Hard security denial: executable token is empty")
        if any("\x00" in token for token in command):
            raise HardDenyError("Hard security denial: NUL bytes are forbidden in command arguments")

        if (
            len(command) > _MAX_COMMAND_ITEMS
            or sum(len(item) for item in command) + len(command) - 1
            > _MAX_COMMAND_CHARS
        ):
            raise HardDenyError(
                "Hard security denial: command is too large for safe validation and approval"
            )

        for index, segment in enumerate(self._split_pipe_segments(command)):
            trusted_probe = bindings is None or bindings[index].trusted_system
            self._hard_validate_single(segment, work_dir, trusted_probe=trusted_probe)

    def _validate_allowlist(
        self,
        command: list[str],
        bindings: tuple[ExecutableBinding, ...] | None = None,
    ) -> None:
        for index, segment in enumerate(self._split_pipe_segments(command)):
            trusted_probe = bindings is None or bindings[index].trusted_system
            if is_exact_version_probe(segment) and trusted_probe:
                continue
            executable = canonical_executable_name(segment[0])
            self._require_allowlisted(segment, executable)

    def _deny_self_termination(self, command: list[str]) -> None:
        work_dir = self.workspace

        def resolve_script(token: str) -> str | None:
            try:
                path = self._resolve_workspace_reference(token, work_dir, "shell script")
                if not path.is_file() or path.stat().st_size > 64 * 1024:
                    return None
                return path.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeError, HardDenyError):
                return None

        for segment in self._split_pipe_segments(command):
            reason = self_termination_denial(
                segment, self.host_chain, self.host, script_resolver=resolve_script
            )
            if reason:
                raise HardDenyError(f"Hard security denial: {reason}")

    def _split_pipe_segments(self, command: list[str]) -> list[list[str]]:
        segments: list[list[str]] = [[]]
        for token in command:
            if token == "|":
                segments.append([])
            else:
                segments[-1].append(token)
        if any(not segment for segment in segments):
            raise HardDenyError("Hard security denial: Empty command segment around '|' is forbidden")
        return segments

    def _hard_validate_single(
        self, command: list[str], work_dir: Path, *, trusted_probe: bool = True
    ) -> None:
        if not command[0].strip():
            raise HardDenyError("Hard security denial: executable token is empty")
        executable = canonical_executable_name(command[0])

        self._validate_common_tokens(
            [command[0]], executable, allow_executable_path=True, work_dir=work_dir
        )

        if executable in self._forbidden and not (
            is_exact_version_probe(command) and trusted_probe
        ):
            raise HardDenyError(
                f"Hard security denial: command '{command[0]}' is unconditionally forbidden"
            )

        if executable in {"bash", "sh", "powershell", "pwsh", "cmd", "cmd.exe"}:
            self._validate_shell_command(command, executable, work_dir)
            return

        if executable == "find":
            self._validate_find_arguments(command[1:])
        if executable == "sort" and _sort_has_output_or_program_option(command[1:]):
            raise HardDenyError("Hard security denial: sort output/external-program modes are forbidden")
        if executable == "tree" and _tree_has_output_option(command[1:]):
            raise HardDenyError("Hard security denial: tree output-file mode is forbidden")
        if executable == "hostname" and not _hostname_is_read_only(command[1:]):
            raise HardDenyError("Hard security denial: hostname mutation is forbidden")
        if executable == "diff" and _diff_has_output_option(command[1:]):
            raise HardDenyError("Hard security denial: diff output-file mode is forbidden")
        if executable == "uniq" and _uniq_has_output_operand(command[1:]):
            raise HardDenyError("Hard security denial: uniq output-file operand is forbidden")
        if executable == "file" and _file_has_compile_option(command[1:]):
            raise HardDenyError("Hard security denial: file magic compilation mode is forbidden")
        if executable == "wc" and any(
            token == "--files0-from" or token.startswith("--files0-from=")
            for token in command[1:]
        ):
            raise HardDenyError(
                "Hard security denial: wc indirect file-list mode is forbidden"
            )
        if executable == "git":
            self._validate_git_arguments(command, work_dir)
        self._validate_common_tokens(command[1:], executable, work_dir=work_dir)
        if executable == "grep":
            self._validate_grep_arguments(command[1:])
        if executable == "rg":
            self._validate_rg_arguments(command, work_dir)
        if executable == "make":
            self._validate_make_argument_clusters(command[1:])
        self._validate_interpreter_modes(command, executable, work_dir)

    def _require_allowlisted(self, command: list[str], executable: str) -> None:
        if executable not in self._allowed:
            raise CommandNotAllowedError(
                f"Command '{command[0]}' is not allowed. "
                f"Allowed: {', '.join(sorted(self._allowed))}"
            )

        # Subcommand validation (for git and similar)
        allowed_sub = self._allowed.get(executable)
        if allowed_sub is not None and len(command) > 1:
            sub = command[1].lstrip("-").casefold()
            if sub and sub not in allowed_sub:
                raise CommandNotAllowedError(
                    f"Subcommand '{command[1]}' is not allowed for '{command[0]}'"
                )

    def _validate_find_arguments(self, args: list[str]) -> None:
        for token in args:
            if is_find_action_token(token):
                raise HardDenyError(
                    f"Hard security denial: find action predicate '{token}' is forbidden: "
                    "it deletes files or executes commands"
                )

    def _validate_make_argument_clusters(self, args: list[str]) -> None:
        """Reject Make syntax that can hide execution inputs or inline code."""
        options_with_values = {"C", "E", "f", "I", "j", "l", "o", "W"}
        canonical_long_inputs = {"--directory", "--file", "--makefile"}
        assignment_operators = (":::=", "::=", ":=", "+=", "?=", "!=", "=")
        makefile_options = {"-f", "--file", "--makefile"}
        for index, token in enumerate(args):
            flag = token.partition("=")[0]
            stripped = token.lstrip()
            makefiles_suffix = (
                stripped[len("MAKEFILES") :].lstrip()
                if stripped.startswith("MAKEFILES")
                else ""
            )
            if makefiles_suffix and makefiles_suffix.startswith(assignment_operators):
                raise HardDenyError(
                    "Hard security denial: command-line MAKEFILES inputs are forbidden"
                )
            if (
                token == "-f-"
                or token in {"--file=-", "--makefile=-"}
                or (
                    token in makefile_options
                    and index + 1 < len(args)
                    and args[index + 1] == "-"
                )
            ):
                raise HardDenyError(
                    "Hard security denial: Makefile input from stdin is forbidden"
                )
            if flag != "--" and flag.startswith("--"):
                if "--temp-stdin".startswith(flag):
                    raise HardDenyError(
                        "Hard security denial: Make temporary-stdin input is forbidden"
                    )
                if "--eval".startswith(flag):
                    raise HardDenyError(
                        "Hard security denial: Make inline eval is forbidden"
                    )
                if any(
                    option.startswith(flag) and flag != option
                    for option in canonical_long_inputs
                ):
                    raise HardDenyError(
                        f"Hard security denial: abbreviated Make input option '{flag}' is forbidden"
                    )
                continue
            if token == "-E":
                raise HardDenyError(
                    "Hard security denial: Make inline eval is forbidden"
                )
            if not token.startswith("-") or token.startswith("--") or len(token) <= 2:
                continue
            for position, option in enumerate(token[1:]):
                if option == "E":
                    raise HardDenyError(
                        "Hard security denial: Make inline eval is forbidden"
                    )
                if option not in options_with_values:
                    continue
                if position > 0:
                    raise HardDenyError(
                        f"Hard security denial: ambiguous clustered Make option '{token}' is forbidden"
                    )
                # The remainder is this option's value, not more flags.
                break

    def _validate_grep_arguments(self, args: list[str]) -> None:
        index = 0
        while index < len(args):
            token = args[index]
            lowered = token.casefold()
            if lowered in {"-r", "--recursive", "--dereference-recursive"}:
                raise HardDenyError("Hard security denial: recursive grep is forbidden")
            if token.startswith("-") and not token.startswith("--") and any(
                flag in token[1:] for flag in ("r", "R")
            ):
                raise HardDenyError("Hard security denial: recursive grep is forbidden")
            if lowered.startswith("--directories=") and lowered.partition("=")[2] == "recurse":
                raise HardDenyError("Hard security denial: recursive grep is forbidden")
            if lowered == "-d" and index + 1 < len(args):
                if args[index + 1].casefold() == "recurse":
                    raise HardDenyError("Hard security denial: recursive grep is forbidden")
                index += 2
                continue
            if lowered.startswith("-d") and lowered[2:] == "recurse":
                raise HardDenyError("Hard security denial: recursive grep is forbidden")
            index += 1

    def _validate_rg_arguments(self, command: list[str], work_dir: Path) -> None:
        """Allow ripgrep only over explicit regular files, never directory trees."""
        safe_flags = {
            "-n", "--line-number", "-N", "--no-line-number",
            "-i", "--ignore-case", "-s", "--case-sensitive", "-S", "--smart-case",
            "-F", "--fixed-strings", "-w", "--word-regexp", "-x", "--line-regexp",
            "-v", "--invert-match", "-q", "--quiet", "-c", "--count",
            "--count-matches", "--no-messages", "--json", "--pcre2",
        }
        positionals: list[str] = []
        after_separator = False
        for token in command[1:]:
            if token == "--" and not after_separator:
                after_separator = True
                continue
            if not after_separator and token in safe_flags:
                continue
            if not after_separator and token.startswith("-"):
                raise HardDenyError(
                    f"Hard security denial: unsupported ripgrep option '{token}' could enable recursive access"
                )
            positionals.append(token)
        if len(positionals) < 2:
            raise HardDenyError(
                "Hard security denial: ripgrep requires explicit regular-file operands"
            )
        for token in positionals[1:]:
            path = self._resolve_workspace_reference(token, work_dir, "ripgrep input")
            if not path.is_file():
                raise HardDenyError(
                    "Hard security denial: recursive ripgrep directory traversal is forbidden"
                )

    def _validate_shell_command(self, command: list[str], executable: str, work_dir: Path) -> None:
        if executable in {"bash", "sh"}:
            if len(command) != 2:
                raise HardDenyError(
                    f"Hard security denial: for '{command[0]}' only a workspace script file is allowed"
                )
            self._validate_script_argument(command[1], executable, work_dir)
            return

        if executable in {"powershell", "pwsh"}:
            if len(command) < 3:
                raise HardDenyError(f"Hard security denial: for '{command[0]}' only -File is allowed")

            script_index: int | None = None
            index = 1
            while index < len(command):
                token = command[index]
                lowered = token.lower()
                if lowered in _POWERSHELL_COMMAND_FLAGS:
                    raise HardDenyError(f"Hard security denial: inline mode '{token}' is forbidden")
                if lowered in _POWERSHELL_FILE_FLAGS:
                    script_index = index + 1
                    break
                if lowered in _POWERSHELL_SAFE_FLAGS:
                    index += 1
                    continue
                if lowered in _POWERSHELL_SAFE_FLAGS_WITH_VALUE:
                    if index + 1 >= len(command):
                        raise HardDenyError(f"Hard security denial: flag '{token}' requires a value")
                    self._validate_common_tokens([command[index + 1]], work_dir=work_dir)
                    index += 2
                    continue
                raise HardDenyError(f"Hard security denial: for '{command[0]}' only -File is allowed")

            if script_index is None or script_index >= len(command):
                raise HardDenyError(f"Hard security denial: no script follows -File for '{command[0]}'")

            if len(command) != script_index + 1:
                raise HardDenyError(f"Hard security denial: only one PowerShell script is allowed")

            script = command[script_index]
            self._validate_common_tokens(command[1:script_index], work_dir=work_dir)
            self._validate_script_argument(script, executable, work_dir)
            return

        self._validate_windows_cmd_command(command, work_dir)

    def _validate_windows_cmd_command(self, command: list[str], work_dir: Path) -> None:
        if len(command) < 3 or command[1].lower() != "/c":
            raise HardDenyError(f"Hard security denial: for '{command[0]}' only /c is allowed")

        if len(command) != 3:
            raise HardDenyError(f"Hard security denial: only one safe argument is allowed after /c")

        payload = command[2].strip()
        if not payload:
            raise HardDenyError("Hard security denial: empty string after /c is forbidden")
        if _CMD_DANGEROUS_INLINE_RE.search(payload):
            raise HardDenyError("Hard security denial: dangerous shell operators in cmd /c")
        if _SUBST_RE.search(payload):
            raise HardDenyError("Hard security denial: command substitution in cmd /c")

        if self._looks_like_relative_script(payload, _SCRIPT_EXTENSIONS["cmd"]):
            self._validate_script_argument(payload, "cmd", work_dir)
            return

        builtin, separator, remainder = payload.partition(" ")
        if builtin.lower() not in _WINDOWS_CMD_BUILTINS:
            raise HardDenyError(f"Hard security denial: unsupported cmd /c payload")
        if not separator:
            return

        remainder_tokens = [token for token in remainder.split() if token]
        if not remainder_tokens:
            return

        if not all(_WINDOWS_CMD_BUILTIN_RE.fullmatch(token) for token in remainder_tokens):
            raise HardDenyError("Hard security denial: CMD builtin arguments contain invalid characters")

        self._validate_common_tokens(remainder_tokens, work_dir=work_dir)

    def _validate_common_tokens(
        self,
        tokens: list[str],
        executable: str = "",
        *,
        allow_executable_path: bool = False,
        work_dir: Path | None = None,
    ) -> None:
        joined_tokens = " ".join(tokens)
        if redact_text(joined_tokens) != joined_tokens:
            raise HardDenyError(
                "Hard security denial: credential-bearing command arguments are forbidden"
            )
        for token_index, token in enumerate(tokens):
            if "\x00" in token:
                raise HardDenyError(
                    "Hard security denial: command arguments cannot contain NUL"
                )
            # Exact match only with shell operator (not substring!)
            if token in _FORBIDDEN_OPERATORS:
                raise HardDenyError(f"Hard security denial: shell operator '{token}' is forbidden")

            # Detect command substitution inside arguments: $(…), `…`, ${…}
            if _SUBST_RE.search(token):
                raise HardDenyError(
                    "Hard security denial: Command substitution ($(), ``, ${}) is forbidden"
                )

            # Secret paths are forbidden for every command, including
            # read-only ones, on both relative and absolute paths.
            self._check_secret_token(token)
            if _INTERNAL_TOKEN_RE.search(token.replace("\\", "/")):
                raise HardDenyError("Hard security denial: access to internal .kvist paths is forbidden")

            # Reapply path-boundary checks to attached option/response values;
            # ``--config=/host`` must not hide a path that ``/host`` would
            # have rejected as a standalone token.
            docker_host_path = (
                executable.casefold() == "docker"
                and _docker_path_is_risk_graded(tokens, token_index)
            )
            for fragment in _argument_path_fragments(token):
                if (
                    self._is_absolute_path(fragment)
                    and not allow_executable_path
                    and executable.casefold() not in _ABSOLUTE_PATH_SAFE_COMMANDS
                    and not docker_host_path
                ):
                    raise HardDenyError(
                        "Hard security denial: Absolute paths are forbidden in arguments"
                    )
                if fragment.startswith("\\\\") and not docker_host_path:
                    raise HardDenyError("Hard security denial: UNC paths are forbidden in arguments")
                segments = [s for s in fragment.replace("\\", "/").split("/") if s]
                if ".." in segments and not docker_host_path:
                    raise HardDenyError("Hard security denial: Path traversal (..) is forbidden")

            if work_dir is not None and not allow_executable_path:
                self._check_existing_sensitive_reference(token, work_dir)

    def _check_secret_token(self, token: str) -> None:
        if _SECRET_TOKEN_RE.search(token.replace("\\", "/")):
            raise HardDenyError("Hard security denial: access to secret files is forbidden")

    def _check_existing_sensitive_reference(self, token: str, work_dir: Path) -> None:
        """Re-check canonical targets for direct, attached, and response paths.

        Relative arguments are workspace references even for otherwise safe
        read-only tools.  Resolve them before execution so a symlink cannot
        turn ``cat local-name`` (or an attached option value) into an
        out-of-workspace read.  Resolution errors for relative references are
        security relevant and therefore fail closed.

        Explicit absolute paths retain their separately risk-graded behaviour
        for the small read-only command allowlist.
        """
        for fragment in _argument_path_fragments(token):
            if not fragment or "\x00" in fragment:
                continue
            try:
                raw = Path(fragment).expanduser()
            except (OSError, RuntimeError, ValueError) as error:
                raise HardDenyError(
                    "Hard security denial: cannot resolve command argument"
                ) from error
            is_explicit_absolute = raw.is_absolute()
            candidate = raw if is_explicit_absolute else work_dir / raw

            if not is_explicit_absolute:
                try:
                    resolved = candidate.resolve(strict=False)
                except (OSError, RuntimeError, ValueError) as error:
                    raise HardDenyError(
                        "Hard security denial: cannot resolve relative command "
                        "argument or symlink"
                    ) from error
                if not _is_relative_to(resolved, self.workspace):
                    raise HardDenyError(
                        "Hard security denial: command argument resolves "
                        "outside the workspace"
                    )

            if not candidate.exists() and not candidate.is_symlink():
                continue
            try:
                canonical = candidate.resolve(strict=True)
            except (OSError, RuntimeError, ValueError) as error:
                if not is_explicit_absolute:
                    raise HardDenyError(
                        "Hard security denial: cannot resolve relative command "
                        "argument or symlink"
                    ) from error
                continue
            if not is_explicit_absolute and not _is_relative_to(
                canonical,
                self.workspace,
            ):
                raise HardDenyError(
                    "Hard security denial: command argument resolves outside "
                    "the workspace"
                )
            if not is_explicit_absolute:
                self._check_workspace_regular_file_hardlinks(
                    canonical,
                    "command argument",
                )
            self._check_resolved_sensitive_path(canonical, "command argument")

    def _check_workspace_regular_file_hardlinks(
        self,
        candidate: Path,
        kind: str,
    ) -> None:
        try:
            info = candidate.stat()
        except OSError as error:
            raise HardDenyError(
                f"Hard security denial: cannot inspect {kind} identity"
            ) from error
        self._check_inode_hardlink_containment(info, kind)

    def _check_inode_hardlink_containment(
        self,
        info: os.stat_result,
        kind: str,
    ) -> None:
        """Reject an inode whose extra names may live outside the workspace.

        A link count above one is only dangerous when some of those names are
        unreachable from the workspace: that is how an external inode is
        imported under a workspace pathname.  Aliases that are all inside the
        workspace import nothing; secret aliases among them are neutralised by
        the sandbox mask, not by denying the argument.  A count below one means
        the inode is already unlinked by a concurrent replace and has fewer,
        never more, names.
        """
        if not stat.S_ISREG(info.st_mode) or info.st_nlink <= 1:
            return
        try:
            aliases = count_workspace_inode_aliases(
                self.workspace,
                (info.st_dev, info.st_ino),
            )
        except UnsafeSecretPathError as error:
            raise HardDenyError(
                f"Hard security denial: {kind} has multiple hard links and the "
                "workspace cannot be scanned to prove containment"
            ) from error
        if aliases < info.st_nlink:
            raise HardDenyError(
                f"Hard security denial: {kind} has multiple hard links and "
                "cannot be proven to stay inside the workspace"
            )

    def _validate_script_argument(self, script: str, executable: str, work_dir: Path) -> None:
        self._validate_common_tokens([script], work_dir=work_dir)
        if not self._looks_like_relative_script(script, _SCRIPT_EXTENSIONS[executable]):
            raise HardDenyError(
                f"Hard security denial: for '{executable}' only a workspace script file is allowed"
            )
        self._resolve_workspace_reference(script, work_dir, f"{executable} script")

    def _looks_like_relative_script(self, path_token: str, extensions: tuple[str, ...]) -> bool:
        normalized = path_token.replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized.lower().endswith(extensions)

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _is_absolute_path(self, token: str) -> bool:
        """Absolute path: Unix (/..., ~/...) or Windows (C:\\...)."""
        if token.startswith("/") or token.startswith("~"):
            return True
        if len(token) >= 3 and token[1] == ":" and token[2] in ("/", "\\"):
            return True
        return False

    def _resolve_cwd(self, cwd: str | None) -> Path:
        """Resolves working directory strictly within workspace."""
        if not cwd:
            candidate = self.workspace
        else:
            if self._is_absolute_path(cwd) or cwd.startswith("\\\\"):
                raise HardDenyError("Hard security denial: absolute paths are forbidden for cwd")
            if ".." in [part for part in cwd.replace("\\", "/").split("/") if part]:
                raise HardDenyError("Hard security denial: working directory is outside workspace")
            try:
                candidate = (self.workspace / cwd).resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise HardDenyError(
                    f"Hard security denial: cannot resolve working directory: {error}"
                ) from error
        try:
            candidate.relative_to(self.workspace)
        except ValueError:
            raise HardDenyError("Hard security denial: working directory is outside workspace")

        if not candidate.is_dir():
            raise HardDenyError(f"Hard security denial: working directory not found: {cwd}")

        self._check_resolved_sensitive_path(candidate, "working directory")

        return candidate

    def _resolve_workspace_reference(self, token: str, work_dir: Path, kind: str) -> Path:
        raw = Path(token).expanduser()
        try:
            candidate = (raw if raw.is_absolute() else work_dir / raw).resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise HardDenyError(
                f"Hard security denial: cannot resolve {kind}: {error}"
            ) from error
        try:
            candidate.relative_to(self.workspace)
        except ValueError as error:
            raise HardDenyError(
                f"Hard security denial: {kind} resolves outside the workspace"
            ) from error
        self._check_resolved_sensitive_path(candidate, kind)
        return candidate

    def _check_resolved_sensitive_path(self, candidate: Path, kind: str) -> None:
        normalized = candidate.as_posix()
        if _INTERNAL_TOKEN_RE.search(normalized):
            raise HardDenyError(
                f"Hard security denial: {kind} points into internal .kvist state"
            )
        if _SECRET_TOKEN_RE.search(normalized):
            raise HardDenyError(
                f"Hard security denial: {kind} points into secret material"
            )
