# -*- coding: utf-8 -*-
"""``/permissions``: the person's rules for commands, and auto mode.

One implementation behind the classic terminal and the stdio command, so every
frontend gets the same thing: a menu of arrows (``dialog``) when the command is
typed bare, and the same actions as arguments (``run``) for whoever prefers to
type them. It runs in the person's hands: the model cannot reach
``.kvist/permissions.json`` or ``config.json``. Texts are plain: no frontend
draws Markdown here.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kernel.config.user_config import save_auto_approval
from kernel.sandbox.permission_policy import (
    RULE_LISTS,
    append_rule,
    load_policy,
    pattern_is_specific,
    remove_rule,
)

Confirm = Callable[[str, str], bool]

RULE_COMMANDS = ("allow", "host", "ask", "deny")

AUTO_MODE_STARTUP_WARNING = (
    "Auto mode is ON and is NOT safe: a second model, not you, answers the approval questions. "
    "Turn it off with /permissions auto off. / "
    "Автоматический режим ВКЛЮЧЁН и НЕБЕЗОПАСЕН: на вопросы о подтверждении отвечает вторая модель, а не вы. "
    "Выключить: /permissions auto off."
)

_AUTO_WARNING = (
    "Auto mode is NOT safe.\n\n"
    "A second model will answer, instead of you, the questions that would otherwise wait for your approval: "
    "commands that are neither allowed nor denied by your rules, overwriting files, running outside the sandbox, "
    "network access, fetching web pages from unknown hosts, and the permissions of skills and MCP servers. "
    "It can be wrong, and text in a file, a web page or a command's output can talk it into a mistake. "
    "Whatever it allows runs with your permissions.\n\n"
    "What stays as it is: deny rules, the built-in hard blocks, and dangerous commands, which still ask you "
    "every time. When the classifier cannot decide, you are asked and have 2 minutes; no answer counts as no. "
    "Rules for later (allow, host) are never created by the classifier."
)
_AUTO_WARNING_RU = (
    "Автоматический режим НЕБЕЗОПАСЕН.\n\n"
    "Вторая модель будет отвечать вместо вас на вопросы, которые иначе ждали бы вашего подтверждения: "
    "команды, которые ваши правила не разрешают и не запрещают, перезапись файлов, запуск вне песочницы, "
    "доступ к сети, загрузка страниц с незнакомых сайтов, права скиллов и MCP-серверов. "
    "Она может ошибиться, а текст в файле, на веб-странице или в выводе команды может подтолкнуть её к ошибке. "
    "Всё, что она разрешит, выполнится с вашими правами.\n\n"
    "Что остаётся как было: правила deny, встроенные жёсткие запреты и опасные команды, которые по-прежнему "
    "спрашивают вас каждый раз. Если классификатор не может решить, спросят вас, и на ответ есть 2 минуты; "
    "нет ответа значит нет. Правила на будущее (allow, host) классификатор никогда не создаёт."
)

_HOST_WARNING = (
    "Commands matching '{pattern}' will always run OUTSIDE the sandbox: with your real HOME, your logins "
    "(git, gh, ssh) and without hiding .env files or keys. They will not ask again. Network access is still a "
    "separate permission. Dangerous commands still ask every time."
)
_HOST_WARNING_RU = (
    "Команды по шаблону '{pattern}' всегда будут запускаться ВНЕ песочницы: с вашим настоящим HOME, вашими "
    "логинами (git, gh, ssh) и без скрытия файлов .env и ключей. Больше они спрашивать не будут. Доступ к сети "
    "по-прежнему разрешается отдельно. Опасные команды всё равно спрашивают каждый раз."
)


def _pick(ru: bool, russian: str, english: str) -> str:
    return russian if ru else english


def usage(ru: bool) -> str:
    if ru:
        return (
            "Без аргументов открывается меню. Те же действия можно ввести:\n"
            "  /permissions show               правила и состояние\n"
            "  /permissions allow <команда>    больше не спрашивать про эту команду\n"
            "  /permissions host <команда>     всегда запускать вне песочницы (один раз спросит подтверждение)\n"
            "  /permissions ask <команда>      всегда спрашивать, даже если команда разрешена\n"
            "  /permissions deny <команда>     никогда не запускать\n"
            "  /permissions remove <команда>   удалить правило\n"
            "  /permissions check <команда>    объяснить, что Kvist сделает с командой\n"
            "  /permissions auto [on|off]      вторая модель отвечает на вопросы о подтверждении (НЕБЕЗОПАСНО)\n"
            "Правило это начало команды: 'npm run' покрывает и 'npm run build', а 'npm run *' любую команду, "
            "которая начинается с 'npm run'. Одна звёздочка для allow и host не принимается."
        )
    return (
        "Bare, the command opens a menu. The same actions can be typed:\n"
        "  /permissions show               the rules and the state\n"
        "  /permissions allow <command>    never ask about this command again\n"
        "  /permissions host <command>     always run it outside the sandbox (asks you once to confirm)\n"
        "  /permissions ask <command>      always ask, even if the command is allowed\n"
        "  /permissions deny <command>     never run it\n"
        "  /permissions remove <command>   delete the rule\n"
        "  /permissions check <command>    explain what Kvist would do with the command\n"
        "  /permissions auto [on|off]      a second model answers the approval questions (NOT SAFE)\n"
        "A rule is the start of a command: 'npm run' also covers 'npm run build', and 'npm run *' covers every "
        "command that begins with 'npm run'. A lone star is not accepted for allow and host."
    )


def run(agent, args: list[str], confirm: Confirm, ru: bool, ui: Any = None) -> tuple[bool, str]:
    """Run one ``/permissions`` line; returns ``(ok, text)``.

    Bare, it opens the menu when there is a ``ui`` to show it on, else it prints the state.
    """
    if not args:
        if ui is not None:
            return True, dialog(agent, ui, confirm, ru)
        return True, state_text(agent, ru) + "\n\n" + usage(ru)
    name, rest = args[0].lower(), args[1:]
    if name == "show":
        return True, state_text(agent, ru) + "\n\n" + usage(ru)
    if name in RULE_COMMANDS:
        return _add_rule(agent, name, " ".join(rest).strip(), confirm, ru)
    if name == "remove":
        return _remove_rule(agent, " ".join(rest).strip(), ru)
    if name == "auto":
        return _auto(agent, rest, confirm, ru)
    # A command typed after /permissions was explained before the subcommands existed.
    return _check(agent, rest if name == "check" else args, ru)


def state_text(agent, ru: bool) -> str:
    policy, note = load_policy(agent.executor.workspace)
    labels = {
        "allow": _pick(ru, "Разрешено (allow)", "Allow"),
        "host": _pick(ru, "Вне песочницы (host)", "Outside the sandbox (host)"),
        "ask": _pick(ru, "Спрашивать (ask)", "Ask"),
        "deny": _pick(ru, "Запрещено (deny)", "Deny"),
    }
    lines = [_pick(ru, "Правила проекта (.kvist/permissions.json)", "Project rules (.kvist/permissions.json)")]
    if note:
        lines.append(note)
    for key in RULE_LISTS:
        rules = getattr(policy, key)
        lines.append(f"{labels[key]}: " + (", ".join(rules) if rules else _pick(ru, "(пусто)", "(none)")))
    lines.append(_auto_state(agent, ru))
    return "\n".join(lines)


def _auto_state(agent, ru: bool) -> str:
    if ru:
        return "Автоматический режим: " + ("ВКЛЮЧЁН, небезопасно" if agent.auto_approval else "выключен")
    return "Auto mode: " + ("ON, not safe" if agent.auto_approval else "off")


# --- the menu -----------------------------------------------------------------


def dialog(agent, ui: Any, confirm: Confirm, ru: bool) -> str:
    """Arrows and Enter until the person leaves; returns the last notice, or ``""``."""
    notice = ""
    while True:
        choice = ui.choose_option(
            _pick(ru, "Разрешения команд", "Command permissions"),
            _menu_options(agent, ru),
            help_text="\n\n".join(part for part in (notice, state_text(agent, ru)) if part),
        )
        if choice is None:
            return notice
        notice = _menu_action(agent, ui, confirm, ru, choice)


def _menu_options(agent, ru: bool) -> list[dict[str, str]]:
    auto = _pick(ru, "включить или выключить", "turn on or off")
    rows = [
        ("allow", _pick(ru, "Больше не спрашивать про команду", "Never ask about a command"),
         _pick(ru, "правило allow", "an allow rule")),
        ("host", _pick(ru, "Всегда запускать вне песочницы", "Always run outside the sandbox"),
         _pick(ru, "правило host, небезопасно", "a host rule, not safe")),
        ("ask", _pick(ru, "Всегда спрашивать про команду", "Always ask about a command"),
         _pick(ru, "правило ask", "an ask rule")),
        ("deny", _pick(ru, "Никогда не запускать команду", "Never run a command"),
         _pick(ru, "правило deny", "a deny rule")),
        ("remove", _pick(ru, "Удалить правило", "Remove a rule"), _pick(ru, "выбрать из списка", "pick from the list")),
        ("check", _pick(ru, "Что будет с командой", "What would happen to a command"),
         _pick(ru, "объяснить, не запуская", "explain without running it")),
        ("auto", _pick(ru, "Автоматический режим", "Auto mode"), auto + " · " + _auto_state(agent, ru)),
    ]
    return [{"value": value, "label": label, "description": description} for value, label, description in rows]


def _menu_action(agent, ui: Any, confirm: Confirm, ru: bool, choice: str) -> str:
    if choice == "remove":
        return _menu_remove(agent, ui, ru)
    if choice == "auto":
        return _menu_auto(agent, ui, confirm, ru)
    hint = _pick(
        ru,
        "Начало команды. 'npm run *' покрывает любую команду, которая начинается с 'npm run'.",
        "The start of a command. 'npm run *' covers every command that begins with 'npm run'.",
    )
    command = ui.prompt_text(_pick(ru, "Команда", "Command"), help_text=hint)
    if not command or not command.strip():
        return ""
    if choice == "check":
        return _check(agent, command.split(), ru)[1]
    return _add_rule(agent, choice, command.strip(), confirm, ru)[1]


def _menu_remove(agent, ui: Any, ru: bool) -> str:
    policy, _ = load_policy(agent.executor.workspace)
    options = [
        {"value": f"{key}\t{pattern}", "label": pattern, "description": key}
        for key in RULE_LISTS
        for pattern in getattr(policy, key)
    ]
    if not options:
        return _pick(ru, "Правил пока нет.", "There are no rules yet.")
    chosen = ui.choose_option(_pick(ru, "Какое правило удалить", "Which rule to remove"), options)
    if chosen is None:
        return ""
    key, pattern = chosen.split("\t", 1)
    return _remove_rule(agent, pattern, ru, key)[1]


def _menu_auto(agent, ui: Any, confirm: Confirm, ru: bool) -> str:
    options = [
        {"value": "on", "label": _pick(ru, "Включить (небезопасно)", "Turn on (not safe)"),
         "description": _pick(ru, "перед включением покажу предупреждение", "a warning comes first")},
        {"value": "off", "label": _pick(ru, "Выключить", "Turn off"), "description": ""},
    ]
    current = "on" if agent.auto_approval else "off"
    chosen = ui.choose_option(_pick(ru, "Автоматический режим", "Auto mode"), options, current_value=current)
    if chosen is None:
        return ""
    return _auto(agent, [chosen], confirm, ru)[1]


# --- the actions --------------------------------------------------------------


def _add_rule(agent, kind: str, pattern: str, confirm: Confirm, ru: bool) -> tuple[bool, str]:
    if not pattern:
        return False, usage(ru)
    if kind in ("allow", "host") and not pattern_is_specific(pattern):
        return False, _pick(
            ru,
            "Правило должно начинаться с имени команды: 'npm run *' подходит, одна звёздочка нет.",
            "A rule must start with the command's name: 'npm run *' works, a lone star does not.",
        )
    if kind == "host":
        warning = (_HOST_WARNING_RU if ru else _HOST_WARNING).format(pattern=pattern)
        if not confirm(_pick(ru, "Запуск вне песочницы", "Run outside the sandbox"), warning):
            return False, _pick(ru, "Правило не добавлено.", "Rule not added.")
    try:
        append_rule(agent.executor.workspace, kind, pattern)
    except ValueError as error:
        return False, str(error)
    return True, _pick(ru, f"Добавлено в {kind}: {pattern}", f"Added to {kind}: {pattern}")


def _remove_rule(agent, pattern: str, ru: bool, key: str | None = None) -> tuple[bool, str]:
    if not pattern:
        return False, usage(ru)
    try:
        removed = remove_rule(agent.executor.workspace, pattern, key)
    except ValueError as error:
        return False, str(error)
    if not removed:
        return False, _pick(ru, f"Такого правила нет: {pattern}", f"No such rule: {pattern}")
    where = ", ".join(removed)
    return True, _pick(ru, f"Удалено из {where}: {pattern}", f"Removed from {where}: {pattern}")


def _auto(agent, rest: list[str], confirm: Confirm, ru: bool) -> tuple[bool, str]:
    choice = rest[0].lower() if rest else ""
    if choice not in ("on", "off"):
        return True, _auto_state(agent, ru)
    if choice == "off":
        agent.set_auto_approval(False)
        save_auto_approval(False)
        return True, _pick(ru, "Автоматический режим выключен.", "Auto mode is off.")
    title = _pick(ru, "Автоматический режим небезопасен", "Auto mode is not safe")
    if not confirm(title, _AUTO_WARNING_RU if ru else _AUTO_WARNING):
        return False, _pick(ru, "Автоматический режим не включён.", "Auto mode not turned on.")
    agent.set_auto_approval(True)
    save_auto_approval(True)
    return True, _pick(
        ru,
        "Автоматический режим ВКЛЮЧЁН. Он небезопасен. Выключить: /permissions auto off.",
        "Auto mode is ON. It is not safe. Turn it off with /permissions auto off.",
    )


def _check(agent, command: list[str], ru: bool) -> tuple[bool, str]:
    if not command:
        return False, usage(ru)
    explanation = agent.executor.explain_shell_command(command, role=agent.executor.active_role)
    lines = [f"{' '.join(command)} → {explanation.get('verdict', 'unknown')}"]
    lines.extend(f"  {reason}" for reason in explanation.get("reasons", []))
    return True, "\n".join(lines)
