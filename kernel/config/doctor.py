# -*- coding: utf-8 -*-
"""``/doctor`` and ``--doctor``: one report on whether this Kvist install can work.

The checks read; nothing here writes, builds or starts a frontend. Only
:func:`probe_model` talks to the network, and only after the person said yes.
Secrets are reported by name and presence, never by value.
"""
from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from kernel.config.version import kvist_version
from kernel.sandbox.redaction import redact_text

OK = "ok"
WARN = "warn"
FAIL = "fail"
INFO = "info"
_MARKS = {OK: "✓", WARN: "!", FAIL: "✗", INFO: "·"}

# What the core cannot start without, and what each frontend launcher imports.
_CORE_MODULES = (
    "kernel.agent.agent",
    "kernel.agent.agent_runtime",
    "kernel.agent.agent_model",
    "kernel.agent.native_tools",
    "kernel.agent.role_router",
    "kernel.protocol.parser",
    "kernel.protocol.event_stdio",
    "kernel.sandbox.executor",
    "kernel.sandbox.terminal",
    "kernel.session.session_store",
    "kernel.session.change_journal",
    "kernel.context.context_budget",
    "kernel.llm.llm_clients",
    "kernel.plan.plan_mode",
    "kernel.memory.service",
    "kernel.plugins.registry",
)
_THIRD_PARTY = ("openai", "anthropic", "tiktoken", "rich", "prompt_toolkit")
_PROMPT_FILES = (
    "constitution.txt",
    "chat_prompt.txt",
    "coder_prompt.txt",
    "reader_prompt.txt",
    "devops_prompt.txt",
    "universal_prompt.txt",
    "report_prompt.txt",
    "router_prompt.txt",
    "native_tools.txt",
    "plan_mode.txt",
)
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "system"
# The Ink app is written for Node 22 (CLAUDE.md, "Ink terminal frontend").
_NODE_MIN_MAJOR = 22
_PING_PROMPT = "Reply with the single word: pong"
_SUBPROCESS_TIMEOUT = 5


@dataclasses.dataclass(slots=True)
class Check:
    group: str
    name: str
    status: str
    detail: str = ""


def diagnose(workspace: Path, ru: bool, provider: str | None = None) -> list[Check]:
    """Every offline check, in the order the report shows them."""
    say = _Say(ru)
    checks: list[Check] = []
    checks += _check_environment(say, workspace)
    checks += _check_modules(say)
    checks += _check_settings(say, workspace)
    checks += _check_provider(say, provider)
    checks += _check_frontends(say)
    return checks


def provider_ready(checks: list[Check], ru: bool) -> bool:
    """False when the provider, its models or its key are missing: a ping cannot work then."""
    group = _Say(ru)("Provider", "Провайдер")
    return not any(check.group == group and check.status == FAIL for check in checks)


def probe_model(make_caller: Callable[[], tuple[Any, str]], ask: Callable[[str, str], bool], ru: bool) -> Check:
    """Ask the person, then have the model answer "pong" through the request path a run uses.

    ``make_caller`` returns ``(caller, model)``; ``caller`` needs the three attributes
    ``_create_json_completion`` reads from an agent: ``client``, ``config`` and ``_ui``.
    ``ask(title, body)`` is the frontend's yes/no question; nothing is sent on a no.
    """
    from kernel.agent.agent_model import (
        _create_json_completion,
        _format_llm_error,
        safe_extract_completion_content,
    )

    say = _Say(ru)
    group = say("Model", "Модель")
    try:
        caller, model = make_caller()
    except (RuntimeError, ValueError, OSError) as error:
        return Check(group, say("client", "клиент"), FAIL, redact_text(str(error)))
    provider = caller.config.provider
    title = say("Test the model connection?", "Проверить связь с моделью?")
    body = say(
        f"Doctor will send one tiny request to {provider} / {model} asking it to reply "
        "«pong». It uses a few tokens of your account.",
        f"Doctor отправит один короткий запрос в {provider} / {model} с просьбой ответить "
        "«pong». Это несколько токенов вашего аккаунта.",
    )
    if not ask(title, body):
        return Check(group, model, INFO, say("not tested: no permission was given", "не проверялась: разрешения не было"))

    messages = [{"role": "user", "content": _PING_PROMPT}]
    started = time.monotonic()
    try:
        response = _create_json_completion(caller, model, messages, stream=False, json_mode=False)
        answer = safe_extract_completion_content(response, provider, model)
    # Provider SDKs share no exception base class; this is the boundary
    # call_model draws, and the error is reported, not swallowed.
    except Exception as error:
        return Check(group, model, FAIL, _format_llm_error(error, provider))
    seconds = time.monotonic() - started
    if "pong" in answer.casefold():
        return Check(group, model, OK, say(f"answered «pong» in {seconds:.1f} s", f"ответила «pong» за {seconds:.1f} с"))
    shown = answer.strip().replace("\n", " ")[:60]
    return Check(group, model, WARN, say(f"reachable, but answered «{shown}»", f"доступна, но ответила «{shown}»"))


