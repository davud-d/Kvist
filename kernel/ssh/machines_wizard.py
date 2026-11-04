# -*- coding: utf-8 -*-
"""The machines dialog: save, change and choose the machines Kvist works on.

It runs in the launcher on the person's terminal, with the same widgets as ``/provider`` (``choose_option``,
``prompt_text``, ``prompt_secret``), and ends with a ``Choice``: where to open the interface next.
``ui`` is anything with those three methods, so tests drive it with a script.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from kernel.ssh import access, machines, messages
from kernel.ssh.command import Target, parse_target
from kernel.ssh.machines import Machine, STANDARD_FOLDER
from kernel.ssh.paths import RemoteError

_TEXT = {
    "title": {"en": "Machines", "ru": "Машины"},
    "help": {"en": "↑/↓ — select, Enter — open, Esc — back", "ru": "↑/↓ — выбор, Enter — открыть, Esc — назад"},
    "add": {"en": "+ Add a machine…", "ru": "+ Добавить машину…"},
    "local": {"en": "This computer", "ru": "Этот компьютер"},
    "local_hint": {"en": "work here, without a server", "ru": "работать здесь, без сервера"},
    "recent": {"en": "Recent sessions…", "ru": "Недавние сессии…"},
    "recent_hint": {"en": "sessions on all machines", "ru": "сессии на всех машинах"},
    "main_mark": {"en": "main", "ru": "основная"},
    "address": {"en": "Address", "ru": "Адрес"},
    "address_help": {
        "en": "login@host, an IP address, or a name from ~/.ssh/config. A port may follow: login@host:2222",
        "ru": "логин@хост, IP-адрес или имя из ~/.ssh/config. Порт можно дописать: логин@хост:2222",
    },
    "port": {"en": "Port", "ru": "Порт"},
    "port_help": {"en": "Empty — the usual one (22)", "ru": "Пусто — обычный (22)"},
    "name": {"en": "Name for this machine", "ru": "Имя этой машины"},
    "name_help": {"en": "Lowercase letters, digits, - and _", "ru": "Строчные буквы, цифры, - и _"},
    "bad_name": {"en": "That name is taken or not allowed.", "ru": "Такое имя занято или недопустимо."},
    "method": {"en": "How to sign in", "ru": "Как входить"},
    "method_as_is": {"en": "As ssh does (agent, ~/.ssh/config)", "ru": "Как делает ssh (agent, ~/.ssh/config)"},
    "method_key": {"en": "A key file", "ru": "Файл ключа"},
    "method_password": {"en": "A password, once", "ru": "Пароль, один раз"},
    "method_password_hint": {
        "en": "Kvist makes a key, installs it on the machine, and forgets the password",
        "ru": "Kvist создаст ключ, установит его на машину и забудет пароль",
    },
    "key_path": {"en": "Path to the private key", "ru": "Путь к закрытому ключу"},
    "password": {"en": "Password for {host}", "ru": "Пароль для {host}"},
    "password_help": {"en": "It is used once and not saved", "ru": "Используется один раз и не сохраняется"},
    "trust": {"en": "Trust this machine? Its key is {kind} {fingerprint}", "ru": "Доверять этой машине? Её ключ {kind} {fingerprint}"},
    "trust_help": {
        "en": "Compare it with what the machine's owner gave you. Yes adds it to ~/.ssh/known_hosts",
        "ru": "Сверьте с тем, что сказал владелец машины. «Да» добавит его в ~/.ssh/known_hosts",
    },
    "yes": {"en": "Yes", "ru": "Да"},
    "no": {"en": "No", "ru": "Нет"},
    "folder": {"en": "Folder on {name}", "ru": "Папка на {name}"},
    "folder_help": {
        "en": "Empty — the standard one ({standard}); it is offered to be created",
        "ru": "Пусто — стандартная ({standard}); её предложат создать",
    },
    "login_failed": {"en": "Could not sign in: {detail}", "ru": "Войти не удалось: {detail}"},
    "failed_choice": {"en": "What now?", "ru": "Что дальше?"},
    "retry": {"en": "Try again", "ru": "Ещё раз"},
    "save_anyway": {"en": "Save it anyway", "ru": "Сохранить всё равно"},
    "cancel": {"en": "Cancel", "ru": "Отмена"},
    "make_main": {"en": "Make {name} the main machine?", "ru": "Сделать {name} основной машиной?"},
    "connect": {"en": "Connect", "ru": "Подключиться"},
    "set_folder": {"en": "Folder…", "ru": "Папка…"},
    "edit": {"en": "Change address or sign-in…", "ru": "Изменить адрес или вход…"},
    "main": {"en": "Make main", "ru": "Сделать основной"},
    "unmain": {"en": "Stop being main", "ru": "Больше не основная"},
    "delete": {"en": "Delete", "ru": "Удалить"},
    "delete_ask": {"en": "Forget {name}? Its files stay on the machine.", "ru": "Забыть {name}? Файлы на машине останутся."},
    "other_folder": {"en": "Another folder…", "ru": "Другая папка…"},
    "which_folder": {"en": "Folder to work in on {name}", "ru": "В какой папке работать на {name}"},
    "no_tools": {"en": "ssh tools are missing: {tools}", "ru": "Не хватает программ ssh: {tools}"},
    "no_recent": {"en": "No sessions on other machines yet.", "ru": "Сессий на машинах пока нет."},
    "default_folder": {"en": "default", "ru": "по умолчанию"},
}


def _t(key: str, language: str, **fields: object) -> str:
    variants = _TEXT[key]
    return variants.get(language, variants["en"]).format(**fields)


@dataclass(frozen=True, slots=True)
class Choice:
    """Where to open the interface: ``machine`` None means this computer."""

    machine: Machine | None
    folder: str | None = None
    session: str | None = None


def _yes(ui, title: str, language: str, help_text: str = "") -> bool:
    options = [{"value": "yes", "label": _t("yes", language)}, {"value": "no", "label": _t("no", language)}]
    return ui.choose_option(title, options, current_value="yes", help_text=help_text) == "yes"


def _suggested_name(host: str) -> str:
    base = re.sub(r"[^a-z0-9_-]+", "-", host.rsplit("@", 1)[-1].lower()).strip("-_") or "server"
    taken = {machine.name for machine in machines.list_machines()}
    name, number = base[:30], 2
    while name in taken:
        name, number = f"{base[:27]}-{number}", number + 1
    return name


# --- adding or changing a machine ------------------------------------------------------------------


def _ask_address(ui, language: str, current: str = "") -> Target | None:
    while True:
        text = ui.prompt_text(_t("address", language), current_value=current, help_text=_t("address_help", language))
        if text is None or not text.strip():
            return None
        try:
            return parse_target(text, "/")
        except RemoteError as error:
            current = text
            ui.print_error(str(error))


def _ask_port(ui, language: str, target: Target, current: int | None) -> int | None:
    if target.port:
        return target.port
    text = ui.prompt_text(_t("port", language), current_value=str(current or ""), help_text=_t("port_help", language))
    return int(text) if text and text.strip().isdigit() else None


def _ask_name(ui, language: str, host: str, current: str | None) -> str | None:
    suggestion = current or _suggested_name(host)
    while True:
        name = ui.prompt_text(_t("name", language), current_value=suggestion, help_text=_t("name_help", language))
        if name is None:
            return None
        name = name.strip().lower()
        taken = machines.get_machine(name) is not None and name != current
        if machines.is_valid_name(name) and not taken:
            return name
        ui.print_error(_t("bad_name", language))
        suggestion = name


def _trust_host(ui, language: str, target: Target) -> bool:
    """Make sure ssh knows the machine's key; the person confirms its fingerprint the first time."""
    endpoint = access.resolve_endpoint(target.host, target.port)
    if access.host_is_known(endpoint):
        return True
    scanned = access.scan_host_key(endpoint)
    title = _t("trust", language, kind=scanned.kind, fingerprint=scanned.fingerprint)
    if not _yes(ui, title, language, _t("trust_help", language)):
        return False
    access.trust_host_key(scanned)
    return True


