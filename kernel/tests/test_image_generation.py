# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import threading
from types import SimpleNamespace

import pytest

from kernel.agent import image_generation
from kernel.agent.agent_tools import run_agent_tool
from kernel.agent.agent_model import _load_role_prompt
from kernel.agent.native_tools import tool_definition
from kernel.protocol.parser import ALLOWED_ACTIONS
from kernel.sandbox.executor import SafeExecutor
from kernel.tests.conftest import NullUI, commands_from_response, make_command

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


class FakeImages:
    def __init__(self, data: bytes | None) -> None:
        self.data = data
        self.requests: list[dict] = []

    def generate(self, **request):
        self.requests.append(request)
        encoded = base64.b64encode(self.data).decode() if self.data is not None else None
        return SimpleNamespace(data=[SimpleNamespace(b64_json=encoded)])


class RecordingUI(NullUI):
    def __init__(self) -> None:
        self.artifacts: list[dict] = []

    def print_artifact(self, payload: dict) -> None:
        self.artifacts.append(payload)


def _agent(agent_factory, data: bytes | None = PNG):
    agent = agent_factory(provider="anymodel")
    agent.client = SimpleNamespace(images=FakeImages(data))
    agent._ui = RecordingUI()
    return agent


def _call(agent, **args):
    return run_agent_tool(agent, make_command("generate_image", args))


def test_the_extension_comes_from_the_picture_not_from_the_model(agent_factory, tmp_path):
    agent = _agent(agent_factory, JPEG)

    result = _call(agent, prompt="a red fox", path="art/fox.png")

    assert result["ok"] is True
    assert result["data"]["path"] == "art/fox.jpg"
    assert (tmp_path / "art" / "fox.jpg").read_bytes() == JPEG
    assert not (tmp_path / "art" / "fox.png").exists()


def test_a_name_without_extension_gets_one(agent_factory, tmp_path):
    result = _call(_agent(agent_factory), prompt="a red fox", path="fox")

    assert result["data"]["path"] == "fox.png"
    assert (tmp_path / "fox.png").read_bytes() == PNG


def test_the_frontend_is_handed_the_saved_file(agent_factory, tmp_path):
    agent = _agent(agent_factory)

    _call(agent, prompt="a red fox", path="fox")

    assert agent._ui.artifacts == [{
        "skill": "image", "name": "fox.png", "mime": "image/png",
        "path": str(tmp_path / "fox.png"), "caption": "a red fox",
    }]


def test_an_existing_file_is_never_overwritten(agent_factory, tmp_path):
    (tmp_path / "fox.png").write_bytes(b"mine")

    result = _call(_agent(agent_factory), prompt="a red fox", path="fox")

    assert result["ok"] is False
    assert "already exists" in result["error"]
    assert (tmp_path / "fox.png").read_bytes() == b"mine"


def test_a_path_outside_the_workspace_is_refused(agent_factory, tmp_path):
    result = _call(_agent(agent_factory), prompt="a red fox", path="../fox")

    assert result["ok"] is False
    assert not (tmp_path.parent / "fox.png").exists()


@pytest.fixture(params=["directory descriptors", "resolved paths"])
def file_writer(request, monkeypatch):
    if request.param == "resolved paths":
        # Windows has no directory descriptors; its writer runs here, where links are easy to make.
        monkeypatch.setattr(SafeExecutor, "_create_new_file", SafeExecutor._create_new_file_by_path)


