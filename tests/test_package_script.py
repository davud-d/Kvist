from __future__ import annotations

import importlib


def test_package_script_import_has_main():
    module = importlib.import_module("package_script")
    assert callable(module.main)