def _ask_method(ui, language: str) -> str | None:
    options = [
        {"value": "as_is", "label": _t("method_as_is", language), "description": ""},
        {"value": "key", "label": _t("method_key", language), "description": ""},
        {"value": "password", "label": _t("method_password", language), "description": _t("method_password_hint", language)},
    ]
    return ui.choose_option(_t("method", language), options, current_value="as_is", help_text=_t("help", language))


def _ask_key(ui, language: str, current: str | None) -> str | None:
    while True:
        text = ui.prompt_text(_t("key_path", language), current_value=current or "~/.ssh/id_ed25519", help_text="")
        if text is None or not text.strip():
            return None
        path = str(Path(text.strip()).expanduser())
        problem = access.usable_key_file(path)
        if problem is None:
            return path
        current = text
        ui.print_error(problem)


def _sign_in(ui, language: str, name: str, target: Target, method: str, key: str | None) -> tuple[bool, str | None]:
    """Get in with the chosen method; returns (worked, key file to remember)."""
    if method == "password":
        password = ui.prompt_secret(_t("password", language, host=target.label), current_value="", help_text=_t("password_help", language))
        if password is None:
            return False, None
        key = access.install_key_with_password(name, target, password)
        return True, key
    if method == "key":
        key = _ask_key(ui, language, key)
        if key is None:
            return False, None
    login = access.check_login(Target(target.host, target.port, target.workspace, key))
    if login.ok:
        return True, key
    raise RemoteError(_t("login_failed", language, detail=login.detail or login.problem))


