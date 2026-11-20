# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kernel.ide.completion import CompletionRequest, clean_model_output, prepare_completion
from kernel.plugins import plain_plugins
from kernel.plugins.plain_plugins import PluginError, discover, expand, read_plugin, typed_command
from kernel.plugins.paths import config_dir
from kernel.plugins.sandbox import runner_unavailable_reason
from kernel.protocol.core_manifest import build_manifest

NOVIBECODE = '''"""Teach instead of writing the code."""
import kvist


def command():
    return "novibecode"


def run(request):
    print("noise a plugin left in")
    return kvist.read("novibecode.md") + "\\nThe person asks: " + request
'''


@pytest.fixture(autouse=True)
def _sandbox_counts_as_present(request, monkeypatch):
    """Approval and expansion are checked on every system; the tests that really run a plugin skip on their own."""
    if "sandbox" not in request.node.name:
        monkeypatch.setattr(plain_plugins, "runner_unavailable_reason", lambda: "")


def _plugin(root: Path, folder: str, source: str) -> Path:
    """``<root>/<folder>/plugin.py`` with ``source``."""
    (root / folder).mkdir(parents=True, exist_ok=True)
    path = root / folder / "plugin.py"
    path.write_text(source, encoding="utf-8")
    return path


def _run(path: Path, scratch: Path, request: str = "") -> str:
    command = read_plugin(path)
    return plain_plugins.run_plugin(command, plain_plugins.snapshot_folder(command, scratch), request)


def _simple(name: str, text: str = "be brief") -> str:
    return f'def command():\n    return "{name}"\n\n\ndef run(request):\n    return "{text}"\n'


class _UI:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def confirm(self, title: str, body: str) -> bool:
        self.asked.append(title)
        return self.answer


def _agent(workspace: Path, *, answer: bool = True, interactive: bool = True) -> SimpleNamespace:
    control = SimpleNamespace(awaiting_input=lambda waiting: None)
    return SimpleNamespace(
        executor=SimpleNamespace(workspace=str(workspace)), config=SimpleNamespace(interactive=interactive),
        control=control, _ui=_UI(answer), _state=SimpleNamespace(response_language="en"),
    )


def test_the_name_and_description_are_read_without_running_the_file(tmp_path):
    path = _plugin(tmp_path / "plugins", "teach", NOVIBECODE.replace("import kvist", "raise SystemExit('ran')"))
    command = read_plugin(path)
    assert (command.name, command.description) == ("novibecode", "Teach instead of writing the code.")


@pytest.mark.parametrize(("source", "said"), [
    ("def run(request):\n    return ''\n", "no command()"),
    ("def command():\n    return 'x'\n", "no run(request)"),
    ("NAME = 'x'\ndef command():\n    return NAME\ndef run(request):\n    return ''\n", "plain text"),
    ("def command():\n    return 'X Y'\ndef run(request):\n    return ''\n", "lowercase"),
    ("def command():\n    return 'x'\ndef run():\n    return ''\n", "exactly one argument"),
    ("def command(:\n", "syntax error on line 1"),
])
def test_a_file_that_declares_no_usable_command_says_why(tmp_path, source, said):
    _plugin(tmp_path / "plugins", "bad", source)
    found = discover(tmp_path)
    assert found.commands == {}
    assert len(found.problems) == 1 and said in found.problems[0]


def test_a_loose_file_or_a_misnamed_one_is_pointed_to_plugin_py(tmp_path):
    root = tmp_path / "plugins"
    root.mkdir()
    (root / "teach.py").write_text(_simple("teach"), encoding="utf-8")
    (root / "other").mkdir()
    (root / "other" / "other.py").write_text(_simple("other"), encoding="utf-8")
    # A folder with no Python in it, like the installed skills under ~/.kvist/plugins/skill, is no plugin.
    (root / "notes").mkdir()
    (root / "notes" / "readme.md").write_text("hi", encoding="utf-8")
    found = discover(tmp_path)
    assert found.commands == {}
    assert [problem.split(": ", 1)[1] for problem in found.problems] == [
        f"a plugin lives in its own folder; move it to {root / 'teach' / 'plugin.py'}",
        "the plugin's file must be named plugin.py (found other.py)",
    ]


