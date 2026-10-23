# -*- coding: utf-8 -*-
"""``generate_image``: a picture from AnyModel's image models, saved into the workspace.

Offered only where the session talks to AnyModel (its gateway serves the
image models through the OpenAI images endpoint). The model gives a
description and a name without extension; the file type is whatever the
provider returned, read from the bytes.
"""
from __future__ import annotations

import base64
import binascii
import itertools
import mimetypes
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

import openai

from kernel.agent.agent_state import RunCancelled
from kernel.agent.cancellable_call import call_cancellable
from kernel.llm.llm_clients import ANYMODEL_PROVIDER
from kernel.plan.end_session import _ALLOWED_ACTIONS_LINE_RE, _append_to_section
from kernel.sandbox.executor import ExecutorError
from kernel.sandbox.redaction import redact_text

ACTION = "generate_image"
STANDARD_MODEL = "cx/gpt-image-2"
HIGH_QUALITY_MODEL = "cx/gpt-image-2.5"
# Image models run for tens of seconds; the chat timeout would cut them off.
MIN_TIMEOUT_SECONDS = 300.0
_ROLES = frozenset({"coder", "devops", "universal"})
_PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "system" / "generate_image.txt"
_SCHEMA_LINE = 'generate_image: {"prompt": "<str>", "path": "<str, no extension>", "high_quality": false}'
_KNOWN_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
# Each picture is paid for; a model that fires off many at once spends the person's money in one breath.
MAX_RUNNING = 3


class ImageError(Exception):
    """A failure the model can read and act on (another name, a clearer prompt)."""


def available(agent: Any, role: str) -> bool:
    """The tool exists for the main agent of an AnyModel session, in a role that may write files."""
    return agent.config.provider == ANYMODEL_PROVIDER and role in _ROLES and not agent.is_helper


def with_tool_in_prompt(role_prompt: str) -> str:
    """The role prompt with generate_image in ALLOWED ACTIONS, its block, and its schema line."""
    listed = _ALLOWED_ACTIONS_LINE_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2).rstrip().rstrip('.')}, {ACTION}.",
        role_prompt,
        count=1,
    )
    block = _PROMPT_FILE.read_text(encoding="utf-8").strip()
    with_block = _append_to_section(listed, "ALLOWED ACTIONS", block)
    return with_block.replace("\n\nNo extra keys", f"\n{_SCHEMA_LINE}\n\nNo extra keys", 1)


