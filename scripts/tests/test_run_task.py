# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import sys

from kernel.sandbox import sandbox


def test_run_task_import_has_main_entrypoint():
    module = importlib.import_module("run_task")
    assert callable(module.main)


def test_container_flag_is_refused_outside_a_container(monkeypatch, tmp_path, capsys):
    module = importlib.import_module("run_task")
    monkeypatch.setattr(sandbox, "_CONTAINER_MARKERS", (tmp_path / "absent",))
    monkeypatch.setattr(sys, "argv", ["run_task.py", "--container", "--workspace", str(tmp_path), "do it"])

    assert module.main() == 2
    assert "not inside a Docker or Podman container" in capsys.readouterr().err
