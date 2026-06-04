from __future__ import annotations

import importlib


def test_run_task_import_has_run_entrypoint():
    module = importlib.import_module("run_task")
    assert callable(module.run)
