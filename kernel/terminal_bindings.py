# -*- coding: utf-8 -*-
"""Executable resolution and the execution context that binds an approval to exact executables and files."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from kernel.command_policy import (
    canonical_executable_name,
    is_bare_executable_token,
    is_exact_version_probe,
)
from kernel.sandbox import SandboxMode
from kernel.secret_paths import UnsafeSecretPathError, secret_mask_digest
from kernel.terminal_git import _harden_git_arguments
from kernel.terminal_interpreters import (
    _direct_interpreter_script,
    _option_value,
    _option_values,
    _script_file_arguments,
)
from kernel.terminal_paths import (
    _directory_identity,
    _find_parent_file,
    _is_relative_to,
    _path_entry_identity,
    _path_entry_is_symlink,
    _path_identity,
    _sha256_file,
)
from kernel.terminal_rules import _SCRIPT_EXTENSIONS, _WINDOWS_CMD_BUILTINS
from kernel.terminal_trust import (
    _env_interpreter_name,
    _is_trusted_system_executable,
    _parse_shebang,
    _same_executable_family,
    _verify_venv_python_launcher,
)
from kernel.terminal_types import (
    ExecutableBinding,
    ExecutableOrigin,
    ExecutionContext,
    FileIdentity,
    HardDenyError,
)


class ExecutableBindingMixin:
    """Executable resolution, origin approval and execution-context binding for TerminalExecutor.

    Instance attributes expected from TerminalExecutor.__init__: ``workspace``,
    ``profile``, ``_allowed``, ``_policy``, ``_policy_note``, ``_is_windows`` and
    ``_external_tool_paths``. Calls ``_split_pipe_segments``,
    ``_resolve_workspace_reference``, ``_looks_like_relative_script`` and the
    hardlink and sensitive-path checks from ArgumentValidationMixin, and
    ``_git_metadata_directory``, ``_git_common_directory`` and
    ``_git_configuration_paths`` from GitValidationMixin.
    """

    def _build_execution_context(
        self,
        command: list[str],
        work_dir: Path,
        role: str,
        env: dict[str, str],
        bindings: tuple[ExecutableBinding, ...],
        *,
        sandbox_mode: SandboxMode,
        network_allowed: bool,
        bwrap_path: str | None,
        secret_targets: tuple[list[Path], list[Path]] | None = None,
    ) -> ExecutionContext:
        referenced = tuple(self._hash_referenced_files(command, work_dir))
        environment_payload = json.dumps(sorted(env.items()), ensure_ascii=False, separators=(",", ":"))
        policy_payload = json.dumps(
            {
                "allow": self._policy.allow,
                "deny": self._policy.deny,
                "ask": self._policy.ask,
                "note": self._policy_note,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        sandbox_identity = (
            _path_identity(Path(bwrap_path)) if bwrap_path else None
        )
        try:
            mask_digest = secret_mask_digest(self.workspace, secret_targets)
        except UnsafeSecretPathError as error:
            raise HardDenyError(
                f"Hard security denial: cannot build a complete secret mask: {error}"
            ) from error
        return ExecutionContext(
            argv=tuple(command),
            cwd=str(work_dir),
            cwd_identity=_directory_identity(work_dir),
            role=role,
            sandbox_mode=sandbox_mode.value,
            sandbox_profile=self.profile,
            network_mode="allow" if network_allowed else "deny",
            environment_digest=hashlib.sha256(environment_payload.encode("utf-8")).hexdigest(),
            policy_digest=hashlib.sha256(policy_payload.encode("utf-8")).hexdigest(),
            sandbox_executable_identity=sandbox_identity,
            executable_bindings=bindings,
            referenced_files=referenced,
            secret_mask_digest=mask_digest,
            tool_paths=tuple(str(path) for path in self._external_tool_paths),
        )

    def _resolve_executable(self, token: str, work_dir: Path, env: dict[str, str]) -> Path | None:
        binding = self._resolve_executable_binding(token, work_dir, env)
        return Path(binding.resolved_path) if binding.resolved_path else None

    def _resolve_executable_bindings(
        self, command: list[str], work_dir: Path, env: dict[str, str]
    ) -> tuple[ExecutableBinding, ...]:
        bindings: list[ExecutableBinding] = []
        for segment in self._split_pipe_segments(command):
            token = segment[0]
            if (
                self._is_windows
                and is_bare_executable_token(token)
                and token.casefold() in _WINDOWS_CMD_BUILTINS
            ):
                cmd_binding = self._resolve_executable_binding("cmd.exe", work_dir, env)
                binding = ExecutableBinding(
                    token=token,
                    lookup_path=cmd_binding.lookup_path,
                    lookup_in_workspace=cmd_binding.lookup_in_workspace,
                    lookup_is_symlink=cmd_binding.lookup_is_symlink,
                    resolved_path=cmd_binding.resolved_path,
                    requested_identity=canonical_executable_name(token),
                    effective_identity=cmd_binding.effective_identity,
                    identity_matches=(
                        canonical_executable_name(token) == cmd_binding.effective_identity
                        if cmd_binding.effective_identity is not None
                        else None
                    ),
                    lookup_identity=cmd_binding.lookup_identity,
                    target_identity=cmd_binding.target_identity,
                    trusted_system=cmd_binding.trusted_system,
                    content_digest=cmd_binding.content_digest,
                    requested_name=canonical_executable_name(token),
                    effective_name=cmd_binding.effective_name,
                    target_in_workspace=cmd_binding.target_in_workspace,
                    lookup_is_alias=cmd_binding.lookup_is_alias,
                    origin=cmd_binding.origin,
                    identity_mismatch=False,
                )
            else:
                binding = self._resolve_executable_binding(token, work_dir, env)
            bindings.append(binding)
        if "|" in command:
            bindings.append(self._resolve_executable_binding("sh", work_dir, env))
        return tuple(bindings)

    def _resolve_executable_binding(
        self, token: str, work_dir: Path, env: dict[str, str], *, attest_shebang: bool = True
    ) -> ExecutableBinding:
        lookup: Path | None
        if not is_bare_executable_token(token):
            raw = Path(token).expanduser()
            lookup = raw if raw.is_absolute() else work_dir / raw
            lookup = Path(os.path.abspath(lookup))
        else:
            found = shutil.which(token, path=env["PATH"])
            lookup = Path(found).absolute() if found else None

        resolved: Path | None = None
        if lookup is not None:
            try:
                candidate = lookup.resolve(strict=True)
                if candidate.is_file():
                    resolved = candidate
            except (OSError, RuntimeError):
                resolved = None

        trusted = bool(
            lookup is not None
            and resolved is not None
            and _is_trusted_system_executable(lookup, resolved)
        )
        digest: str | None = None
        if resolved is not None and not trusted:
            try:
                digest = _sha256_file(resolved)
            except OSError as error:
                raise HardDenyError(
                    f"Hard security denial: cannot hash executable '{token}': {error}"
                ) from error

        interpreter = (
            self._attest_shebang_interpreter(resolved, work_dir, env)
            if attest_shebang and resolved is not None and not trusted
            else None
        )
        venv_verified: tuple[FileIdentity, str] | None = None
        if not trusted and lookup is not None and resolved is not None:
            venv_verified = _verify_venv_python_launcher(self.workspace, lookup, resolved)
        requested_identity = canonical_executable_name(token)
        effective_identity = (
            canonical_executable_name(resolved.name) if resolved is not None else None
        )
        requested_name = requested_identity
        effective_name = effective_identity

        lookup_in_workspace = bool(
            lookup is not None and _is_relative_to(lookup, self.workspace)
        )
        target_in_workspace = bool(
            resolved is not None and _is_relative_to(resolved, self.workspace)
        )
        lookup_is_symlink = _path_entry_is_symlink(lookup)
        lookup_is_alias = bool(
            lookup is not None
            and resolved is not None
            and (lookup_is_symlink or Path(os.path.abspath(lookup)) != resolved)
        )

        if resolved is None:
            origin = ExecutableOrigin.UNRESOLVED
        elif trusted:
            origin = ExecutableOrigin.TRUSTED_SYSTEM
        elif venv_verified is not None:
            origin = ExecutableOrigin.VERIFIED_VENV
        elif lookup_in_workspace and lookup_is_alias:
            origin = ExecutableOrigin.WORKSPACE_ALIAS
        elif lookup_in_workspace:
            origin = ExecutableOrigin.WORKSPACE_FILE
        else:
            origin = ExecutableOrigin.EXTERNAL_TOOL

        identity_matches = (
            (requested_name == effective_name)
            if effective_name is not None
            else None
        )
        identity_mismatch = bool(
            requested_name
            and effective_name
            and not _same_executable_family(requested_name, effective_name)
        )
        if venv_verified is not None:
            identity_mismatch = False

        return ExecutableBinding(
            token=token,
            lookup_path=str(lookup) if lookup is not None else None,
            resolved_path=str(resolved) if resolved is not None else None,
            lookup_identity=(
                _path_entry_identity(lookup)
                if lookup is not None
                else None
            ),
            target_identity=(
                _path_identity(resolved)
                if resolved is not None
                else None
            ),
            trusted_system=trusted,
            content_digest=digest,

            requested_name=requested_name,
            effective_name=effective_name,
            requested_identity=requested_identity,
            effective_identity=effective_identity,
            identity_matches=identity_matches,
            lookup_in_workspace=lookup_in_workspace,
            target_in_workspace=target_in_workspace,
            lookup_is_symlink=lookup_is_symlink,
            lookup_is_alias=lookup_is_alias,
            origin=origin,
            identity_mismatch=identity_mismatch,

            interpreter_path=interpreter[0] if interpreter else None,
            interpreter_identity=interpreter[1] if interpreter else None,
            interpreter_digest=interpreter[2] if interpreter else None,
            interpreter_trusted_system=interpreter[3] if interpreter else False,

            venv_launcher=venv_verified is not None,
            venv_pyvenv_cfg_identity=(
                venv_verified[0]
                if venv_verified
                else None
            ),
            venv_pyvenv_cfg_digest=(
                venv_verified[1]
                if venv_verified
                else None
            ),
        )

    def _attest_shebang_interpreter(
        self, script: Path, work_dir: Path, env: dict[str, str]
    ) -> tuple[str, FileIdentity | None, str | None, bool] | None:
        """Attest the interpreter the kernel selects from a script's ``#!`` line.

        The kernel execs ``parts[0]`` literally, so that binary is what we must
        attest — regardless of its name.  Only when ``parts[0]`` resolves to a
        *trusted-system* ``env`` do we follow its argument to the effective
        interpreter (``env`` re-executes it via PATH).  An ``env``-named binary
        anywhere else is attested as itself, closing the impersonation bypass.
        """
        try:
            with open(script, "rb") as handle:
                head = handle.read(256)
        except OSError:
            return None
        parts = _parse_shebang(head)
        if parts is None:
            return None
        if not parts:
            raise HardDenyError(
                "Hard security denial: unsupported or ambiguous shebang interpreter"
            )
        binding = self._resolve_executable_binding(
            parts[0], work_dir, env, attest_shebang=False
        )
        if (
            binding.resolved_path is not None
            and binding.trusted_system
            and os.path.basename(binding.resolved_path) == "env"
        ):
            arg = _env_interpreter_name(parts[1:])
            if not arg:
                raise HardDenyError(
                    "Hard security denial: unsupported or ambiguous env shebang interpreter"
                )
            binding = self._resolve_executable_binding(
                arg, work_dir, env, attest_shebang=False
            )
        if binding.resolved_path is None:
            # Interpreter cannot be resolved with the sandboxed PATH; the exec
            # would fail, but do not fabricate an attestation for it.
            return (binding.token, None, None, False)
        interpreter = Path(binding.resolved_path)
        self._check_resolved_sensitive_path(interpreter, "shebang interpreter")
        in_workspace = _is_relative_to(interpreter, self.workspace)
        if not binding.trusted_system and not in_workspace and self._run_sandbox != "host":
            raise HardDenyError(
                "Hard security denial: shebang interpreter is outside trusted system "
                "roots and the workspace"
            )
        return (
            binding.resolved_path,
            binding.target_identity,
            binding.content_digest,
            binding.trusted_system,
        )

    def _validate_executable_boundaries(
        self,
        command: list[str],
        work_dir: Path,
        bindings: tuple[ExecutableBinding, ...],
    ) -> None:
        segments = self._split_pipe_segments(command)
        for index, segment in enumerate(segments):
            token = segment[0]
            binding = bindings[index]
            if binding.lookup_in_workspace and binding.lookup_path:
                lookup = Path(binding.lookup_path)
                try:
                    # ``lstat``: a launcher symlink is attested through its own
                    # target binding, not through the target's link count.
                    lookup_info = lookup.lstat()
                except OSError as error:
                    raise HardDenyError(
                        "Hard security denial: cannot inspect workspace "
                        "executable identity"
                    ) from error
                self._check_inode_hardlink_containment(
                    lookup_info,
                    "workspace executable",
                )
            if binding.target_in_workspace and binding.resolved_path:
                self._check_workspace_regular_file_hardlinks(
                    Path(binding.resolved_path),
                    "workspace executable",
                )
            if binding.resolved_path:
                self._check_resolved_sensitive_path(
                    Path(binding.resolved_path), "executable"
                )
            if is_bare_executable_token(token):
                continue

            # A verified project venv Python launcher (e.g.
            # .venv/bin/python -> /usr/bin/python3) may resolve to a trusted,
            # user-immutable system interpreter; the launcher's own lstat
            # identity and its venv's pyvenv.cfg are attested, so swapping the
            # symlink or pyvenv.cfg invalidates approval.  This is the only
            # workspace-to-system escape permitted — a generic alias (e.g.
            # ``.venv/bin/cat -> /usr/bin/bash``) is never trusted merely
            # because its target lives under a system root; escapes to any
            # other target, or from any other basename/location, stay denied.
            # The target need not live under a trusted system root (e.g. a
            # uv-managed interpreter under the user's HOME) when pyvenv.cfg
            # declares it — see ``_verify_venv_python_launcher``.
            if (
                binding.lookup_in_workspace
                and not binding.target_in_workspace
                and not binding.venv_launcher
            ):
                raise HardDenyError(
                    "Hard security denial: executable symlink resolves"
                    " outside the workspace: "
                    f"requested={binding.token!r}, "
                    f"lookup={binding.lookup_path!r}, "
                    f"effective={binding.resolved_path!r}"
                )
            if not binding.lookup_in_workspace and not binding.trusted_system and self._run_sandbox != "host":
                raise HardDenyError(
                    "Hard security denial: path-qualified executable is outside trusted system roots"
                )

        if "|" in command:
            shell_binding = bindings[-1]
            if not shell_binding.resolved_path or not shell_binding.trusted_system:
                raise HardDenyError(
                    "Hard security denial: trusted pipeline shell is unavailable"
                )

    def _requires_executable_origin_approval(
        self, command: list[str], bindings: tuple[ExecutableBinding, ...]
    ) -> bool:
        for index, segment in enumerate(self._split_pipe_segments(command)):
            binding = bindings[index]
            if binding.trusted_system:
                continue
            # Only a verified project venv Python launcher is treated as a
            # trusted origin; a generic workspace symlink/PATH alias landing
            # on a trusted-looking target (e.g. a workspace ``cat`` aliasing
            # ``/usr/bin/bash``) must still require per-execution approval so
            # a project allow rule keyed on the alias name cannot silently
            # authorize a different effective executable.
            if binding.venv_launcher:
                continue
            executable = canonical_executable_name(segment[0])
            if executable in self._allowed or is_exact_version_probe(segment):
                return True
        return False

    def _materialize_executables(
        self,
        command: list[str],
        bindings: tuple[ExecutableBinding, ...],
    ) -> list[str]:
        materialized: list[str] = []
        for segment_index, segment in enumerate(self._split_pipe_segments(command)):
            if segment_index:
                materialized.append("|")
            binding = bindings[segment_index]
            is_builtin = (
                self._is_windows
                and is_bare_executable_token(segment[0])
                and segment[0].casefold() in _WINDOWS_CMD_BUILTINS
            )
            materialized.append(self._materialized_argv0(segment[0], binding, is_builtin))
            args = list(segment[1:])
            if canonical_executable_name(segment[0]) == "git" and not is_exact_version_probe(segment):
                args = _harden_git_arguments(args)
            materialized.extend(args)
        return materialized

    def _materialized_argv0(self, token: str, binding: ExecutableBinding, is_builtin: bool) -> str:
        if is_builtin or not binding.resolved_path:
            return token
        # A project launcher symlink (e.g. .venv/bin/python) must keep its own
        # path as argv[0] so the interpreter finds pyvenv.cfg and its
        # site-packages; substituting the resolved system binary would break
        # venv semantics.  The launcher inode is attested, so a swap between
        # approval and spawn invalidates the bound context.  A bare "python"
        # found on the child PATH in .venv/bin is the same launcher: given
        # the resolved binary instead, it ran outside the venv and could not
        # import the project's own packages.
        if (
            binding.lookup_path
            and binding.lookup_path != binding.resolved_path
            and binding.venv_launcher
        ):
            return binding.lookup_path
        return binding.resolved_path

    def _hash_referenced_files(self, command: list[str], work_dir: Path) -> list[tuple[str, str]]:
        paths: dict[str, Path] = {}
        for segment in self._split_pipe_segments(command):
            for path in self._referenced_files_for_segment(segment, work_dir):
                if not path.exists():
                    continue
                try:
                    canonical = path.resolve(strict=True)
                except (OSError, RuntimeError) as error:
                    raise HardDenyError(
                        f"Hard security denial: cannot resolve referenced execution input: {error}"
                    ) from error
                if not canonical.is_file():
                    raise HardDenyError("Hard security denial: referenced execution input is not a regular file")
                try:
                    relative = canonical.relative_to(self.workspace).as_posix()
                except ValueError as error:
                    raise HardDenyError(
                        "Hard security denial: referenced execution input is outside the workspace"
                    ) from error
                self._check_resolved_sensitive_path(canonical, "referenced execution input")
                paths[relative] = canonical
        hashed: list[tuple[str, str]] = []
        for relative, path in sorted(paths.items()):
            try:
                digest = _sha256_file(path)
            except OSError as error:
                raise HardDenyError(
                    f"Hard security denial: cannot hash referenced execution input '{relative}': {error}"
                ) from error
            hashed.append((relative, digest))
        return hashed

    def _referenced_files_for_segment(self, command: list[str], work_dir: Path) -> list[Path]:
        executable = canonical_executable_name(command[0])
        found: list[Path] = []

        if is_exact_version_probe(command):
            return found

        if executable in {"bash", "sh"} and len(command) == 2:
            found.append(self._resolve_workspace_reference(command[1], work_dir, "script"))
        elif executable in {"powershell", "pwsh"}:
            script = _option_value(command, {"-file", "-f"}, casefold=True)
            if script:
                found.append(self._resolve_workspace_reference(script, work_dir, "PowerShell script"))
        elif executable in {"cmd", "cmd.exe"} and len(command) == 3:
            payload = command[2]
            if self._looks_like_relative_script(payload, _SCRIPT_EXTENSIONS[executable]):
                found.append(self._resolve_workspace_reference(payload, work_dir, "CMD script"))
        elif executable in {"python", "python3", "py", "node", "ruby", "perl", "php"}:
            script = _direct_interpreter_script(command, executable)
            if script:
                found.append(self._resolve_workspace_reference(script, work_dir, "interpreter script"))
            found.extend(self._existing_workspace_file_arguments(command[1:], work_dir))
        elif executable in {"awk", "gawk", "sed"}:
            for token in _script_file_arguments(command):
                found.append(self._resolve_workspace_reference(token, work_dir, f"{executable} script"))
        elif executable == "make":
            make_dir = self._effective_option_directory(
                command, work_dir, {"-C", "--directory"}, sequential=True
            )
            makefiles = _option_values(command, {"-f", "--file", "--makefile"})
            if makefiles:
                for makefile in makefiles:
                    found.append(self._resolve_workspace_reference(makefile, make_dir, "Makefile"))
            else:
                for name in ("GNUmakefile", "makefile", "Makefile"):
                    candidate = make_dir / name
                    if candidate.is_file():
                        found.append(candidate)
                        break
        elif executable == "cargo":
            manifest = _option_value(command, {"--manifest-path"})
            if manifest:
                found.append(self._resolve_workspace_reference(manifest, work_dir, "Cargo manifest"))
            else:
                candidate = _find_parent_file(work_dir, self.workspace, "Cargo.toml")
                if candidate:
                    found.append(candidate)
        elif executable == "go":
            go_dir = self._effective_option_directory(command, work_dir, {"-C"})
            candidate = _find_parent_file(go_dir, self.workspace, "go.mod")
            if candidate:
                found.append(candidate)
            found.extend(self._existing_workspace_file_arguments(command[1:], go_dir))
        elif executable == "cmake":
            source = _option_value(command, {"-S", "--source"})
            if source:
                source_dir = self._resolve_workspace_reference(source, work_dir, "CMake source")
            else:
                source_dir = self._cmake_positional_source(command, work_dir) or work_dir
            found.append(source_dir / "CMakeLists.txt")
        elif executable == "ninja":
            ninja_dir = self._effective_option_directory(command, work_dir, {"-C"})
            build_files = _option_values(command, {"-f"}) or ["build.ninja"]
            for build_file in build_files:
                found.append(self._resolve_workspace_reference(build_file, ninja_dir, "Ninja build file"))
        elif executable in {"npm", "npx", "pnpm", "yarn"}:
            directory_flags = {
                "npm": {"--prefix"},
                "npx": {"--prefix"},
                "pnpm": {"--dir", "--prefix", "-C"},
                "yarn": {"--cwd"},
            }[executable]
            package_dir = self._effective_option_directory(command, work_dir, directory_flags)
            found.append(package_dir / "package.json")
        elif executable == "git":
            git_dir = self._git_metadata_directory(work_dir)
            if git_dir is not None:
                commondir = git_dir / "commondir"
                if commondir.exists() or commondir.is_symlink():
                    found.append(commondir)
                found.extend(self._git_configuration_paths(git_dir))
                common_dir = self._git_common_directory(git_dir)
                for hooks in {git_dir / "hooks", common_dir / "hooks"}:
                    if hooks.is_dir():
                        for hook in hooks.iterdir():
                            if hook.is_file() and os.access(hook, os.X_OK):
                                found.append(hook)
        return found

    def _effective_option_directory(
        self,
        command: list[str],
        work_dir: Path,
        names: set[str],
        *,
        sequential: bool = False,
    ) -> Path:
        current = work_dir
        for value in _option_values(command, names):
            base = current if sequential else work_dir
            current = self._resolve_workspace_reference(value, base, "tool working directory")
            if not current.is_dir():
                # The tool will report a missing directory; keeping the
                # canonical path still makes the approval context stable.
                break
        return current

    def _cmake_positional_source(self, command: list[str], work_dir: Path) -> Path | None:
        for token in command[1:]:
            if token.startswith("-"):
                continue
            try:
                candidate = self._resolve_workspace_reference(token, work_dir, "CMake source")
            except HardDenyError:
                raise
            if candidate.is_dir() and (candidate / "CMakeLists.txt").exists():
                return candidate
        return None

    def _existing_workspace_file_arguments(self, args: list[str], work_dir: Path) -> list[Path]:
        """Bind plausible directly named files without guessing option arity."""
        found: dict[str, Path] = {}
        for token in args:
            candidates = [token]
            if "=" in token:
                candidates.append(token.partition("=")[2])
            for value in candidates:
                if not value or value.startswith("-"):
                    continue
                try:
                    raw = Path(value).expanduser()
                    candidate = raw if raw.is_absolute() else work_dir / raw
                    if not candidate.exists() and not candidate.is_symlink():
                        continue
                    resolved = self._resolve_workspace_reference(value, work_dir, "interpreter input")
                except (OSError, RuntimeError, ValueError):
                    continue
                if resolved.is_file():
                    found[str(resolved)] = resolved
        return list(found.values())

    @staticmethod
    def _escape_cmd_arg(arg: str) -> str:
        """Escapes CMD special characters for safe passing through cmd.exe /c."""
        # Characters that CMD interprets as operators
        _CMD_META = set('()%!^"<>&|')
        if not any(ch in _CMD_META for ch in arg):
            return arg
        # Escape each CMD metacharacter with ^
        escaped = []
        for ch in arg:
            if ch in _CMD_META:
                escaped.append("^")
            escaped.append(ch)
        return "".join(escaped)

    def _wrap_for_platform(
        self, command: list[str], bindings: tuple[ExecutableBinding, ...]
    ) -> list[str]:
        """On Windows wraps CMD built-in commands through cmd.exe /c."""
        if self._is_windows and command[0].lower() in _WINDOWS_CMD_BUILTINS:
            cmd_path = bindings[0].resolved_path
            if not cmd_path or not bindings[0].trusted_system:
                raise HardDenyError("Hard security denial: trusted cmd.exe is unavailable")
            # Escape arguments to prevent shell injection via & | && etc.
            safe_args = [self._escape_cmd_arg(a) for a in command]
            return [cmd_path, "/c"] + safe_args
        return command