def _after_failure(ui, language: str, error: RemoteError) -> str:
    options = [
        {"value": "retry", "label": _t("retry", language)},
        {"value": "save", "label": _t("save_anyway", language)},
        {"value": "cancel", "label": _t("cancel", language)},
    ]
    return ui.choose_option(f"{error}\n{_t('failed_choice', language)}", options, current_value="retry", help_text="") or "cancel"


def _ask_folder(ui, language: str, name: str, current: str | None) -> str | None:
    text = ui.prompt_text(
        _t("folder", language, name=name),
        current_value=current or "",
        help_text=_t("folder_help", language, standard=STANDARD_FOLDER),
    )
    return (text or "").strip() or None


def add_or_edit_machine(ui, language: str, existing: Machine | None = None) -> Machine | None:
    """Ask for everything about one machine and save it; None when the person backed out."""
    missing = access.tools_missing()
    if missing:
        ui.print_error(_t("no_tools", language, tools=", ".join(missing)))
        return None
    target = _ask_address(ui, language, existing.spec if existing else "")
    if target is None:
        return None
    port = _ask_port(ui, language, target, existing.port if existing else None)
    name = _ask_name(ui, language, target.host, existing.name if existing else None)
    if name is None:
        return None
    target = Target(target.host, port, "/", None)
    while True:
        method = _ask_method(ui, language)
        if method is None:
            return None
        try:
            if not _trust_host(ui, language, target):
                return None
            worked, key = _sign_in(ui, language, name, target, method, existing.key if existing else None)
            if not worked:
                return None
            break
        except RemoteError as error:
            decision = _after_failure(ui, language, error)
            if decision == "cancel":
                return None
            if decision == "save":
                key = existing.key if existing else None
                break
    folder = _ask_folder(ui, language, name, existing.folder if existing else None)
    machine = Machine(name, target.host, port, key, folder, existing.folders if existing else ())
    if existing and existing.name != name:
        machines.delete_machine(existing.name)
    machines.save_machine(machine)
    if machines.main_machine() is None and _yes(ui, _t("make_main", language, name=name), language):
        machines.set_main(name)
    return machine


# --- the menus -------------------------------------------------------------------------------------


def _pick_folder(ui, language: str, machine: Machine) -> str | None:
    """Which folder to work in on ``machine`` this time; the machine's own first."""
    seen: list[str] = []
    options = []
    for folder in (machine.work_folder, *machine.folders):
        if folder in seen:
            continue
        seen.append(folder)
        hint = _t("default_folder", language) if folder == machine.work_folder else ""
        options.append({"value": folder, "label": folder, "description": hint})
    options.append({"value": "@other", "label": _t("other_folder", language), "description": ""})
    chosen = ui.choose_option(_t("which_folder", language, name=machine.name), options, current_value=machine.work_folder, help_text="")
    if chosen != "@other":
        return chosen
    return _ask_folder(ui, language, machine.name, None)


