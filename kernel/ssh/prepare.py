# -*- coding: utf-8 -*-
"""Once a frontend is open on a server: is anything there, can it work, and is the core running for this folder?

Every question is the frontend's own dialog (``DialogIO``: ordinary input requests), so a missing Kvist or service is a
question with an answer, drawn the way the frontend draws everything, not a program that asks on the terminal behind it.
"""
from __future__ import annotations

from collections.abc import Callable

from kernel.protocol.frontend_protocol import PROTOCOL_VERSION
from kernel.ssh import credentials, deploy, messages, wire
from kernel.ssh.command import Target, call_op, classify, meaningful
from kernel.ssh.paths import RemoteError

Call = Callable[[Target, str], tuple[int, dict | None, str]]
Options = list[tuple[str, str]]  # (value, label)


class DialogIO:
    """Questions the frontend draws itself, through the core's ordinary input requests."""

    interactive = True

    def __init__(self, ui) -> None:
        self._ui = ui

    def say(self, text: str) -> None:
        self._ui.print_error(text)

    def note(self, text: str) -> None:
        self._ui.print_notice(text)

    def menu(self, title: str, options: Options) -> str | None:
        shown = [{"value": value, "label": label} for value, label in options]
        return self._ui.choose_option(title, shown, current_value=options[0][0], help_text="")


def _say(io, key: str, target: Target, language: str, **fields: object) -> None:
    io.say(messages.text(key, language, host=target.label, path=target.workspace, **fields))


def _problem(io, answer: dict, target: Target, language: str) -> bool:
    """Say what is wrong with the server's answer; True when something is."""
    if answer.get("platform") == "win32":
        _say(io, "unsupported", target, language)
    elif answer.get("protocol_version") != PROTOCOL_VERSION:
        _say(io, "protocol", target, language, theirs=answer.get("protocol_version"), ours=PROTOCOL_VERSION)
    elif answer.get("remote_revision", 1) < wire.REVISION:
        _say(io, "old_kvist", target, language)
    elif not answer.get("workspace_exists"):
        _say(io, "no_folder", target, language)
    elif not answer.get("provider_ready") and credentials.from_this_computer() is None:
        _say(io, "no_provider", target, language)
    else:
        return False
    return True


def _restart_old_daemon(io, call: Call, target: Target, language: str, daemon: dict) -> bool:
    """A daemon that an older Kvist started keeps running that code, so what was installed since does not reach it until it restarts."""
    if not io.interactive:
        _say(io, "daemon_old", target, language)
        return False
    others = int(daemon.get("attached", 0))
    title = messages.text("daemon_old_title", language, host=target.label)
    if others:
        title = messages.text("daemon_old_busy", language, count=others) + title
    if io.menu(title, [("restart", messages.text("daemon_restart", language)), ("cancel", messages.text("cancel", language))]) != "restart":
        return False
    return _run(io, call, target, "stop", language) and _run(io, call, target, "start", language)


def _run(io, call: Call, target: Target, operation: str, language: str) -> bool:
    code, answer, stderr = call(target, operation)
    if answer is not None and answer.get("ok"):
        for note in answer.get("notes", []):
            io.say(note)
        return True
    detail = (answer or {}).get("error") or (meaningful(stderr).splitlines() or [f"exit {code}"])[-1]
    _say(io, "op_failed", target, language, error=detail)
    return False


def _offer_to_run(io, call: Call, target: Target, language: str) -> bool:
    title = messages.text("service_title", language, host=target.label, path=target.workspace)
    options = [
        ("install-service", messages.text("service_install", language)),
        ("start", messages.text("service_once", language)),
        ("cancel", messages.text("cancel", language)),
    ]
    choice = io.menu(title, options)
    return choice in ("install-service", "start") and _run(io, call, target, choice, language)


def _offer_folder(io, call: Call, target: Target, language: str) -> bool:
    title = messages.text("ask_folder", language, host=target.label, path=target.workspace)
    options = [("yes", messages.text("yes", language)), ("no", messages.text("no", language))]
    return io.menu(title, options) == "yes" and _run(io, call, target, "mkdir", language)


def _offer_to_install(io, install: Callable[[Target], str], target: Target, language: str, kind: str) -> bool:
    """No Kvist on the machine, or one too old for remote work: offer to copy this computer's own. True when it is there now."""
    binary = deploy.local_binary()
    what = (
        messages.text("install_copy", language, host=target.label, mb=max(1, binary.stat().st_size // 1_000_000))
        if binary
        else messages.text("install_sources", language, host=target.label)
    )
    title = messages.text("install_none" if kind == "not_found" else "install_old", language, host=target.label)
    if io.menu(title, [("install", what), ("cancel", messages.text("cancel", language))]) != "install":
        return False
    io.note(messages.text("installing", language, host=target.label))
    try:
        version = install(target)
    except (RemoteError, OSError) as error:
        io.say(str(error))
        return False
    io.note(messages.text("installed", language, host=target.label, version=version))
    return True


def prepare(
    target: Target,
    *,
    io,
    call: Call = call_op,
    language: str | None = None,
    install: Callable[[Target], str] = deploy.install_core,
) -> bool:
    language = language or messages.ui_language()
    code, answer, stderr = call(target, "probe")
    if answer is None and io.interactive:
        kind = classify(code, stderr, shim_state=None).kind
        if kind in ("not_found", "old_kvist") and _offer_to_install(io, install, target, language, kind):
            code, answer, stderr = call(target, "probe")
    if answer is not None and answer.get("ok") and answer.get("remote_revision", 1) < wire.REVISION and io.interactive:
        if _offer_to_install(io, install, target, language, "old_kvist"):
            code, answer, stderr = call(target, "probe")
    if answer is not None and not answer.get("ok"):
        # The server answered and said what is wrong; that is better than guessing from ssh's exit code.
        _say(io, "op_failed", target, language, error=answer.get("error", "?"))
        return False
    if answer is None:
        failure = classify(code, stderr, shim_state=None)
        _say(io, failure.kind, target, language, minutes=0)
        if not failure.definitive and meaningful(stderr):
            io.say(meaningful(stderr).splitlines()[-1])
        return False
    if not answer.get("workspace_exists") and io.interactive and _offer_folder(io, call, target, language):
        answer = {**answer, "workspace_exists": True}
    if _problem(io, answer, target, language):
        return False
    if answer.get("daemon"):
        if answer["daemon"].get("revision", 1) < wire.REVISION:
            return _restart_old_daemon(io, call, target, language, answer["daemon"])
        return True
    if not io.interactive:
        _say(io, "no_service", target, language)
        return False
    return _offer_to_run(io, call, target, language)
