# -*- coding: utf-8 -*-
"""Git argument hardening and repository configuration checks for read-only git commands."""
from __future__ import annotations

import re
from pathlib import Path

from kernel.command_policy import (
    _git_has_external_helper_option,
    _git_has_remerge_diff_option,
    _git_ref_filter_has_signature_atom,
    _git_has_signature_verification_option,
    _git_has_unsafe_submodule_option,
    _git_tag_has_signing_or_verification_option,
    _git_read_operation_arguments,
    _has_long_output_option,
    is_exact_version_probe,
)
from kernel.terminal_paths import _is_relative_to
from kernel.terminal_types import HardDenyError


_KNOWN_GIT_SUBCOMMANDS: frozenset[str] = frozenset({
    "add", "am", "archive", "bisect", "blame", "branch", "bundle", "checkout",
    "cherry-pick", "clean", "clone", "commit", "config", "describe", "diff",
    "fetch", "format-patch", "gc", "grep", "init", "log", "ls-files", "merge",
    "mv", "notes", "pull", "push", "range-diff", "rebase", "reflog", "remote",
    "reset", "restore", "revert", "rev-parse", "rm", "show", "show-branch",
    "sparse-checkout", "stash", "status", "submodule", "switch", "tag", "worktree",
})


def _git_config_section_values(content: str, section: str, key: str) -> list[str]:
    """Extract simple scalar values from an exact, unsubsectioned Git section."""
    values: list[str] = []
    section_pattern = re.compile(
        rf"(?ims)^\s*\[\s*{re.escape(section)}\s*\]\s*"
        rf"(.*?)(?=^\s*\[|\Z)"
    )
    key_pattern = re.compile(
        rf"(?im)^\s*{re.escape(key)}\s*=\s*(.*?)\s*$"
    )
    for match in section_pattern.finditer(content):
        values.extend(value.group(1) for value in key_pattern.finditer(match.group(1)))
    return values


def _harden_git_arguments(args: list[str]) -> list[str]:
    """Disable repository-configured hooks/helpers for approved Git reads."""
    hardened = [
        "-c", "core.fsmonitor=false",
        "-c", "log.showSignature=false",
        "-c", "format.pretty=medium",
        "-c", "log.diffMerges=off",
        "-c", "diff.submodule=short",
        "-c", "diff.ignoreSubmodules=all",
        "-c", "core.alternateRefsCommand=",
        "-c", "branch.sort=refname",
        "-c", "tag.sort=refname",
        "--no-optional-locks",
    ]
    if not args:
        return hardened
    subcommand = args[0].casefold()
    helper_flags = ["--no-ext-diff", "--no-textconv"]
    submodule_flags = ["--ignore-submodules=all"]
    if subcommand == "status":
        return hardened + [args[0]] + submodule_flags + args[1:]
    if subcommand in {"diff", "log", "show"}:
        present = set(args[1:])
        return (
            hardened
            + [args[0]]
            + [flag for flag in helper_flags if flag not in present]
            + submodule_flags
            + args[1:]
        )
    if (
        subcommand == "stash"
        and len(args) >= 2
        and args[1].casefold() in {"list", "show"}
    ):
        present = set(args[2:])
        return (
            hardened
            + args[:2]
            + [flag for flag in helper_flags if flag not in present]
            + submodule_flags
            + args[2:]
        )
    return hardened + args


