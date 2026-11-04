# -*- coding: utf-8 -*-
"""The few sentences the remote machinery says itself, in the person's language."""
from __future__ import annotations

import os

_TEXTS = {
    "observer": {
        "en": "another frontend is driving this session · Enter — take over",
        "ru": "сессией управляет другой фронтенд · Enter — перехватить управление",
    },
    "observer_gone": {
        "en": "the frontend that was driving this session stopped answering · Enter — take control",
        "ru": "фронтенд, который вёл сессию, перестал отвечать · Enter — взять управление",
    },
    "restarted": {
        "en": "Kvist on this server was restarted. A task that was running can be continued with /resume.",
        "ru": "Kvist на этом сервере перезапущен. Прерванную задачу можно продолжить командой /resume.",
    },
    "not_connected": {
        "en": "Not connected to the server yet; your message was not sent.",
        "ru": "Соединения с сервером пока нет; сообщение не отправлено.",
    },
    "host_key": {
        "en": "The host key of {host} is not trusted yet. Run `ssh {host}` once, accept it, and try again.",
        "ru": "Ключ хоста {host} ещё не доверенный. Выполните `ssh {host}` один раз, примите ключ и повторите.",
    },
    "auth": {
        "en": "ssh could not log in to {host} without typing anything. Set up a key or ssh-agent; passwords cannot be entered here.",
        "ru": "ssh не смог войти на {host} без ввода. Настройте ключ или ssh-agent: пароль здесь ввести нельзя.",
    },
    "not_found": {
        "en": "Kvist is not installed on {host}: kvist-core is neither in PATH nor in ~/.local/share/kvist.",
        "ru": "На {host} не установлен Kvist: kvist-core нет ни в PATH, ни в ~/.local/share/kvist.",
    },
    "old_kvist": {
        "en": "Kvist on {host} is too old for remote work: it does not know --remote-op. Put a build that has it there; "
        "a development build can be copied with: python scripts/install_on_machine.py NAME",
        "ru": "Kvist на {host} слишком старый для удалённой работы: он не знает --remote-op. Поставьте туда сборку с ним; "
        "сборку из исходников можно скопировать командой: python scripts/install_on_machine.py ИМЯ",
    },
    "install_none": {
        "en": "Kvist is not installed on {host}.",
        "ru": "На {host} Kvist не установлен.",
    },
    "install_old": {
        "en": "Kvist on {host} is too old for remote work.",
        "ru": "Kvist на {host} слишком старый для удалённой работы.",
    },
    "daemon_old_title": {
        "en": "The agent running on {host} for this folder was started by an older Kvist and cannot work with your provider. Restart it?",
        "ru": "Агент, запущенный на {host} для этой папки, стартовал со старым Kvist и не умеет работать с вашим провайдером. Перезапустить его?",
    },
    "daemon_old_busy": {
        "en": "{count} other interface(s) are connected to it, and a turn in progress is cancelled by a restart. ",
        "ru": "К нему подключено других интерфейсов: {count}, а ход, который идёт, при перезапуске отменяется. ",
    },
    "daemon_restart": {"en": "Restart it now", "ru": "Перезапустить сейчас"},
    "daemon_old": {
        "en": "The agent on {host} is older than this Kvist and cannot work with your provider. Restart it: ssh {host} kvist-core --remote-op stop --workspace {path}, then connect again.",
        "ru": "Агент на {host} старше этого Kvist и не умеет работать с вашим провайдером. Перезапустите его: ssh {host} kvist-core --remote-op stop --workspace {path}, затем подключитесь снова.",
    },
    "install_copy": {
        "en": "Copy this computer's Kvist to {host} (about {mb} MB)",
        "ru": "Скопировать на {host} Kvist с этого компьютера (около {mb} МБ)",
    },
    "install_sources": {
        "en": "Copy this checkout's sources to {host} and set them up there (needs Python 3.11 and internet there)",
        "ru": "Скопировать на {host} исходники из этого репозитория и настроить их там (нужны Python 3.11 и интернет на сервере)",
    },
    "copy_does_not_run": {
        "en": "The copy does not run on {host}: {reason}\nA program built on a newer system than the server's needs a newer C library. "
        "Use a release build (made on an older system), or copy the sources from a checkout: python scripts/install_on_machine.py NAME",
        "ru": "Копия на {host} не запускается: {reason}\nПрограмма, собранная на более новой системе, чем на сервере, требует более новую библиотеку C. "
        "Возьмите релизную сборку (её делают на более старой системе) или скопируйте исходники из репозитория: python scripts/install_on_machine.py ИМЯ",
    },
    "installing": {
        "en": "Copying Kvist to {host}… this can take a minute.",
        "ru": "Копирую Kvist на {host}… это может занять минуту.",
    },
    "installed": {
        "en": "Kvist is on {host} now ({version}).",
        "ru": "Kvist на {host} установлен ({version}).",
    },
    "no_service": {
        "en": "Kvist is not running on {host} for {path}. Start it with: kvist --remote-op install-service --remote {host} --remote-workspace {path}",
        "ru": "На {host} Kvist для {path} не запущен. Запустите: kvist --remote-op install-service --remote {host} --remote-workspace {path}",
    },
    "rotated": {
        "en": "The server started a new event log while you were away; open the connection again.",
        "ru": "Пока вас не было, сервер начал новый журнал событий; подключитесь заново.",
    },
    "interrupted": {
        "en": "The connection broke while the log was being replayed; open the connection again.",
        "ru": "Соединение оборвалось во время повтора журнала; подключитесь заново.",
    },
    "unreachable": {
        "en": "{host} is not answering",
        "ru": "{host} не отвечает",
    },
    "protocol": {
        "en": "Kvist on {host} speaks protocol {theirs}, this one {ours}. Update the older one.",
        "ru": "Kvist на {host} говорит по протоколу {theirs}, этот — по {ours}. Обновите более старый.",
    },
    "no_provider": {
        "en": "There is no provider to work with: none is set up on this computer for {host} to borrow (the ChatGPT sign-in cannot be lent), and none on {host} itself. Set one up here with /provider.",
        "ru": "Нет провайдера, с которым можно работать: на этом компьютере нет такого, который можно одолжить {host} (вход через ChatGPT одолжить нельзя), и на самом {host} тоже. Настройте его здесь командой /provider.",
    },
    "no_folder": {
        "en": "{path} is not a folder on {host}.",
        "ru": "{path} — не папка на {host}.",
    },
    "unsupported": {
        "en": "{host} runs a system the remote core does not support (it needs Linux, macOS or a BSD).",
        "ru": "На {host} система, которую удалённое ядро не поддерживает (нужны Linux, macOS или BSD).",
    },
    "service_title": {
        "en": "Kvist is not running for {path} on {host}.",
        "ru": "Kvist для {path} на {host} не запущен.",
    },
    "service_install": {
        "en": "Install it as a service that survives reboots (recommended)",
        "ru": "Установить как службу, которая переживает перезагрузку (рекомендуется)",
    },
    "service_once": {
        "en": "Start it once, until the next reboot",
        "ru": "Запустить один раз, до ближайшей перезагрузки",
    },
    "cancel": {"en": "Cancel", "ru": "Отмена"},
    "ask_folder": {
        "en": "{path} does not exist on {host}. Create it?",
        "ru": "Папки {path} на {host} нет. Создать?",
    },
    "cannot_switch": {
        "en": "Another frontend is driving this machine, so session {session} was not opened. Take over first.",
        "ru": "На этой машине сейчас управляет другой фронтенд, поэтому сессия {session} не открыта. Сначала перехватите управление.",
    },
    "yes": {"en": "Yes", "ru": "Да"},
    "no": {"en": "No", "ru": "Нет"},
    "not_opened_title": {
        "en": "Could not open {label}.",
        "ru": "Открыть {label} не удалось.",
    },
    "work_here": {"en": "Work on this computer", "ru": "Работать на этом компьютере"},
    "try_again": {"en": "Try again", "ru": "Попробовать ещё раз"},
    "close": {"en": "Close", "ru": "Закрыть"},
    "connecting": {"en": "Connecting to {label}…", "ru": "Подключаюсь к {label}…"},
    "opening": {
        "en": "Opening {label}. If this window stays as it is, start Kvist there with: kvist --machine {name}",
        "ru": "Открываю {label}. Если окно не изменилось, запустите Kvist там командой: kvist --machine {name}",
    },
    "opening_remote": {
        "en": "Opening {label}. If this window stays as it is, start Kvist there with: kvist --remote {host} --remote-workspace {folder}",
        "ru": "Открываю {label}. Если окно не изменилось, запустите Kvist там командой: kvist --remote {host} --remote-workspace {folder}",
    },
    "opening_local": {
        "en": "Opening this computer. If this window stays as it is, start Kvist again with: kvist --local",
        "ru": "Открываю этот компьютер. Если окно не изменилось, запустите Kvist заново командой: kvist --local",
    },
    "not_opened": {"en": "Could not open {label}.", "ru": "Открыть {label} не удалось."},
    "remote_usage": {
        "en": "Usage: /remote user@host /folder-on-the-server, or /remote off.",
        "ru": "Использование: /remote user@host /папка-на-сервере, или /remote off.",
    },
    "op_failed": {
        "en": "{host} answered: {error}",
        "ru": "{host} ответил: {error}",
    },
    "gave_up": {
        "en": "Gave up reconnecting to {host} after {minutes} min.",
        "ru": "Попытки переподключиться к {host} прекращены через {minutes} мин.",
    },
}


def ui_language(env: dict[str, str] | None = None) -> str:
    """``ru`` when the locale says Russian, ``en`` otherwise (the setup screen reads the same variables)."""
    env = env if env is not None else os.environ
    for name in ("KVIST_LANG", "LC_ALL", "LANG"):
        value = env.get(name, "")
        if value:
            return "ru" if value.lower().startswith("ru") else "en"
    return "en"


def text(key: str, language: str, **fields: object) -> str:
    variants = _TEXTS[key]
    return variants.get(language, variants["en"]).format(**fields)