def caller_for_agent(agent: Any) -> tuple[Any, str]:
    """The running agent's own client and fast model, with a light reasoning effort: the answer is one word."""
    config = dataclasses.replace(agent.config, reasoning_effort="low")
    return SimpleNamespace(client=agent.client, config=config, _ui=agent._ui), agent.config.fast_model


def render(checks: list[Check], ru: bool) -> str:
    lines: list[str] = []
    group = ""
    for check in checks:
        if check.group != group:
            group = check.group
            lines += ["", group]
        detail = f" — {check.detail}" if check.detail else ""
        lines.append(f"  {_MARKS[check.status]} {check.name}{detail}")
    failed = sum(check.status == FAIL for check in checks)
    warned = sum(check.status == WARN for check in checks)
    say = _Say(ru)
    summary = say(
        f"{failed} failed, {warned} warnings, {len(checks)} checks",
        f"Ошибок: {failed}, предупреждений: {warned}, проверок: {len(checks)}",
    )
    return "\n".join([*lines[1:], "", summary])


def has_failures(checks: list[Check]) -> bool:
    return any(check.status == FAIL for check in checks)


class _Say:
    def __init__(self, ru: bool) -> None:
        self._ru = ru

    def __call__(self, en: str, ru: str) -> str:
        return ru if self._ru else en


def _check_environment(say: _Say, workspace: Path) -> list[Check]:
    from kernel.sandbox.process_tree import cgroup_supervision_available
    from kernel.sandbox.sandbox import network_isolation_available, resolve_bubblewrap_path

    group = say("Environment", "Окружение")
    checks = [
        Check(group, "Kvist", OK, f"{kvist_version()}, Python {sys.version.split()[0]}, {sys.platform}"),
        _tool_check(group, "git", say("needed for git_status and git_diff", "нужен для git_status и git_diff")),
        _tool_check(group, "rg", say("search_code falls back to a slow Python walk", "search_code уйдёт на медленный обход на Python")),
    ]
    if sys.platform.startswith("linux"):
        bwrap = resolve_bubblewrap_path()
        checks.append(Check(
            group, "bubblewrap", OK if bwrap else WARN,
            bwrap or say("not found: commands need per-execution approval", "не найден: каждая команда потребует подтверждения"),
        ))
        checks.append(Check(
            group, "cgroup v2", OK if cgroup_supervision_available() else WARN,
            say("process trees are torn down by the cgroup", "деревья процессов закрываются через cgroup")
            if cgroup_supervision_available()
            else say("unavailable: a timeout falls back to killpg", "недоступен: по таймауту процессы убиваются через killpg"),
        ))
    if not network_isolation_available():
        checks.append(Check(
            group, say("network isolation", "изоляция сети"), WARN,
            say("this system cannot cut a command's network", "эта система не может отключить сеть команде"),
        ))
    writable = workspace.is_dir() and os.access(workspace, os.W_OK)
    checks.append(Check(
        group, say("workspace", "рабочая папка"), OK if writable else FAIL,
        str(workspace) if writable else say(f"{workspace} is not a writable folder", f"{workspace} не папка или недоступна для записи"),
    ))
    return checks


def _tool_check(group: str, tool: str, why_needed: str) -> Check:
    found = shutil.which(tool)
    return Check(group, tool, OK if found else WARN, found or why_needed)


