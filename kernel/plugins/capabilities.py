# -*- coding: utf-8 -*-
"""The closed catalog of capabilities a skill may declare and the core may grant.

A manifest naming anything outside this catalog is refused, so a package cannot
invent a privilege the core would not know how to check. ``writes`` marks a
capability whose use changes the workspace or the outside world: reader runs
and plan mode refuse those calls whatever the grant says. ``egress`` marks a
capability that sends data off the machine (to a model provider or the web).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    en: str
    ru: str
    writes: bool = False
    egress: bool = False
    # Keys a manifest may put in this capability's scope object.
    scope_keys: frozenset[str] = frozenset()


_CATALOG = (
    Capability("input.read", "Read the task text, the person's follow-ups and the list of attachments",
               "Читать текст задачи, уточнения пользователя и список вложений"),
    Capability("images.read", "Read image attachments' details and make cropped or resized copies on this machine",
               "Читать сведения о вложенных изображениях и делать их уменьшенные или обрезанные копии на этом компьютере"),
    Capability("images.original", "Receive the original bytes of image attachments",
               "Получать исходные файлы вложенных изображений"),
    Capability("images.analyze", "Send images to the configured Vision model for analysis",
               "Отправлять изображения настроенной Vision-модели для анализа", egress=True),
    Capability("models.call", "Make requests to your configured language models (counted in the task's tokens)",
               "Обращаться к настроенным языковым моделям (расход учитывается в задаче)", egress=True,
               scope_keys=frozenset({"max_calls", "max_tokens", "tiers"})),
    Capability("files.read", "Read files of this workspace (protected files stay hidden)",
               "Читать файлы этого проекта (защищённые файлы остаются скрытыми)", scope_keys=frozenset({"paths"})),
    Capability("files.write", "Create and change files of this workspace (journaled, revertable)",
               "Создавать и изменять файлы этого проекта (с журналом изменений)", writes=True,
               scope_keys=frozenset({"paths"})),
    Capability("changes.read", "See the changes this task made and their origin",
               "Видеть изменения, сделанные в этой задаче, и их происхождение"),
    Capability("git.read", "Read git status and diffs", "Читать git status и diff"),
    Capability("processes.run", "Run commands in the workspace sandbox",
               "Запускать команды в песочнице проекта", writes=True),
    Capability("processes.background", "Keep commands running in the background for this task (a dev server)",
               "Держать команды запущенными в фоне на время задачи (dev-сервер)", writes=True),
    Capability("web.search", "Search the web", "Искать в интернете", egress=True),
    Capability("web.fetch", "Fetch web pages as text", "Загружать веб-страницы как текст", egress=True),
    Capability("http.request", "Send HTTP requests to the listed addresses",
               "Отправлять HTTP-запросы по перечисленным адресам", egress=True, writes=True,
               scope_keys=frozenset({"domains"})),
    Capability("network.internet", "Let its own programs (a browser) reach the listed internet addresses",
               "Пускать свои программы (браузер) по перечисленным адресам в интернете", egress=True,
               scope_keys=frozenset({"domains"})),
    Capability("network.task_services", "Let its own programs reach the servers this task started",
               "Пускать свои программы к серверам, запущенным этой задачей"),
    Capability("connections.use", "Use a service connection you configured, by its name",
               "Использовать настроенное вами подключение к сервису по его имени", egress=True,
               scope_keys=frozenset({"connections"})),
    Capability("ui.notify", "Show short status lines and messages", "Показывать короткие статусы и сообщения"),
    Capability("ui.ask", "Ask you questions and wait for the answer", "Задавать вам вопросы и ждать ответа"),
    Capability("ui.present", "Show you images and files it produced", "Показывать вам созданные изображения и файлы"),
    Capability("sessions.read", "Read and search earlier conversations of this workspace",
               "Читать и искать прошлые разговоры этого проекта"),
    Capability("memory.read", "Read this workspace's memory", "Читать память этого проекта"),
    Capability("memory.write", "Add to and change this workspace's memory (never on your behalf)",
               "Добавлять и менять память этого проекта (никогда от вашего имени)"),
    Capability("storage", "Keep its own data for this workspace", "Хранить свои данные для этого проекта"),
    Capability("artifacts", "Save result files (screenshots, reports) for you",
               "Сохранять файлы результатов (снимки экрана, отчёты) для вас"),
    Capability("agents.manage", "Start a read-only helper agent", "Запускать помощницу только для чтения"),
    Capability("display", "Run programs on its own virtual screen, separate from yours",
               "Запускать программы на собственном виртуальном экране, отдельном от вашего"),
    Capability("host.open", "Open web addresses and generated visualizations in your browser and start installed applications",
               "Открывать веб-адреса и созданные визуализации в вашем браузере и запускать установленные приложения",
               writes=True),
    Capability("host.windows", "See your open windows (their titles), bring one to the front and close one after asking you",
               "Видеть ваши открытые окна (их заголовки), выводить окно вперёд и закрывать окно, спросив вас",
               writes=True),
    Capability("permissions.request", "Ask you for extra access during a task",
               "Просить у вас дополнительный доступ во время задачи"),
)

CAPABILITIES: dict[str, Capability] = {item.name: item for item in _CATALOG}
WRITE_CAPABILITIES = frozenset(item.name for item in _CATALOG if item.writes)


def describe(name: str, scope: dict[str, Any] | None, language: str) -> str:
    """One line for the approval dialog: what the capability lets the skill do, with its scope."""
    capability = CAPABILITIES[name]
    text = capability.ru if language == "ru" else capability.en
    details = _scope_text(scope or {}, language)
    return f"{text}: {details}" if details else text


def _scope_text(scope: dict[str, Any], language: str) -> str:
    parts: list[str] = []
    for key in ("domains", "paths", "connections"):
        values = scope.get(key)
        if isinstance(values, list) and values:
            parts.append(", ".join(str(item) for item in values))
    if "max_calls" in scope:
        parts.append(f"{'до' if language == 'ru' else 'up to'} {scope['max_calls']} "
                     f"{'запросов' if language == 'ru' else 'requests'}")
    if "max_tokens" in scope:
        parts.append(f"{'до' if language == 'ru' else 'up to'} {scope['max_tokens']} "
                     f"{'токенов' if language == 'ru' else 'tokens'}")
    return "; ".join(parts)
