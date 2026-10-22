# -*- coding: utf-8 -*-
"""Interpreter inline-mode checks (python, awk, sed) and script argument extraction."""
from __future__ import annotations

import re
from pathlib import Path

from kernel.sandbox.command_policy import canonical_executable_name, is_exact_version_probe
from kernel.sandbox.terminal_types import HardDenyError


_AWK_SYSTEM_RE = re.compile(r"\bsystem\s*\(")
_AWK_LOAD_RE = re.compile(r"(^|[\s;{}])@load\b")
_AWK_INCLUDE_RE = re.compile(r"(^|[\s;{}])@include\b")
_AWK_EXTERNAL_PIPE_RE = re.compile(r"(?<!\|)\|(?!\|)|\|&")
_AWK_SAFE_LONG_OPTIONS: frozenset[str] = frozenset({
    "--characters-as-bytes", "--csv", "--traditional", "--posix", "--lint",
    "--lint-old", "--non-decimal-data", "--bignum", "--use-lc-numeric",
    "--optimize", "--no-optimize", "--gen-pot", "--version", "--help",
    "--usage", "--copyleft", "--copyright", "--re-interval", "--sandbox",
})
_AWK_SAFE_SHORT_OPTIONS: frozenset[str] = frozenset({
    "-b", "-c", "-C", "-g", "-h", "-L", "-M", "-N", "-O", "-P",
    "-r", "-S", "-t", "-V",
})

# GNU sed constructs that execute commands or write files:
# the standalone `e`/`w`/`W` commands and the `e` flag of s///.
_SED_STANDALONE_WRITE_RE = re.compile(
    r"(?:^|[;\n{}])\s*"
    r"(?:(?:\d+(?:~\d+)?|[+~]\d+|\$|/(?:\\.|[^/])*/)"
    r"(?:\s*,\s*(?:\d+(?:~\d+)?|[+~]\d+|\$|/(?:\\.|[^/])*/))?\s*)?"
    r"!?\s*[eWw](?:\s|$)"
)


def _strip_awk_strings(program: str) -> str:
    """Blank quoted AWK string contents while preserving operators outside."""
    output: list[str] = []
    quote: str | None = None
    escaped = False
    for char in program:
        if escaped:
            output.append(" ")
            escaped = False
            continue
        if quote is not None:
            if char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            output.append(" ")
            continue
        # POSIX AWK has only double-quoted string literals. Apostrophes are
        # ordinary regexp/program characters and must not hide later tokens.
        if char == '"':
            quote = '"'
            output.append(" ")
            continue
        output.append(char)
    return "".join(output)


def _sed_script_is_dangerous(script: str) -> bool:
    if _SED_STANDALONE_WRITE_RE.search(script):
        return True
    # Check the complete script before splitting on semicolons: GNU sed's
    # alternate-regex addresses may legally contain semicolons inside their
    # delimiter pair (for example ``\%a;b%w file``).
    if _sed_command_after_addresses(script) in {"e", "w", "W"}:
        return True
    for start in [0, *(match.end() for match in re.finditer(r"[;\n{}]", script))]:
        if _sed_command_after_addresses(script[start:]) in {"e", "w", "W"}:
            return True
    length = len(script)
    for index, char in enumerate(script[:-1]):
        if char != "s":
            continue
        delimiter = script[index + 1]
        if delimiter.isalnum() or delimiter.isspace() or delimiter == "\\":
            continue
        position = index + 2
        complete = True
        for _ in range(2):
            escaped = False
            while position < length:
                current = script[position]
                position += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == delimiter:
                    break
            else:
                complete = False
                break
        if not complete:
            continue
        flags_start = position
        while position < length and script[position] not in ";\n":
            position += 1
        flags = script[flags_start:position].lstrip()
        flag_word = flags.split(maxsplit=1)[0] if flags else ""
        # Numeric occurrence selectors and benign g/p/I/M flags are allowed;
        # e or w/W changes the command into execution or a filesystem write.
        if any(flag in flag_word for flag in ("e", "w", "W")):
            return True
    return False


