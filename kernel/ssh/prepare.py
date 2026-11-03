# -*- coding: utf-8 -*-
"""Before a frontend opens on a server: is anything there, can it work, and is the core running for this folder?

Run by the launcher on a terminal, so a missing service is a question with an answer, not a frontend
that opens empty and fails.
"""
from __future__ import annotations

import sys
from collections.abc import Callable

from kernel.protocol.frontend_protocol import PROTOCOL_VERSION
from kernel.ssh import messages
from kernel.ssh.command import Target, classify
from kernel.ssh.proxy import call_op

Call = Callable[[Target, str], tuple[int, dict | None, str]]


def _say(key: str, target: Target, language: str, **fields: object) -> None:
    print(messages.text(key, language, host=target.label, path=target.workspace, **fields), file=sys.stderr)


def _problem(answer: dict, target: Target, language: str) -> bool:
    """Say what is wrong with the server's answer; True when something is."""
    if answer.get("platform") == "win32":
        _say("unsupported", target, language)
    elif answer.get("protocol_version") != PROTOCOL_VERSION:
        _say("protocol", target, language, theirs=answer.get("protocol_version"), ours=PROTOCOL_VERSION)
    elif not answer.get("workspace_exists"):
        _say("no_folder", target, language)
    elif not answer.get("provider_ready"):
        _say("no_provider", target, language)
    else:
        return False
    return True


def _run(call: Call, target: Target, operation: str, language: str) -> bool:
    code, answer, stderr = call(target, operation)
    if answer is not None and answer.get("ok"):
        for note in answer.get("notes", []):
            print(note, file=sys.stderr)
        return True
    detail = (answer or {}).get("error") or (stderr.strip().splitlines() or [f"exit {code}"])[-1]
    _say("op_failed", target, language, error=detail)
    return False


def _offer_to_run(call: Call, target: Target, language: str, ask: Callable[[str], str]) -> bool:
    prompt = messages.text("ask_service", language, host=target.label, path=target.workspace)
    choice = (ask(prompt).strip() or "1")
    if choice == "1":
        return _run(call, target, "install-service", language)
    if choice == "2":
        return _run(call, target, "start", language)
    return False


def prepare(
    target: Target,
    *,
    call: Call = call_op,
    ask: Callable[[str], str] = input,
    interactive: bool | None = None,
    language: str | None = None,
) -> bool:
    language = language or messages.ui_language()
    interactive = sys.stdin.isatty() if interactive is None else interactive
    code, answer, stderr = call(target, "probe")
    if answer is None or not answer.get("ok"):
        failure = classify(code, stderr, shim_state=None)
        _say(failure.kind, target, language, minutes=0)
        if not failure.definitive and stderr.strip():
            print(stderr.strip().splitlines()[-1], file=sys.stderr)
        return False
    if _problem(answer, target, language):
        return False
    if answer.get("daemon"):
        return True
    if not interactive:
        _say("no_service", target, language)
        return False
    return _offer_to_run(call, target, language, ask)
