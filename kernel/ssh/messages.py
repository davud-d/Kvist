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
        "en": "Kvist on {host} has no provider set up yet. Run `ssh -t {host} kvist-core --setup` once, then try again.",
        "ru": "На {host} в Kvist ещё не настроен провайдер. Выполните один раз `ssh -t {host} kvist-core --setup` и повторите.",
    },
    "no_folder": {
        "en": "{path} is not a folder on {host}.",
        "ru": "{path} — не папка на {host}.",
    },
    "unsupported": {
        "en": "{host} runs a system the remote core does not support (it needs Linux, macOS or a BSD).",
        "ru": "На {host} система, которую удалённое ядро не поддерживает (нужны Linux, macOS или BSD).",
    },
    "ask_service": {
        "en": "Kvist is not running for {path} on {host}.\n  1) install it as a service that survives reboots (recommended)\n  2) start it once, until the next reboot\n  3) cancel\nChoose [1]: ",
        "ru": "Kvist для {path} на {host} не запущен.\n  1) установить как службу, которая переживает перезагрузку (рекомендуется)\n  2) запустить один раз, до ближайшей перезагрузки\n  3) отмена\nВыбор [1]: ",
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