def _machine_menu(ui, language: str, machine: Machine) -> Choice | None:
    main = machines.main_machine()
    is_main = main is not None and main.name == machine.name
    options = [
        {"value": "connect", "label": _t("connect", language), "description": ""},
        {"value": "folder", "label": _t("set_folder", language), "description": machine.work_folder},
        {"value": "edit", "label": _t("edit", language), "description": machine.spec},
        {"value": "unmain" if is_main else "main", "label": _t("unmain" if is_main else "main", language), "description": ""},
        {"value": "delete", "label": _t("delete", language), "description": ""},
    ]
    action = ui.choose_option(machine.name, options, current_value="connect", help_text=_t("help", language))
    if action == "connect":
        folder = _pick_folder(ui, language, machine)
        return Choice(machine, folder) if folder else None
    if action == "folder":
        folder = _ask_folder(ui, language, machine.name, machine.folder)
        machines.save_machine(Machine(machine.name, machine.host, machine.port, machine.key, folder, machine.folders))
    elif action == "edit":
        add_or_edit_machine(ui, language, machine)
    elif action in ("main", "unmain"):
        machines.set_main(machine.name if action == "main" else None)
    elif action == "delete" and _yes(ui, _t("delete_ask", language, name=machine.name), language):
        machines.delete_machine(machine.name)
    return None


def _age(seconds: float, language: str) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "now" if language == "en" else "сейчас"
    if minutes < 60:
        return f"{minutes} min" if language == "en" else f"{minutes} мин"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h" if language == "en" else f"{hours} ч"
    return f"{hours // 24} d" if language == "en" else f"{hours // 24} дн"


def _recent_menu(ui, language: str, now: float) -> Choice | None:
    known = {machine.name: machine for machine in machines.list_machines()}
    records = [record for record in machines.recent_sessions() if record.machine in known]
    if not records:
        ui.print_error(_t("no_recent", language))
        return None
    options = [
        {
            "value": str(index),
            "label": f"{record.machine} · {record.title or record.session}",
            "description": f"{record.folder} · {_age(now - record.updated, language)}",
        }
        for index, record in enumerate(records)
    ]
    chosen = ui.choose_option(_t("recent", language), options, current_value="0", help_text=_t("help", language))
    if chosen is None:
        return None
    record = records[int(chosen)]
    return Choice(known[record.machine], record.folder, record.session)


def run_menu(ui, language: str | None = None, now: float | None = None) -> Choice | None:
    """The machines dialog. A Choice says where to open next; None leaves things as they are."""
    language = language or messages.ui_language()
    now = time.time() if now is None else now
    while True:
        main = machines.main_machine()
        options = [
            {
                "value": machine.name,
                "label": f"{'● ' if main and main.name == machine.name else '  '}{machine.name}",
                "description": f"{machine.spec} · {machine.work_folder}",
            }
            for machine in machines.list_machines()
        ]
        options += [
            {"value": "@add", "label": _t("add", language), "description": ""},
            {"value": "@recent", "label": _t("recent", language), "description": _t("recent_hint", language)},
            {"value": "@local", "label": _t("local", language), "description": _t("local_hint", language)},
        ]
        selected = ui.choose_option(_t("title", language), options, current_value=main.name if main else "@local", help_text=_t("help", language))
        if selected is None:
            return None
        choice = _selected(ui, language, selected, now)
        if choice is not None:
            return choice


def _selected(ui, language: str, selected: str, now: float) -> Choice | None:
    if selected == "@local":
        return Choice(None)
    if selected == "@add":
        machine = add_or_edit_machine(ui, language)
        # Its folder was just asked for.
        return Choice(machine, machine.work_folder) if machine else None
    if selected == "@recent":
        return _recent_menu(ui, language, now)
    machine = machines.get_machine(selected)
    return _machine_menu(ui, language, machine) if machine else None