def _sed_command_after_addresses(fragment: str) -> str:
    """Return the command byte after common GNU/POSIX sed addresses."""
    position = 0
    length = len(fragment)

    def skip_space(index: int) -> int:
        while index < length and fragment[index].isspace():
            index += 1
        return index

    def consume_regex(index: int, delimiter: str) -> int:
        escaped = False
        while index < length:
            char = fragment[index]
            index += 1
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == delimiter:
                return index
        return length

    position = skip_space(position)
    for address_index in range(2):
        before = position
        if position < length and fragment[position].isdigit():
            while position < length and fragment[position].isdigit():
                position += 1
            if position < length and fragment[position] == "~":
                position += 1
                while position < length and fragment[position].isdigit():
                    position += 1
        elif position < length and fragment[position] == "$":
            position += 1
        elif position < length and fragment[position] in {"+", "~"}:
            position += 1
            while position < length and fragment[position].isdigit():
                position += 1
        elif position < length and fragment[position] == "/":
            position = consume_regex(position + 1, "/")
            while position < length and fragment[position] in {"I", "M"}:
                position += 1
        elif position + 1 < length and fragment[position] == "\\":
            delimiter = fragment[position + 1]
            position = consume_regex(position + 2, delimiter)
            while position < length and fragment[position] in {"I", "M"}:
                position += 1
        if position == before:
            break
        position = skip_space(position)
        if address_index == 0 and position < length and fragment[position] == ",":
            position = skip_space(position + 1)
            continue
        break
    while position < length and fragment[position] == "!":
        position = skip_space(position + 1)
    return fragment[position] if position < length else ""


_SAFE_PYTHON_FLAGS: frozenset[str] = frozenset({
    "-B", "-E", "-I", "-O", "-OO", "-P", "-q", "-s", "-S", "-u",
})


def _interpreter_target(command: list[str], executable: str) -> tuple[str, str | None]:
    """Parse a deliberately small, unambiguous direct-interpreter grammar."""
    args = command[1:]
    if executable in {"python", "python3", "py"}:
        index = 0
        while index < len(args) and args[index] in _SAFE_PYTHON_FLAGS:
            index += 1
        if index >= len(args):
            return "none", None
        token = args[index]
        if token == "-c":
            # The code is one argv item, so the approved context binds it
            # exactly; extra arguments would reach the program as sys.argv.
            if index + 2 != len(args) or not args[index + 1].strip():
                raise HardDenyError(
                    "Hard security denial: Python '-c' takes exactly one non-empty code argument"
                )
            return "inline", args[index + 1]
        if token == "-m":
            if index + 1 >= len(args) or not args[index + 1] or args[index + 1].startswith("-"):
                raise HardDenyError("Hard security denial: Python -m requires a module name")
            return "module", args[index + 1]
        if token == "--":
            return ("script", args[index + 1]) if index + 1 < len(args) else ("none", None)
        if token.startswith("-"):
            raise HardDenyError(
                f"Hard security denial: unsupported or clustered Python option '{token}'"
            )
        return "script", token

    if executable == "node":
        args = _without_allowed_node_options(args)
    if not args:
        return "none", None
    token = args[0]
    if executable == "php" and token in {"-f", "--file"}:
        if len(args) < 2:
            raise HardDenyError("Hard security denial: PHP file mode requires a script")
        return "script", args[1]
    if executable == "php" and token.startswith("--file="):
        return "script", token.partition("=")[2]
    if token == "--":
        return ("script", args[1]) if len(args) > 1 else ("none", None)
    if token.startswith("-"):
        raise HardDenyError(
            f"Hard security denial: unsupported or clustered {executable} option '{token}'"
        )
    return "script", token


def _direct_interpreter_script(command: list[str], executable: str) -> str | None:
    mode, target = _interpreter_target(command, executable)
    return target if mode == "script" else None