def test_a_plugin_cannot_take_a_built_in_name(tmp_path):
    _plugin(tmp_path / "plugins", "mine", _simple("new"))
    found = discover(tmp_path)
    assert found.commands == {} and "built-in" in found.problems[0]


def test_the_project_folder_wins_a_name_over_the_global_one(tmp_path):
    _plugin(config_dir() / "plugins", "teach", _simple("teach", "global"))
    _plugin(config_dir() / "plugins", "other", _simple("other"))
    local = _plugin(tmp_path / "plugins", "teach", _simple("teach", "project"))
    found = discover(tmp_path)
    assert sorted(found.commands) == ["other", "teach"]
    assert found.commands["teach"].path == local


def test_only_a_typed_plugin_name_is_a_plugin_command(tmp_path):
    _plugin(tmp_path / "plugins", "teach", _simple("teach"))
    command, request = typed_command("/Teach  explain loops ", tmp_path)
    assert (command.name, request) == ("teach", "explain loops")
    assert typed_command("/teach", tmp_path)[1] == ""
    assert typed_command("/website shop", tmp_path) is None
    assert typed_command("teach me", tmp_path) is None


def test_approval_is_asked_once_per_code_of_the_folder(tmp_path, monkeypatch):
    ran: list[bytes] = []

    def fake_run(command, snapshot, request):
        ran.append((snapshot / "plugin.py").read_bytes())
        return "be kind"

    monkeypatch.setattr(plain_plugins, "run_plugin", fake_run)
    path = _plugin(tmp_path / "plugins", "teach", _simple("teach"))
    command = read_plugin(path)
    agent = _agent(tmp_path)
    assert "be kind" in expand(agent, command, "hi")
    expand(agent, command, "again")
    assert agent._ui.asked == ["Run the plugin /teach?"]
    # Instructions are not code: editing them asks nothing.
    (path.parent / "teach.md").write_text("new words", encoding="utf-8")
    expand(agent, command, "after new words")
    assert len(agent._ui.asked) == 1
    path.write_text(_simple("teach", "changed"), encoding="utf-8")
    expand(agent, command, "after the change")
    assert len(agent._ui.asked) == 2
    assert ran[-1] == path.read_bytes()
    # A helper module runs too, so a new one needs its own approval.
    (path.parent / "helpers.py").write_text("X = 1\n", encoding="utf-8")
    expand(agent, command, "with a helper")
    assert len(agent._ui.asked) == 3


def test_without_a_sandbox_nothing_is_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(plain_plugins, "runner_unavailable_reason", lambda: "executable skills need Bubblewrap")
    agent = _agent(tmp_path)
    command = read_plugin(_plugin(tmp_path / "plugins", "teach", _simple("teach")))
    with pytest.raises(PluginError, match="plugins need Bubblewrap"):
        expand(agent, command, "hi")
    assert agent._ui.asked == []


def test_a_refused_or_unanswerable_plugin_does_not_run(tmp_path, monkeypatch):
    monkeypatch.setattr(plain_plugins, "run_plugin", lambda *args: pytest.fail("must not run"))
    command = read_plugin(_plugin(tmp_path / "plugins", "teach", _simple("teach")))
    with pytest.raises(PluginError, match="did not allow"):
        expand(_agent(tmp_path, answer=False), command, "hi")
    with pytest.raises(PluginError, match="not approved yet"):
        expand(_agent(tmp_path, interactive=False), command, "hi")


def test_the_model_gets_the_request_and_the_plugin_text(tmp_path, monkeypatch):
    monkeypatch.setattr(plain_plugins, "run_plugin", lambda *args: "Only give hints.")
    command = read_plugin(_plugin(tmp_path / "plugins", "teach", _simple("teach")))
    goal = expand(_agent(tmp_path), command, "write a parser")
    assert goal.startswith("write a parser\n")
    assert '<plugin_instructions command="teach">\nOnly give hints.\n</plugin_instructions>' in goal