def _check_modules(say: _Say) -> list[Check]:
    group = say("Modules", "Модули")
    failures: list[Check] = []
    for name in _CORE_MODULES:
        try:
            importlib.import_module(name)
        # A broken module can fail at import in any of these ways; each is the finding.
        except (ImportError, SyntaxError, OSError, RuntimeError, AttributeError) as error:
            failures.append(Check(group, name, FAIL, f"{type(error).__name__}: {error}"))
    checks = failures or [Check(group, say("core", "ядро"), OK, say(f"{len(_CORE_MODULES)} modules import", f"импортируются {len(_CORE_MODULES)} модулей"))]
    missing = [name for name in _THIRD_PARTY if importlib.util.find_spec(name) is None]
    checks.append(Check(
        group, say("packages", "пакеты"), FAIL if missing else OK,
        ", ".join(missing) if missing else ", ".join(_THIRD_PARTY),
    ))
    absent = [name for name in _PROMPT_FILES if not _non_empty(_PROMPTS_DIR / name)]
    checks.append(Check(
        group, say("prompts", "промпты"), FAIL if absent else OK,
        ", ".join(absent) if absent else say(f"{len(_PROMPT_FILES)} files", f"файлов: {len(_PROMPT_FILES)}"),
    ))
    return checks


def _non_empty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _check_settings(say: _Say, workspace: Path) -> list[Check]:
    from kernel.config.user_config import get_saved_api_settings, get_user_config_path, load_user_config
    from kernel.llm.api_settings import API_SETTINGS, coerce_value
    from kernel.sandbox.permission_policy import load_policy

    group = say("Settings", "Настройки")
    path = get_user_config_path()
    if not path.exists():
        checks = [Check(group, "config.json", WARN, say(f"{path} does not exist yet: first run", f"{path} ещё нет: первый запуск"))]
    else:
        try:
            load_user_config()
        except RuntimeError as error:
            return [Check(group, "config.json", FAIL, f"{path}: {error}")]
        checks = [Check(group, "config.json", OK, str(path))]

    saved = get_saved_api_settings()
    invalid = []
    for setting in API_SETTINGS:
        if setting.key not in saved:
            continue
        try:
            coerce_value(setting, saved[setting.key])
        except ValueError:
            invalid.append(setting.key)
    checks.append(Check(
        group, "/settings-api", WARN if invalid else OK,
        say(f"invalid saved values are ignored: {', '.join(invalid)}", f"неверные сохранённые значения игнорируются: {', '.join(invalid)}")
        if invalid
        else say(f"{len(saved)} changed from the defaults", f"изменено относительно умолчаний: {len(saved)}"),
    ))
    _policy, note = load_policy(workspace)
    checks.append(Check(group, "permissions.json", WARN if note else OK, note or say("rules readable", "правила читаются")))
    return checks


def _check_provider(say: _Say, provider_override: str | None) -> list[Check]:
    from kernel.config.user_config import (
        get_custom_provider_by_name,
        get_saved_models,
        get_saved_provider_or_none,
        load_available_credentials,
        secret_label_for_key,
    )
    from kernel.llm.llm_clients import (
        MODEL_ROLES,
        extract_custom_provider_name,
        is_custom_provider,
        normalize_provider,
        provider_label,
        required_secret_keys_for_provider,
        resolve_models_for_provider,
    )

    group = say("Provider", "Провайдер")
    chosen = provider_override or os.getenv("AGENT_LLM_PROVIDER") or get_saved_provider_or_none()
    if not chosen:
        return [Check(group, say("provider", "провайдер"), FAIL, say("none chosen: run /provider", "не выбран: выполните /provider"))]
    provider = normalize_provider(chosen)
    checks = [Check(group, say("provider", "провайдер"), OK, f"{provider_label(provider)} ({provider})")]

    custom = get_custom_provider_by_name(extract_custom_provider_name(provider)) if is_custom_provider(provider) else None
    saved = (custom or {}).get("models", {}) if is_custom_provider(provider) else get_saved_models(provider)
    models, _warnings = resolve_models_for_provider(provider, saved if isinstance(saved, dict) else {})
    unset = [role for role in MODEL_ROLES if not models.get(role)]
    checks.append(Check(
        group, say("models", "модели"), FAIL if unset else OK,
        say(f"not set: {', '.join(unset)}; use /models", f"не заданы: {', '.join(unset)}; выполните /models")
        if unset
        else ", ".join(f"{role}: {models[role]}" for role in MODEL_ROLES),
    ))

    if provider == "chatgpt":
        from kernel.providers.chatgpt_auth import has_credentials

        signed_in = has_credentials()
        checks.append(Check(group, say("sign-in", "вход"), OK if signed_in else FAIL, "ChatGPT" if signed_in else say("not signed in: run /provider", "вход не выполнен: выполните /provider")))
        return checks
    if custom is not None:
        has_key = bool(str(custom.get("api_key", "")).strip())
        checks.append(Check(group, say("API key", "API-ключ"), OK if has_key else FAIL, say("saved", "сохранён") if has_key else say("missing: run /change-api", "нет: выполните /change-api")))
        return checks
    required = required_secret_keys_for_provider(provider, tuple(models.values()))
    present = load_available_credentials(required)
    for key in required:
        label = secret_label_for_key(key)
        found = key in present
        checks.append(Check(group, say(f"API key: {label}", f"API-ключ: {label}"), OK if found else FAIL, say("saved", "сохранён") if found else say("missing: run /change-api", "нет: выполните /change-api")))
    return checks


