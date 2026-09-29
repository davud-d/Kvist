# -*- coding: utf-8 -*-
from __future__ import annotations

import io

import pytest

from kernel import event_stdio
from kernel import main


def test_main_dispatches_stdio_frontend_without_starting_terminal(monkeypatch, tmp_path):
    captured = {}

    def fake_launch(workspace, goal, build_agent_fn, **options):
        captured.update(
            workspace=workspace,
            goal=goal,
            build_agent_fn=build_agent_fn,
            options=options,
        )

    # resolve_startup_role() (main.py) reads the saved /role preference from
    # ~/.kvist/config.json; the autouse HOME-isolation fixture in conftest.py
    # keeps this deterministic across the machine running it.
    monkeypatch.setattr(
        main.sys,
        "argv",
        ["kvist", "--workspace", str(tmp_path), "--frontend", "stdio"],
    )
    monkeypatch.setattr(main.sys, "stdin", io.StringIO("Inspect the project\n"))
    monkeypatch.setattr(event_stdio, "launch", fake_launch)

    main.main()

    assert captured["workspace"] == str(tmp_path)
    assert captured["goal"] == "Inspect the project"
    assert captured["build_agent_fn"] is main.build_agent
    assert captured["options"] == {
        "provider_override": None,
        "models_override": None,
        "routed_role": None,
        "approval_mode": "ask",
        "resume": False,
    }


def test_main_uses_default_workspace_when_stdio_workspace_is_omitted(monkeypatch, tmp_path):
    captured = {}

    def fake_launch(workspace, goal, build_agent_fn, **options):
        captured.update(workspace=workspace, goal=goal)

    monkeypatch.setattr(main, "_documents_directory", lambda: tmp_path / "Documents")
    monkeypatch.setattr(
        main.sys,
        "argv",
        ["kvist", "--frontend", "stdio"],
    )
    monkeypatch.setattr(main.sys, "stdin", io.StringIO("Inspect the project\n"))
    monkeypatch.setattr(event_stdio, "launch", fake_launch)

    main.main()

    assert captured == {
        "workspace": str((tmp_path / "Documents" / "Kvist Workspace").resolve()),
        "goal": "Inspect the project",
    }


def test_main_dispatches_stdio_run_overrides(monkeypatch, tmp_path):
    captured = {}

    def fake_launch(workspace, goal, build_agent_fn, **options):
        captured.update(workspace=workspace, goal=goal, options=options)

    monkeypatch.setattr(
        main.sys,
        "argv",
        [
            "kvist",
            "--workspace",
            str(tmp_path),
            "--frontend",
            "stdio",
            "--provider",
            "omniroute",
            "--fast-model",
            "fast-id",
            "--smart-model",
            "smart-id",
            "--role",
            "devops",
            "--approval-mode",
            "deny",
        ],
    )
    monkeypatch.setattr(main.sys, "stdin", io.StringIO("Deploy safely\n"))
    monkeypatch.setattr(event_stdio, "launch", fake_launch)

    main.main()

    assert captured == {
        "workspace": str(tmp_path),
        "goal": "Deploy safely",
        "options": {
            "provider_override": "omniroute",
            "models_override": {
                "fast": "fast-id",
                "smart": "smart-id",
            },
            "routed_role": "devops",
            "approval_mode": "deny",
            "resume": False,
        },
    }


def test_main_rejects_partial_model_override(monkeypatch, tmp_path):
    monkeypatch.setattr(
        main.sys,
        "argv",
        [
            "kvist",
            "--workspace",
            str(tmp_path),
            "--frontend",
            "stdio",
            "--fast-model",
            "fast-id",
        ],
    )
    monkeypatch.setattr(main.sys, "stdin", io.StringIO("Inspect\n"))
    monkeypatch.setattr(
        event_stdio,
        "launch",
        lambda *args, **kwargs: pytest.fail("launch must not run"),
    )

    with pytest.raises(SystemExit) as raised:
        main.main()

    assert raised.value.code == 2


def test_main_dispatches_stdio_resume_without_reading_a_goal(monkeypatch, tmp_path):
    captured = {}

    def fake_launch(workspace, goal, build_agent_fn, **options):
        captured.update(goal=goal, options=options)

    monkeypatch.setattr(
        main.sys,
        "argv",
        ["kvist", "--workspace", str(tmp_path), "--frontend", "stdio", "--resume"],
    )
    # stdin must stay untouched: resume has no goal line.
    monkeypatch.setattr(main.sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(event_stdio, "launch", fake_launch)

    main.main()

    assert captured["goal"] == ""
    assert captured["options"]["resume"] is True