class ImageJobs:
    """Pictures being drawn while the agent works on: the monitor of this tool.

    ``start`` returns at once; a thread draws and saves the picture, tells the
    frontend, and leaves a notice the model reads at its next step. Like a
    background command it can end on its own, which wakes an idle agent.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._ids = itertools.count(1)
        self._running: set[str] = set()
        self._notices: list[str] = []

    def start(self, agent: Any, prompt: str, name: str, model: str) -> dict[str, Any]:
        with self._lock:
            if len(self._running) >= MAX_RUNNING:
                raise ImageError(f"{MAX_RUNNING} pictures are already being drawn; wait for one to finish.")
            image_id = f"img_{next(self._ids)}"
            self._running.add(image_id)
            cancel = self._cancel
        _announce(agent, {"image_id": image_id, "state": "generating", "name": Path(name).name, "prompt": prompt})
        threading.Thread(
            target=self._draw, args=(agent, image_id, prompt, name, model, cancel), daemon=True, name="kvist-image"
        ).start()
        return {
            "image_id": image_id,
            "status": "drawing",
            "note": "The picture is being drawn in the background, usually 10 to 120 seconds, and you are woken with a note "
                    "when it is ready or has failed. So do not poll for it (no wait, no listing of folders). Either carry on "
                    "with other work, or end the turn with an ordinary final answer saying you will report when it is ready. "
                    "Do not mark that answer incomplete: an incomplete answer stops the task, and a stopped task is not woken, "
                    "so the picture would arrive to nobody until the person writes again.",
        }

    def _draw(self, agent: Any, image_id: str, prompt: str, name: str, model: str, cancel: threading.Event) -> None:
        began = time.monotonic()
        try:
            saved = draw_now(agent, prompt, name, model, cancel)["path"]
        except RunCancelled:
            notice = None
        except ImageError as error:
            _announce(agent, {"image_id": image_id, "state": "failed", "name": Path(name).name, "error": str(error)})
            notice = f"- the picture {name!r} was not made: {error}"
        else:
            notice = f"- {saved} is ready ({model}, {time.monotonic() - began:.0f} s). Read it with read_file to see it."
        with self._lock:
            self._running.discard(image_id)
            if notice is not None:
                self._notices.append(notice)
        if notice is not None:
            agent._background_finished()

    def has_notice(self) -> bool:
        with self._lock:
            return bool(self._notices)

    def take_notices(self) -> str:
        with self._lock:
            notices, self._notices = self._notices, []
        return "Images:\n" + "\n".join(notices) if notices else ""

    def stop_all(self) -> list[str]:
        """Give up the pictures still being drawn (a stop, a new session); their results are dropped."""
        with self._lock:
            stopped = sorted(self._running)
            self._running.clear()
            self._notices.clear()
        self._cancel.set()
        self._cancel = threading.Event()
        return stopped


def generate(agent: Any, args: dict[str, Any]) -> dict[str, Any]:
    prompt = str(args["prompt"])
    model = HIGH_QUALITY_MODEL if args.get("high_quality") else STANDARD_MODEL
    name = str(args["path"])
    if agent.wake_listener is None:
        # Nothing can wake the agent in this interface, so it waits for the picture.
        return draw_now(agent, prompt, name, model, agent._cancel_event)
    return agent.images.start(agent, prompt, name, model)


def draw_now(agent: Any, prompt: str, name: str, model: str, cancel: threading.Event) -> dict[str, Any]:
    data = _request_image(agent, model, prompt, cancel)
    suffix = _suffix(data)
    path = _with_suffix(name, suffix)
    try:
        saved = agent.executor.create_binary_file(path, data)
    except ExecutorError as error:
        raise ImageError(str(error)) from error
    _show(agent, saved, suffix, prompt)
    return {"path": saved, "bytes_written": len(data), "model": model}


def _request_image(agent: Any, model: str, prompt: str, cancel: threading.Event) -> bytes:
    timeout = max(float(agent.config.api_timeout), MIN_TIMEOUT_SECONDS)
    try:
        response = call_cancellable(
            cancel,
            lambda: agent.client.images.generate(model=model, prompt=prompt, n=1, timeout=timeout),
        )
    except openai.OpenAIError as error:
        raise ImageError(f"{model} did not produce a picture: {redact_text(str(error))}") from error
    # A reply that lands in the same instant as a stop is still a stopped picture.
    if cancel.is_set():
        raise RunCancelled
    encoded = response.data[0].b64_json if response.data else None
    if not encoded:
        raise ImageError(f"{model} answered without a picture; try another description.")
    try:
        return base64.b64decode(encoded, validate=True)
    except binascii.Error as error:
        raise ImageError(f"{model} returned a damaged picture.") from error


def _suffix(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise ImageError("The provider returned data that is not a PNG, JPEG or WebP picture.")


def _with_suffix(path: str, suffix: str) -> str:
    """The name the model gave, with the real extension in place of any it guessed."""
    given = PurePosixPath(path)
    if given.suffix.lower() in _KNOWN_SUFFIXES:
        given = given.with_suffix("")
    return f"{given}{suffix}"


def _announce(agent: Any, payload: dict[str, Any]) -> None:
    """Tell a frontend a picture is being drawn or failed (live only; the finished one is an artifact event)."""
    printer = getattr(agent._ui, "print_image_job", None)
    if printer is not None:
        printer(payload)


def _show(agent: Any, relative_path: str, suffix: str, prompt: str) -> None:
    """Hand the picture to a frontend that can display it (Glass puts it beside the chat)."""
    printer = getattr(agent._ui, "print_artifact", None)
    if printer is None:
        return
    printer({
        "skill": "image",
        "name": Path(relative_path).name,
        "mime": mimetypes.guess_type(f"x{suffix}")[0] or "image/png",
        "path": str(Path(agent.executor.workspace) / relative_path),
        "caption": prompt,
    })
