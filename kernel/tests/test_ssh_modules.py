# -*- coding: utf-8 -*-
"""Every module of kernel.ssh can be imported (a stray character in one once made the whole remote core fail to start)."""
from __future__ import annotations

import importlib
import pkgutil

import kernel.ssh


def test_every_module_imports():
    names = [module.name for module in pkgutil.iter_modules(kernel.ssh.__path__, "kernel.ssh.")]

    assert len(names) >= 14
    for name in names:
        importlib.import_module(name)


def test_every_sentence_the_code_asks_for_exists_in_both_languages():
    """A missing key only fails at the moment a person needs that sentence, so look for them all."""
    import re
    from pathlib import Path

    from kernel.ssh import messages

    used = set()
    for path in Path(kernel.ssh.__path__[0]).glob("*.py"):
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'messages\.text\(\s*"([a-z_]+)"', text))
        used |= set(re.findall(r'_say\([^,]+,\s*"([a-z_]+)"', text))
    used |= {"host_key", "auth", "not_found", "no_service", "unreachable", "rotated", "interrupted", "gave_up", "old_kvist"}  # named by classify()

    missing = sorted(key for key in used if key not in messages._TEXTS)
    assert missing == []
    assert all({"en", "ru"} <= set(variants) for variants in messages._TEXTS.values())