class GitValidationMixin:
    """Git argument and repository-configuration validation for TerminalExecutor.

    Instance attributes expected from TerminalExecutor.__init__: ``workspace``.
    """

    def _validate_git_arguments(self, command: list[str], work_dir: Path) -> None:
        """Reject Git syntax that can hide subcommands or launch configured helpers."""
        if is_exact_version_probe(command):
            return
        # Kvist's allowlist, risk policy, and deterministic hardening all bind
        # to direct ``git SUBCOMMAND`` syntax. Ambiguous/global-option forms
        # fail closed; callers can select a repository with ``cwd`` instead.
        if len(command) > 1 and command[1].startswith("-"):
            raise HardDenyError(
                "Hard security denial: Git global options before the subcommand are forbidden"
            )
        if len(command) > 1 and command[1].casefold() not in _KNOWN_GIT_SUBCOMMANDS:
            raise HardDenyError(
                "Hard security denial: unknown Git subcommands/aliases are forbidden"
            )
        if (
            len(command) > 1
            and command[1].casefold() == "tag"
            and _git_tag_has_signing_or_verification_option(command[2:])
        ):
            raise HardDenyError(
                "Hard security denial: Git tag signing/verification helpers are forbidden"
            )
        if (
            len(command) > 1
            and command[1].casefold() in {"branch", "tag"}
            and _git_ref_filter_has_signature_atom(command[2:])
        ):
            raise HardDenyError(
                "Hard security denial: Git ref signature formatting is forbidden"
            )
        git_dir = self._git_metadata_directory(work_dir)
        subcommand = command[1].casefold() if len(command) > 1 else ""
        self._deny_git_repository_helpers(git_dir, subcommand)
        read_args = _git_read_operation_arguments(command)
        if len(command) > 1 and command[1].casefold() in {"status", "diff"}:
            self._deny_git_worktree_filters(git_dir)
        if (
            len(command) > 1
            and command[1].casefold() == "status"
            and _git_has_unsafe_submodule_option(command[2:])
        ):
            raise HardDenyError(
                "Hard security denial: recursive Git submodule inspection is forbidden"
            )
        if read_args is None:
            return
        if _has_long_output_option(read_args):
            raise HardDenyError("Hard security denial: Git output-file mode is forbidden")
        if _git_has_external_helper_option(read_args):
            raise HardDenyError(
                "Hard security denial: Git external diff/textconv execution is forbidden"
            )
        if _git_has_remerge_diff_option(read_args):
            raise HardDenyError(
                "Hard security denial: Git remerge diffs may execute custom merge drivers"
            )
        if _git_has_unsafe_submodule_option(read_args):
            raise HardDenyError(
                "Hard security denial: recursive Git submodule inspection is forbidden"
            )
        if _git_has_signature_verification_option(read_args):
            raise HardDenyError(
                "Hard security denial: Git signature-verifier execution is forbidden"
            )

    def _git_metadata_directory(self, work_dir: Path) -> Path | None:
        """Locate repository metadata without allowing a gitdir workspace escape."""
        current = work_dir
        while _is_relative_to(current, self.workspace):
            marker = current / ".git"
            if marker.is_dir():
                resolved = marker.resolve(strict=True)
                if not _is_relative_to(resolved, self.workspace):
                    raise HardDenyError(
                        "Hard security denial: Git metadata resolves outside the workspace"
                    )
                return resolved
            if marker.is_file():
                try:
                    pointer = marker.resolve(strict=True)
                    if not _is_relative_to(pointer, self.workspace):
                        raise HardDenyError(
                            "Hard security denial: Git metadata pointer resolves outside the workspace"
                        )
                    content = pointer.read_text(encoding="utf-8", errors="strict")
                except HardDenyError:
                    raise
                except (OSError, UnicodeError) as error:
                    raise HardDenyError(
                        f"Hard security denial: cannot inspect Git metadata pointer: {error}"
                    ) from error
                prefix, separator, value = content.strip().partition(":")
                if prefix.casefold() != "gitdir" or not separator or not value.strip():
                    raise HardDenyError("Hard security denial: malformed .git metadata pointer")
                resolved = (current / value.strip()).resolve(strict=True)
                if not resolved.is_dir() or not _is_relative_to(resolved, self.workspace):
                    raise HardDenyError(
                        "Hard security denial: Git metadata resolves outside the workspace"
                    )
                return resolved
            if current == self.workspace or current.parent == current:
                break
            current = current.parent
        if (work_dir / "HEAD").is_file() and (work_dir / "config").is_file():
            return work_dir
        return None

    def _deny_git_worktree_filters(self, git_dir: Path | None) -> None:
        """SAFE Git worktree reads may not invoke clean/process filter commands."""
        if git_dir is None:
            return
        for config in self._git_configuration_paths(git_dir):
            content = self._read_git_configuration(config)
            if re.search(r"(?im)^\s*\[\s*(?:filter|include|includeIf)\b", content):
                raise HardDenyError(
                    "Hard security denial: Git worktree filters/includes may execute helpers"
                )

    def _deny_git_repository_helpers(
        self, git_dir: Path | None, subcommand: str
    ) -> None:
        """Reject effective repository config that can launch hidden helpers."""
        if git_dir is None:
            return
        for config in self._git_configuration_paths(git_dir):
            content = self._read_git_configuration(config)
            if re.search(r"(?im)^\s*\[\s*include(?:If)?\b", content):
                raise HardDenyError(
                    "Hard security denial: Git configuration includes may import executable settings"
                )
            alternate_commands = _git_config_section_values(
                content, "core", "alternateRefsCommand"
            )
            if alternate_commands or re.search(
                r"(?im)^\s*core\.alternateRefsCommand\s*=", content
            ):
                raise HardDenyError(
                    "Hard security denial: Git alternate-refs commands are forbidden"
                )
            if subcommand in {"branch", "tag"}:
                for value in _git_config_section_values(content, subcommand, "sort"):
                    normalized = value.casefold().lstrip("-")
                    if normalized.startswith("signature:") or re.search(
                        r"%\(\*?signature", normalized
                    ):
                        raise HardDenyError(
                            "Hard security denial: Git ref signature sorting is forbidden"
                        )

    def _read_git_configuration(self, config: Path) -> str:
        try:
            resolved = config.resolve(strict=True)
            if not _is_relative_to(resolved, self.workspace):
                raise HardDenyError(
                    "Hard security denial: Git configuration resolves outside the workspace"
                )
            if resolved.stat().st_size > 1024 * 1024:
                raise HardDenyError("Hard security denial: Git configuration is too large")
            return resolved.read_text(encoding="utf-8", errors="strict")
        except HardDenyError:
            raise
        except (OSError, UnicodeError) as error:
            raise HardDenyError(
                f"Hard security denial: cannot inspect Git configuration: {error}"
            ) from error

    def _git_common_directory(self, git_dir: Path) -> Path:
        """Resolve a linked worktree's common metadata directory fail-closed."""
        marker = git_dir / "commondir"
        if not marker.exists() and not marker.is_symlink():
            return git_dir
        try:
            resolved_marker = marker.resolve(strict=True)
            if not _is_relative_to(resolved_marker, self.workspace):
                raise HardDenyError(
                    "Hard security denial: Git commondir pointer resolves outside the workspace"
                )
            if resolved_marker.stat().st_size > 4096:
                raise HardDenyError("Hard security denial: Git commondir pointer is too large")
            value = resolved_marker.read_text(encoding="utf-8", errors="strict").strip()
            if not value or "\n" in value or "\r" in value or "\x00" in value:
                raise HardDenyError("Hard security denial: malformed Git commondir pointer")
            common_dir = (git_dir / value).resolve(strict=True)
        except HardDenyError:
            raise
        except (OSError, UnicodeError, RuntimeError) as error:
            raise HardDenyError(
                f"Hard security denial: cannot inspect Git commondir pointer: {error}"
            ) from error
        if not common_dir.is_dir() or not _is_relative_to(common_dir, self.workspace):
            raise HardDenyError(
                "Hard security denial: Git common metadata resolves outside the workspace"
            )
        return common_dir

    def _git_configuration_paths(self, git_dir: Path) -> list[Path]:
        """Return existing common and per-worktree Git configuration files."""
        common_dir = self._git_common_directory(git_dir)
        candidates = [common_dir / "config", git_dir / "config", git_dir / "config.worktree"]
        paths: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not candidate.exists() and not candidate.is_symlink():
                continue
            key = str(candidate)
            if key not in seen:
                seen.add(key)
                paths.append(candidate)
        return paths