def _check_frontends(say: _Say) -> list[Check]:
    from kernel.config.user_config import get_saved_frontend

    group = say("Frontends", "Фронтенды")
    saved = get_saved_frontend()
    # A problem with the interface that opens at launch matters (the launcher falls
    # back to Ink); the others are only facts.
    checks = [Check(group, say("Classic Legacy (terminal)", "Classic Legacy (terminal)"), OK, "prompt_toolkit")]
    checks += [
        _frontend_check(group, label, saved in values, problem)
        for label, values, problem in (
            ("Ink / Claude-style", ("tui", "claude"), _ink_problem(say)),
            ("OpenCode", ("opencode",), _opencode_problem(say)),
            ("Classic (glass)", ("glass",), _kitty_binary_problem(say, "glass")),
            ("Voice", ("voice",), _kitty_binary_problem(say, "voice")),
        )
    ]
    return checks


def _frontend_check(group: str, label: str, is_saved: bool, problem: str) -> Check:
    if not problem:
        return Check(group, label, OK)
    return Check(group, label, WARN if is_saved else INFO, problem)


def _ink_problem(say: _Say) -> str:
    from kernel import main as launcher

    node = launcher.find_node()
    if node is None:
        return say("Node.js not found", "Node.js не найден")
    major = _node_major(node)
    if major is not None and major < _NODE_MIN_MAJOR:
        return say(f"Node.js {major} is too old, {_NODE_MIN_MAJOR}+ needed", f"Node.js {major} слишком старый, нужен {_NODE_MIN_MAJOR}+")
    if not launcher._TUI_ENTRY.is_file():
        return say("not built: run `npm run build` in frontends/tui/ink", "не собран: выполните `npm run build` в frontends/tui/ink")
    return ""


def _node_major(node: str) -> int | None:
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = re.match(r"v(\d+)", out.strip())
    return int(found.group(1)) if found else None


def _opencode_problem(say: _Say) -> str:
    from kernel import main as launcher

    if launcher.find_opencode_binary():
        return ""
    if launcher.find_bun() is None:
        return say("Bun not found", "Bun не найден")
    if not launcher._OPENCODE_ENTRY.is_file():
        return say("not part of this build", "нет в этой сборке")
    if not (launcher._OPENCODE_DIR / "node_modules" / "@opentui").is_dir():
        return say("no packages: run `bun install` in frontends/tui/opencode", "нет пакетов: выполните `bun install` в frontends/tui/opencode")
    return ""


def _kitty_binary_problem(say: _Say, which: str) -> str:
    from kernel import main as launcher

    if os.name == "nt":
        return say("needs kitty graphics, which Windows terminals lack", "нужна графика kitty, её нет в терминалах Windows")
    found = launcher.find_glass() if which == "glass" else launcher.find_voice_screen()
    if found is None:
        return say("not built: run `cargo build --release` in its folder", "не собран: выполните `cargo build --release` в его папке")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return ""
    # The program asks the terminal itself; stdin and stdout must be the real tty.
    try:
        check = subprocess.run([found, "--check"], stderr=subprocess.PIPE, text=True, timeout=_SUBPROCESS_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"{type(error).__name__}: {error}"
    return check.stderr.strip() if check.returncode != 0 else ""