def _script_file_arguments(command: list[str]) -> list[str]:
    values: list[str] = []
    executable = canonical_executable_name(command[0]) if command else ""
    index = 1
    while index < len(command):
        token = command[index]
        if token in {"-f", "--file", "-E", "--exec"} and index + 1 < len(command):
            values.append(command[index + 1])
            index += 2
            continue
        if token.startswith("--file="):
            values.append(token.partition("=")[2])
        elif token.startswith("--exec="):
            values.append(token.partition("=")[2])
        elif token.startswith("-f") and token != "-f":
            values.append(token[2:])
        elif token.startswith("-E") and token != "-E":
            values.append(token[2:])
        elif executable == "sed" and token.startswith("-") and not token.startswith("--"):
            cluster = token[1:]
            for position, option in enumerate(cluster):
                if option == "f":
                    attached = cluster[position + 1 :]
                    if attached:
                        values.append(attached)
                    elif index + 1 < len(command):
                        values.append(command[index + 1])
                        index += 1
                    break
                # These options consume the remainder (or next token), so a
                # later 'f' byte is script data/numeric data, not an option.
                if option in {"e", "l"}:
                    break
        index += 1
    return values


def _option_values(
    command: list[str],
    names: set[str],
    *,
    casefold: bool = False,
) -> list[str]:
    """Collect split, equals, and attached single-letter option values."""
    normalized_names = {name.casefold() if casefold else name for name in names}
    values: list[str] = []
    index = 1
    while index < len(command):
        token = command[index]
        comparable = token.casefold() if casefold else token
        flag, separator, value = comparable.partition("=")
        if flag in normalized_names:
            if separator:
                # Slice the original token so path case is preserved.
                values.append(token.partition("=")[2])
                index += 1
                continue
            if index + 1 < len(command):
                values.append(command[index + 1])
                index += 2
                continue
            index += 1
            continue
        matched_attached = False
        for name in normalized_names:
            if len(name) == 2 and name.startswith("-") and comparable.startswith(name) and comparable != name:
                values.append(token[len(name) :])
                matched_attached = True
                break
        index += 1
        if matched_attached:
            continue
    return values


def _option_value(
    command: list[str], names: set[str], *, casefold: bool = False
) -> str | None:
    values = _option_values(command, names, casefold=casefold)
    return values[0] if values else None


_NODE_TSX_IMPORTS = {"tsx", "tsx/esm"}


def _without_allowed_node_options(args: list[str]) -> list[str]:
    """Drop the node options a test run needs: `--import tsx[/esm]`, `--test`,
    `--test-name-pattern PATTERN` (also the `=` forms).

    `--import tsx` is the repository's launcher for TypeScript tests and runs
    the same workspace tsx that `./node_modules/.bin/tsx` already runs; any
    other `--import` value stays a preload and is denied by the caller.
    `--test` and `--test-name-pattern` only select which workspace test files
    and cases run. Reporters, coverage and every other node option stay denied.
    """
    kept: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        has_value = index + 1 < len(args)
        if arg == "--import" and has_value and args[index + 1] in _NODE_TSX_IMPORTS:
            index += 2
            continue
        if arg.startswith("--import=") and arg[len("--import=") :] in _NODE_TSX_IMPORTS:
            index += 1
            continue
        if arg == "--test-name-pattern" and has_value:
            index += 2
            continue
        if arg == "--test" or arg.startswith("--test-name-pattern="):
            index += 1
            continue
        kept.append(arg)
        index += 1
    return kept


