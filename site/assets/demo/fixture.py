#!/usr/bin/env python3
"""Create the throwaway workspace the demo cast is recorded against.

Same code for both locales; only the goal sentence in drive.py differs, so the
two casts are the same task asked in two languages.
"""
from __future__ import annotations

import pathlib
import sys

SLUGIFY = '''"""URL slugs for the docs build."""
from __future__ import annotations

import re

_KEEP = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    return _KEEP.sub("-", title.lower())
'''

CHECK = '''"""Tiny stdlib-only check runner. No pytest in this workspace."""
from __future__ import annotations

from slugify import slugify

CASES = [
    ("Hello World", "hello-world"),
    ("  Release 2.0  ", "release-2-0"),
    ("C++ // notes", "c-notes"),
    ("already-fine", "already-fine"),
]


def main() -> int:
    bad = 0
    for title, want in CASES:
        got = slugify(title)
        mark = "ok  " if got == want else "FAIL"
        if got != want:
            bad += 1
        print(f"{mark} {title!r} -> {got!r} (want {want!r})")
    print(f"\\n{len(CASES) - bad}/{len(CASES)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

KVIST_MD = """# Project instructions

Run the checks with `python3 check.py` from the workspace root.
Keep `slugify.py` dependency-free: standard library only.
"""

PERMISSIONS = """{
  "allow": ["python3 check.py"],
  "deny": [],
  "ask": []
}
"""


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: fixture.py <workspace> [locale]", file=sys.stderr)
        return 2
    root = pathlib.Path(sys.argv[1])
    (root / ".kvist").mkdir(parents=True, exist_ok=True)
    (root / "slugify.py").write_text(SLUGIFY, encoding="utf-8")
    (root / "check.py").write_text(CHECK, encoding="utf-8")
    (root / "KVIST.md").write_text(KVIST_MD, encoding="utf-8")
    (root / ".kvist" / "permissions.json").write_text(PERMISSIONS, encoding="utf-8")
    print(f"fixture ready at {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
