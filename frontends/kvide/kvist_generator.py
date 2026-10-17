#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kvide commit-message generator backed by ``kvist-core --commit-message``.

kvide runs this script as a generator (see kvide's docs/generators.md): it
reads ``{"version", "files", "diff"}`` on stdin and prints the message, or a
JSON error envelope on failure. The model call, the provider choice and the
protected-path filter all stay in Kvist; this file only translates between the
two payloads, so it uses the standard library alone.

    python kvist_generator.py --core ~/.local/share/kvist/kvist-core
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

MAX_RECENT_SUBJECTS = 15
_DIFF_HEADER_RE = re.compile(r"^diff --git a/.+? b/(?P<path>.+)$")
_NEW_FILE_PREFIX = "New file: "


def fail(code: str, message: str) -> None:
    print(json.dumps({"error": {"code": code, "message": message}}))
    sys.exit(1)


def split_diff(diff: str, files: list[str]) -> list[dict[str, str]]:
    """Cut kvide's diff into one patch per file.

    kvide appends ``New file: <path>`` blocks for untracked files after the
    ``git diff`` output, so both kinds of section header start a new patch.
    """
    patches: list[dict[str, str]] = []
    path: str | None = None
    lines: list[str] = []

    def close() -> None:
        if path is not None:
            patches.append({"path": path, "patch": "\n".join(lines)})

    for line in diff.splitlines():
        header = _DIFF_HEADER_RE.match(line)
        if header is not None:
            close()
            path, lines = header.group("path"), [line]
        elif line.startswith(_NEW_FILE_PREFIX) and line[len(_NEW_FILE_PREFIX):] in files:
            close()
            path, lines = line[len(_NEW_FILE_PREFIX):], [line]
        elif path is not None:
            lines.append(line)
    close()
    return patches


def recent_subjects() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "log", f"-{MAX_RECENT_SUBJECTS}", "--format=%s"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []  # history only tunes the style; a repository without it still works
    return result.stdout.splitlines() if result.returncode == 0 else []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--core", required=True, help="path to the kvist-core binary")
    args = parser.parse_args()

    context = json.load(sys.stdin)
    if context.get("version") != 1:
        fail("unsupported_version", f"Unsupported protocol version: {context.get('version')!r}")

    patches = split_diff(context["diff"], context["files"])
    payload = {"files": patches, "recent_subjects": recent_subjects()}
    try:
        result = subprocess.run(
            [args.core, "--commit-message"],
            input=json.dumps(payload).encode("utf-8"),
            capture_output=True,
        )
    except OSError as error:
        fail("core_missing", f"Cannot run {args.core}: {error}")

    try:
        reply = json.loads(result.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        tail = result.stderr.decode("utf-8", errors="replace").strip()[-300:]
        fail("bad_reply", tail or "kvist-core returned no JSON.")
    if not reply.get("ok"):
        fail("kvist_error", str(reply.get("error") or "Unknown error."))
    print(reply["message"])


if __name__ == "__main__":
    main()
