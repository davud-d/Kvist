# -*- coding: utf-8 -*-
from __future__ import annotations

import io

import pytest

import event_stdio
import main


def test_main_dispatches_stdio_frontend_without_starting_terminal(monkeypatch, tmp_path):
    captured = {}

    def fake_launch(workspace, goal, build_agent_fn, **options):
        captured.update(
            workspace=workspace,
            goal=goal,
            build_agent_fn=build_agent_fn,
            options=options,
        )

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
            "--planner-model",
            "planner-id",
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
                "planner": "planner-id",
            },
            "routed_role": "devops",
            "approval_mode": "deny",
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