def test_agent_run_expands_a_plugin_and_shows_a_failure_instead_of_calling_the_model(agent_factory, tmp_path,
                                                                                      monkeypatch):
    agent = agent_factory(interactive=True)
    _plugin(tmp_path / "plugins", "teach", _simple("teach"))
    monkeypatch.setattr(plain_plugins, "run_plugin", lambda *args: "Only give hints.")
    monkeypatch.setattr(agent, "_ui", SimpleNamespace(**{name: getattr(agent._ui, name) for name in dir(agent._ui)
                                                         if not name.startswith("__")}, confirm=lambda *a: True))
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(agent, "_run_turn", lambda goal, role, images, language_text: seen.append((goal, language_text)))
    agent.run("/teach объясни циклы")
    assert "Only give hints." in seen[0][0] and seen[0][1] == "объясни циклы"

    def broken(*args):
        raise PluginError("/teach failed: boom")

    monkeypatch.setattr(plain_plugins, "run_plugin", broken)
    assert agent.run("/teach again") == "/teach failed: boom"
    assert len(seen) == 1


@pytest.mark.skipif(bool(runner_unavailable_reason()), reason="needs the Linux plugin sandbox")
def test_run_executes_in_a_sandbox_that_sees_only_its_own_folder(tmp_path):
    root = tmp_path / "plugins"
    path = _plugin(root, "teach", NOVIBECODE)
    (path.parent / "novibecode.md").write_text("Be a teacher.", encoding="utf-8")
    assert _run(path, tmp_path / "a", "loops") == "Be a teacher.\nThe person asks: loops"
    probe = _plugin(root, "probe", (
        "import os\nimport helpers\n\ndef command():\n    return 'probe'\n\ndef run(request):\n"
        "    return repr((sorted(os.listdir('/kvist/plugin')), helpers.X, os.path.exists(" + repr(str(root)) + ")))\n"
    ))
    (probe.parent / "helpers.py").write_text("X = 7\n", encoding="utf-8")
    assert _run(probe, tmp_path / "b") == "(['helpers.py', 'plugin.py'], 7, False)"


@pytest.mark.skipif(bool(runner_unavailable_reason()), reason="needs the Linux plugin sandbox")
def test_a_failing_plugin_reports_its_error_from_the_sandbox(tmp_path):
    path = _plugin(tmp_path / "plugins", "oops", "def command():\n    return 'oops'\n\ndef run(request):\n    return 1 / 0\n")
    with pytest.raises(PluginError, match="ZeroDivisionError"):
        _run(path, tmp_path / "a")
    path.write_text("def command():\n    return 'oops'\n\ndef run(request):\n    return 42\n", encoding="utf-8")
    with pytest.raises(PluginError, match="must return text"):
        _run(path, tmp_path / "b")


def test_the_manifest_lists_plugins_and_problems(tmp_path):
    _plugin(tmp_path / "plugins", "teach", NOVIBECODE)
    _plugin(tmp_path / "plugins", "bad", "def run(request):\n    return ''\n")
    manifest = build_manifest(workspace=str(tmp_path))
    assert [(c["name"], c["description"]) for c in manifest["plugin_commands"]] == \
        [("novibecode", "Teach instead of writing the code.")]
    assert len(manifest["plugin_problems"]) == 1
    json.dumps(manifest)


@pytest.mark.parametrize(("file_path", "prefix", "expected"), [
    ("plugins/teach/plugin.py", "def command():\n    ", True),
    ("plugins/teach/helpers.py", "def ", True),
    ("plugins/loose.py", "def ", False),
    ("app/plugins/auth/views.py", "def ", False),
    ("tools/x.py", "import kvist\n", True),
    ("tools/x.py", "from kvist import read\n", True),
    ("tools/x.py", "import kvistlike\n", False),
    ("src/app.py", "import os\n", False),
])
def test_completion_gets_the_plugin_api_for_a_plugin_file(tmp_path, file_path, prefix, expected):
    request = CompletionRequest(file_path=file_path, language="python", prefix=prefix, suffix="")
    prompt = prepare_completion(request, tmp_path)[1]["content"]
    assert ("Kvist plugin API" in prompt) is expected
    if expected:
        assert "kvist.read(name)" in prompt


def test_a_made_up_cursor_tag_is_not_inserted():
    assert clean_model_output('def run(request):\n    return ""\n</cursor>', "x = 1\n\n") == 'def run(request):\n    return ""\n'