def _link(link, target, *, target_is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as error:
        pytest.skip(f"this account cannot create symbolic links: {error}")


def _checked_before_the_link_appeared(agent, monkeypatch, tmp_path):
    """The path check passed while the link was not there yet; the writer must hold on its own."""
    monkeypatch.setattr(agent.executor, "_guard_env_file_access", lambda path: tmp_path / path)


def test_a_link_at_the_name_is_not_followed(agent_factory, tmp_path, tmp_path_factory, monkeypatch, file_writer):
    outside = tmp_path_factory.mktemp("outside") / "theirs.png"
    outside.write_bytes(b"theirs")
    _link(tmp_path / "fox.png", outside)
    agent = _agent(agent_factory)
    _checked_before_the_link_appeared(agent, monkeypatch, tmp_path)

    result = _call(agent, prompt="a red fox", path="fox")

    assert result["ok"] is False
    assert "already exists" in result["error"]
    assert outside.read_bytes() == b"theirs"


def test_a_linked_folder_leading_out_of_the_workspace_is_refused(
    agent_factory, tmp_path, tmp_path_factory, monkeypatch, file_writer
):
    outside = tmp_path_factory.mktemp("outside")
    _link(tmp_path / "art", outside, target_is_directory=True)
    agent = _agent(agent_factory)
    _checked_before_the_link_appeared(agent, monkeypatch, tmp_path)

    result = _call(agent, prompt="a red fox", path="art/fox")

    assert result["ok"] is False
    assert "source directory safely" in result["error"]
    assert list(outside.iterdir()) == []


def test_a_reply_that_is_not_a_picture_is_an_error(agent_factory, tmp_path):
    result = _call(_agent(agent_factory, b"<html>nope</html>"), prompt="a red fox", path="fox")

    assert result["ok"] is False
    assert not list(tmp_path.glob("fox*"))


def test_an_empty_reply_is_an_error(agent_factory):
    result = _call(_agent(agent_factory, None), prompt="a red fox", path="fox")

    assert result["ok"] is False
    assert "without a picture" in result["error"]


def test_the_standard_model_is_gpt_image_2_and_the_flag_asks_for_2_5(agent_factory):
    agent = _agent(agent_factory)

    _call(agent, prompt="a red fox", path="plain")
    _call(agent, prompt="a red fox", path="fine", high_quality=True)

    assert [request["model"] for request in agent.client.images.requests] == ["cx/gpt-image-2", "cx/gpt-image-2.5"]


def test_the_quality_flag_must_be_a_boolean():
    from kernel.protocol.parser import CommandValidationError
    with pytest.raises(CommandValidationError):
        commands_from_response(
            '<tool_call>{"name": "generate_image", "arguments": {"prompt": "a", "path": "b", "high_quality": "yes"}}</tool_call>'
        )


@pytest.mark.parametrize("role,provider,expected", [
    ("coder", "anymodel", True),
    ("devops", "anymodel", True),
    ("reader", "anymodel", False),
    ("chat", "anymodel", False),
    ("coder", "openai", False),
])
def test_the_tool_is_offered_only_to_a_writing_role_on_anymodel(agent_factory, role, provider, expected):
    assert image_generation.available(agent_factory(provider=provider), role) is expected


def test_a_helper_is_not_offered_the_tool(agent_factory):
    agent = agent_factory(provider="anymodel")
    agent.is_helper = True

    assert image_generation.available(agent, "coder") is False


def test_the_prompt_lists_the_tool_with_its_block_and_schema():
    prompt = image_generation.with_tool_in_prompt(_load_role_prompt("coder"))

    assert "generate_image: draws a picture" in prompt
    assert 'generate_image: {"prompt": "<str>", "path": "<str, no extension>", "high_quality": false}' in prompt
    assert ", generate_image." in prompt


def test_the_call_parses_and_the_native_tool_matches():
    [command] = commands_from_response(
        '<tool_call>{"name": "generate_image", "arguments": {"prompt": "a fox", "path": "fox"}}</tool_call>'
    )
    assert command.action.args == {"prompt": "a fox", "path": "fox"}
    assert "generate_image" in ALLOWED_ACTIONS
    assert tool_definition("generate_image")["function"]["parameters"]["required"] == ["prompt", "path"]


def _waking_agent(agent_factory, data: bytes | None = PNG):
    agent = _agent(agent_factory, data)
    agent.woken = threading.Event()
    agent.wake_listener = agent.woken.set
    return agent


def test_with_a_way_to_wake_the_agent_the_call_returns_at_once(agent_factory, tmp_path):
    agent = _waking_agent(agent_factory)
    gate = threading.Event()
    real = agent.client.images.generate
    agent.client.images.generate = lambda **request: (gate.wait(5), real(**request))[1]

    result = _call(agent, prompt="a red fox", path="fox")

    assert result["data"]["status"] == "drawing"
    assert not (tmp_path / "fox.png").exists()
    assert agent._ui.artifacts == []
    gate.set()
    assert agent.woken.wait(5)
    assert (tmp_path / "fox.png").read_bytes() == PNG
    assert agent.has_background_news() is True
    assert "fox.png is ready" in agent.images.take_notices()
    assert agent.has_background_news() is False


def test_a_failed_picture_wakes_the_agent_with_the_reason(agent_factory, tmp_path):
    (tmp_path / "fox.png").write_bytes(b"mine")
    agent = _waking_agent(agent_factory)

    _call(agent, prompt="a red fox", path="fox")

    assert agent.woken.wait(5)
    assert "already exists" in agent.images.take_notices()


def test_a_stopped_picture_leaves_no_notice_and_wakes_nobody(agent_factory, tmp_path):
    agent = _waking_agent(agent_factory)
    gate = threading.Event()
    real = agent.client.images.generate
    agent.client.images.generate = lambda **request: (gate.wait(5), real(**request))[1]
    _call(agent, prompt="a red fox", path="fox")

    assert agent.images.stop_all() == ["img_1"]
    gate.set()

    assert not agent.woken.wait(0.5)
    assert agent.images.has_notice() is False


def test_no_more_than_three_pictures_at_once(agent_factory):
    agent = _waking_agent(agent_factory)
    gate = threading.Event()
    real = agent.client.images.generate
    agent.client.images.generate = lambda **request: (gate.wait(5), real(**request))[1]
    for index in range(3):
        assert _call(agent, prompt="a red fox", path=f"fox{index}")["ok"] is True

    refused = _call(agent, prompt="a red fox", path="fox3")

    assert refused["ok"] is False
    assert "already being drawn" in refused["error"]
    gate.set()


def test_the_turn_ends_while_the_picture_is_drawn_and_its_end_wakes_the_agent(agent_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_NATIVE_TOOLS", "0")
    agent = _waking_agent(agent_factory)
    agent.config.max_steps = 4
    agent.config.stream = False
    agent._state.native_tools = False
    agent._state.current_agent_role = "coder"
    agent._last_response_language = "ru"
    gate = threading.Event()
    real = agent.client.images.generate
    agent.client.images.generate = lambda **request: (gate.wait(5), real(**request))[1]
    prompts: list[str] = []
    replies = iter([
        '<tool_call>{"name": "generate_image", "arguments": {"prompt": "a red fox", "path": "fox"}}</tool_call>',
    ])
    agent._call_action_model = lambda prompt: prompts.append(prompt) or next(replies, "Готово.")

    agent.control.begin()
    agent._run_loop("нарисуй лису", start_step=1)

    assert not (tmp_path / "fox.png").exists()
    assert not agent.wake_due()
    gate.set()
    assert agent.woken.wait(5)
    assert agent.wake_due()

    agent.wake()

    assert "fox.png is ready" in prompts[-1]
    assert not agent.wake_due()


def test_a_started_picture_is_not_reported_as_saved():
    from kernel.protocol.action_result import build_action_result

    def summary(data):
        feedback = {"ok": True, "data": data}
        return build_action_result(1, "generate_image", {"prompt": "a", "path": "fox"}, feedback)["summary"]

    assert summary({"image_id": "img_1", "status": "drawing"}) == "Drawing in the background"
    assert summary({"path": "fox.png"}) == "Saved fox.png"


def test_the_start_answer_tells_the_model_not_to_poll_or_end_incomplete(agent_factory):
    note = _call(_waking_agent(agent_factory), prompt="a red fox", path="fox")["data"]["note"]

    assert "do not poll" in note
    assert "Do not mark that answer incomplete" in note
    assert "a stopped task is not woken" in note