class InterpreterValidationMixin:
    """Interpreter, awk and sed validation for TerminalExecutor.

    Uses no instance attributes of its own; calls ``_resolve_workspace_reference``
    from ArgumentValidationMixin.
    """

    def _validate_interpreter_modes(
        self,
        command: list[str],
        executable: str,
        work_dir: Path,
    ) -> None:
        if is_exact_version_probe(command):
            return
        if (
            executable in {"python", "python3", "py"}
            and any(arg == "-c" or arg.startswith("-c") for arg in command[1:])
            and _interpreter_target(command, executable)[0] != "inline"
        ):
            raise HardDenyError(
                "Hard security denial: Python '-c' is allowed only as "
                "`python -c CODE` with one code argument and no further arguments"
            )
        if executable == "node" and any(
            arg in {"-e", "--eval", "-p", "--print"}
            or arg.startswith(("--eval=", "--print=", "-e"))
            for arg in command[1:]
        ):
            raise HardDenyError("Hard security denial: inline Node execution is forbidden")
        if executable == "node" and any(
            arg in {"-r", "--require", "--loader", "--import"}
            or arg.startswith(("-r", "--require=", "--loader=", "--experimental-loader=", "--import="))
            or arg in {"--experimental-loader", "--run"}
            or arg.startswith("--run=")
            for arg in _without_allowed_node_options(command[1:])
        ):
            raise HardDenyError("Hard security denial: Node preload/loader execution is forbidden")
        if executable == "ruby" and any(
            arg == "-e"
            or arg.startswith("-e")
            or arg in {"-r", "--require", "-I", "--include", "-C", "-X", "-S", "-x"}
            or arg.startswith(("--require=", "--include=", "-r", "-I", "-C", "-X", "-S", "-x"))
            for arg in command[1:]
        ):
            raise HardDenyError(
                "Hard security denial: Ruby inline/preload/search/chdir modes are forbidden"
            )
        if executable == "perl" and any(
            arg in {"-e", "-E", "-M", "-m", "-I", "-S", "-x"}
            or arg.startswith(("-e", "-E", "-M", "-m", "-I", "-S", "-x"))
            for arg in command[1:]
        ):
            raise HardDenyError(
                "Hard security denial: Perl inline/preload/search/chdir modes are forbidden"
            )
        if executable == "php" and any(
            arg in {"-a", "-r", "-B", "-R", "-E", "-d", "-c", "-S", "--interactive", "--define", "--php-ini", "--server", "--process-begin", "--process-code", "--process-end"}
            or arg.startswith(("-r", "-B", "-R", "-E", "-d", "-c", "-S", "--define=", "--php-ini=", "--server=", "--process-begin=", "--process-code=", "--process-end="))
            for arg in command[1:]
        ):
            raise HardDenyError(
                "Hard security denial: PHP inline/config/server execution modes are forbidden"
            )
        if executable in {"python", "python3", "py"} and any(
            arg in {"-i", "--inspect"} or arg.startswith("--inspect=")
            for arg in command[1:]
        ):
            raise HardDenyError("Hard security denial: Python interactive mode is forbidden")
        if executable == "node" and any(
            arg in {"-i", "--interactive"} for arg in command[1:]
        ):
            raise HardDenyError("Hard security denial: Node interactive mode is forbidden")
        if executable in {"python", "python3", "py", "node", "ruby", "perl", "php"}:
            mode, script = _interpreter_target(command, executable)
            if script == "-":
                raise HardDenyError("Hard security denial: interpreter stdin execution is forbidden")
            if mode == "none":
                raise HardDenyError(
                    f"Hard security denial: '{command[0]}' must select a concrete workspace script"
                )
        if executable in {"awk", "gawk"}:
            self._validate_awk(command, work_dir)
        if executable == "sed":
            self._validate_sed(command, work_dir)

    def _validate_awk(self, command: list[str], work_dir: Path) -> None:
        programs: list[str] = []
        index = 1
        while index < len(command):
            token = command[index]
            if (
                token in {"-p", "-o", "-d", "-D", "--profile", "--pretty-print", "--dump-variables", "--debug"}
                or token.startswith(("--profile=", "--pretty-print=", "--dump-variables=", "--debug="))
                or (
                    token.startswith("-")
                    and not token.startswith("--")
                    and len(token) > 2
                    and token[1] in {"p", "o", "d", "D"}
                )
            ):
                raise HardDenyError("Hard security denial: AWK diagnostic/output-file modes are forbidden")
            if (
                token in {"-i", "--include"}
                or (token.startswith("-i") and token != "-i")
                or token.startswith("--include=")
            ):
                raise HardDenyError("Hard security denial: AWK source inclusion is forbidden")
            if (
                token in {"-l", "--load"}
                or (token.startswith("-l") and token != "-l")
                or token.startswith("--load=")
            ):
                raise HardDenyError("Hard security denial: dynamic AWK extensions are forbidden")
            if token in {"-f", "--file", "-E", "--exec"}:
                if index + 1 >= len(command):
                    raise HardDenyError("Hard security denial: AWK program-file option requires a file")
                programs.append(self._read_inspectable_script(command[index + 1], work_dir, "AWK"))
                index += 2
                continue
            if token.startswith("-f") and token != "-f":
                programs.append(self._read_inspectable_script(token[2:], work_dir, "AWK"))
                index += 1
                continue
            if token == "--":
                if index + 1 >= len(command):
                    raise HardDenyError("Hard security denial: AWK requires a program after '--'")
                programs.append(command[index + 1])
                break
            if token.startswith("-E") and token != "-E":
                programs.append(self._read_inspectable_script(token[2:], work_dir, "AWK"))
                index += 1
                continue
            if token.startswith("--file="):
                programs.append(self._read_inspectable_script(token.partition("=")[2], work_dir, "AWK"))
                index += 1
                continue
            if token.startswith("--exec="):
                programs.append(self._read_inspectable_script(token.partition("=")[2], work_dir, "AWK"))
                index += 1
                continue
            if token in {"-e", "--source"}:
                if index + 1 >= len(command):
                    raise HardDenyError("Hard security denial: AWK source option requires a program")
                programs.append(command[index + 1])
                index += 2
                continue
            if token.startswith("-e") and token != "-e":
                programs.append(token[2:])
                index += 1
                continue
            if token.startswith("--source="):
                programs.append(token.partition("=")[2])
                index += 1
                continue
            if token in {"-v", "--assign", "-F", "--field-separator"}:
                if index + 1 >= len(command):
                    raise HardDenyError(f"Hard security denial: AWK option '{token}' requires a value")
                index += 2
                continue
            if (
                (token.startswith("-v") and token != "-v")
                or (token.startswith("-F") and token != "-F")
                or token.startswith(("--assign=", "--field-separator="))
            ):
                index += 1
                continue
            if token == "-W" or token.startswith("-W"):
                raise HardDenyError(
                    "Hard security denial: implementation-specific AWK -W modes are forbidden"
                )
            if token in _AWK_SAFE_LONG_OPTIONS or token.startswith("--lint="):
                index += 1
                continue
            if token.startswith("--"):
                raise HardDenyError(
                    f"Hard security denial: unsupported or abbreviated AWK option '{token}'"
                )
            if token in _AWK_SAFE_SHORT_OPTIONS:
                index += 1
                continue
            if token.startswith("-"):
                raise HardDenyError(
                    f"Hard security denial: unsupported or ambiguous AWK option '{token}'"
                )
            programs.append(token)
            # The first positional token is the inline program; remaining
            # positionals are input files and need no language scan.
            break

        for program in programs:
            stripped = _strip_awk_strings(program.replace("\\\n", ""))
            if _AWK_SYSTEM_RE.search(stripped):
                raise HardDenyError("Hard security denial: AWK system() calls are forbidden")
            if _AWK_LOAD_RE.search(stripped):
                raise HardDenyError("Hard security denial: AWK dynamic extension loading is forbidden")
            if _AWK_INCLUDE_RE.search(stripped):
                raise HardDenyError("Hard security denial: AWK source inclusion is forbidden")
            if re.search(r"@[A-Za-z_][A-Za-z0-9_:]*\s*\(", stripped):
                raise HardDenyError("Hard security denial: indirect AWK function execution is forbidden")
            if _AWK_EXTERNAL_PIPE_RE.search(stripped):
                raise HardDenyError("Hard security denial: AWK getline with a pipe/external pipe is forbidden")
            if re.search(r">{1,2}", stripped):
                raise HardDenyError("Hard security denial: AWK output redirection is forbidden")

    def _validate_sed(self, command: list[str], work_dir: Path) -> None:
        scripts: list[str] = []
        explicit_scripts = False
        index = 1
        while index < len(command):
            token = command[index]
            lowered = token.casefold()
            if lowered == "--in-place" or lowered.startswith("--in-place="):
                raise HardDenyError("Hard security denial: In-place sed mode is forbidden")
            if token in {"-e", "--expression"}:
                if index + 1 >= len(command):
                    raise HardDenyError("Hard security denial: sed expression flag requires a script")
                scripts.append(command[index + 1])
                explicit_scripts = True
                index += 2
                continue
            if token.startswith("--expression="):
                scripts.append(token.partition("=")[2])
                explicit_scripts = True
                index += 1
                continue
            if token in {"-f", "--file"}:
                if index + 1 >= len(command):
                    raise HardDenyError("Hard security denial: sed -f requires a script file")
                scripts.append(self._read_inspectable_script(command[index + 1], work_dir, "sed"))
                explicit_scripts = True
                index += 2
                continue
            if token.startswith("--file="):
                scripts.append(self._read_inspectable_script(token.partition("=")[2], work_dir, "sed"))
                explicit_scripts = True
                index += 1
                continue
            if lowered in {
                "--quiet", "--silent", "--regexp-extended", "--posix", "--sandbox",
                "--unbuffered", "--null-data", "--debug", "--separate",
            }:
                index += 1
                continue
            if token.startswith("--"):
                raise HardDenyError(f"Hard security denial: unsupported sed option '{token}'")
            if token.startswith("-") and not token.startswith("--"):
                cluster = token[1:]
                position = 0
                consumed_script = False
                while position < len(cluster):
                    option = cluster[position]
                    if option == "i":
                        raise HardDenyError("Hard security denial: In-place sed mode is forbidden")
                    if option == "e":
                        attached = cluster[position + 1 :]
                        if attached:
                            scripts.append(attached)
                            consumed_script = True
                        elif index + 1 < len(command):
                            scripts.append(command[index + 1])
                            index += 1
                            consumed_script = True
                        else:
                            raise HardDenyError("Hard security denial: sed -e requires a script")
                        explicit_scripts = True
                        break
                    if option == "f":
                        attached = cluster[position + 1 :]
                        if not attached:
                            if index + 1 >= len(command):
                                raise HardDenyError("Hard security denial: sed -f requires a script file")
                            attached = command[index + 1]
                            index += 1
                        scripts.append(self._read_inspectable_script(attached, work_dir, "sed"))
                        explicit_scripts = True
                        consumed_script = True
                        break
                    if option == "l":
                        attached = cluster[position + 1 :]
                        if attached:
                            if not attached.isdigit():
                                raise HardDenyError(
                                    "Hard security denial: ambiguous sed -l option is forbidden"
                                )
                        else:
                            if index + 1 >= len(command) or not command[index + 1].isdigit():
                                raise HardDenyError(
                                    "Hard security denial: sed -l requires a numeric line length"
                                )
                            index += 1
                        break
                    position += 1
                index += 1
                if consumed_script:
                    continue
                continue
            if not explicit_scripts and not scripts:
                scripts.append(token)
            index += 1

        for script in scripts:
            if _sed_script_is_dangerous(script):
                raise HardDenyError(
                    "Hard security denial: sed execute/write commands (e, w, W, s///e, s///w) are forbidden"
                )

    def _read_inspectable_script(self, token: str, work_dir: Path, language: str) -> str:
        if token == "-":
            raise HardDenyError(
                f"Hard security denial: {language} program input from stdin is forbidden"
            )
        path = self._resolve_workspace_reference(token, work_dir, f"{language} script")
        if not path.exists():
            raise HardDenyError(f"Hard security denial: {language} script file does not exist")
        if not path.is_file():
            raise HardDenyError(f"Hard security denial: {language} script is not a regular file")
        try:
            if path.stat().st_size > 1024 * 1024:
                raise HardDenyError(f"Hard security denial: {language} script is too large to inspect")
            return path.read_text(encoding="utf-8", errors="strict")
        except UnicodeError as error:
            raise HardDenyError(f"Hard security denial: {language} script is not UTF-8") from error
        except OSError as error:
            raise HardDenyError(f"Hard security denial: cannot inspect {language} script: {error}") from error
