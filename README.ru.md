# Kvist

[English version](README.md)

<p align="center">
  <a href="https://github.com/davud-d/Kvist"><img alt="GitHub repository" src="https://img.shields.io/badge/GitHub-Kvist-181717?style=for-the-badge&logo=github"></a>
  <a href="https://github.com/davud-d/Kvist/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/davud-d/Kvist?style=for-the-badge&logo=github&color=yellow"></a>
  <a href="https://github.com/davud-d/Kvist/forks"><img alt="GitHub forks" src="https://img.shields.io/github/forks/davud-d/Kvist?style=for-the-badge&logo=github"></a>
  <a href="https://github.com/davud-d/Kvist/issues"><img alt="GitHub issues" src="https://img.shields.io/github/issues/davud-d/Kvist?style=for-the-badge&logo=github"></a>
  <a href="https://github.com/davud-d/Kvist/commits"><img alt="GitHub last commit" src="https://img.shields.io/github/last-commit/davud-d/Kvist?style=for-the-badge&logo=github"></a>
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/github/license/davud-d/Kvist?style=for-the-badge&color=blue"></a>
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Windows" src="https://img.shields.io/badge/Windows-supported-0078D4?style=flat-square&logo=windows11&logoColor=white">
  <img alt="Linux" src="https://img.shields.io/badge/Linux-supported-FCC624?style=flat-square&logo=linux&logoColor=black">
  <img alt="Terminal CLI" src="https://img.shields.io/badge/Interface-Terminal_CLI-4D4D4D?style=flat-square&logo=windowsterminal&logoColor=white">
  <img alt="Electron GUI" src="https://img.shields.io/badge/Interface-Electron_GUI-47848F?style=flat-square&logo=electron&logoColor=white">
  <img alt="Local workspace agent" src="https://img.shields.io/badge/Agent-local_workspace-6f42c1?style=flat-square&logo=robotframework&logoColor=white">
  <img alt="Four agent roles plus auto" src="https://img.shields.io/badge/Roles-coder_%7C_reader_%7C_devops_%7C_chat-8A2BE2?style=flat-square">
  <img alt="Five built-in providers" src="https://img.shields.io/badge/LLM_providers-5_builtin-0A7EA4?style=flat-square">
  <img alt="Seven prompts" src="https://img.shields.io/badge/System_prompts-7-5C2D91?style=flat-square">
  <img alt="NDJSON protocol" src="https://img.shields.io/badge/Protocol-NDJSON_v1-000000?style=flat-square&logo=json&logoColor=white">
  <img alt="Human in the loop" src="https://img.shields.io/badge/Safety-human--in--the--loop-2EA44F?style=flat-square">
  <img alt="Test suite" src="https://img.shields.io/badge/tests-1500%2B_collected-brightgreen?style=flat-square&logo=pytest&logoColor=white">
  <img alt="JetBrains plugin" src="https://img.shields.io/badge/JetBrains_Plugin-2025.3-000000?style=flat-square&logo=jetbrains&logoColor=white">
</p>

<p align="center">
  <img alt="OpenAI" src="https://img.shields.io/badge/OpenAI-supported-412991?style=flat-square&logo=openai&logoColor=white">
  <img alt="Anthropic" src="https://img.shields.io/badge/Anthropic-supported-D97757?style=flat-square&logo=anthropic&logoColor=white">
  <img alt="OpenRouter" src="https://img.shields.io/badge/OpenRouter-supported-6467F2?style=flat-square">
  <img alt="OmniRoute" src="https://img.shields.io/badge/OmniRoute-supported-008080?style=flat-square">
  <img alt="FreeFlow LLM" src="https://img.shields.io/badge/FreeFlow_LLM-beta-orange?style=flat-square">
  <img alt="Custom endpoints" src="https://img.shields.io/badge/Custom_endpoints-OpenAI_%7C_Anthropic-555555?style=flat-square">
</p>

Kvist — локальный coding agent под присмотром пользователя. Он работает с одним
workspace за раз: читает и редактирует файлы проекта, запускает команды
разработки и инфраструктуры, проверяет результат и хранит возобновляемые
именованные сессии.

Основной интерфейс — терминал. Также есть Electron GUI и версионируемый NDJSON-
протокол для сторонних frontend-ов.

## Превью

У Kvist две пользовательские точки входа. Ниже каждая показана в полном цикле
одной короткой read-only задачи: `start` — интерфейс готов, `working` — агент
действительно изучает workspace, `end` — ответ завершён, файлы не изменены.

### Терминальный UI в Kitty

При первом запуске выполните `kvist` в каталоге проекта. Выберите провайдера и
введите его API key в замаскированном prompt `API key:`. Значение сохраняется в
`~/.kvist/config.json` и не выводится в терминал.

![Терминальный UI Kvist готов к задаче](preview/terminal-start.png)

![Терминальный агент Kvist работает над задачей](preview/terminal-working.png)

![Терминальная задача Kvist завершена](preview/terminal-end.png)

### Electron GUI

Electron frontend использует тот же runtime и NDJSON-протокол. Выберите или
откройте проект, введите задачу в composer и отвечайте на появившиеся карточки
подтверждений или input-запросов. API key настраивается runtime, а не вставляется
в текст задачи.

![GUI Kvist готов в Hyprland](preview/gui-start.png)

![GUI-агент Kvist работает над задачей в Hyprland](preview/gui-working.png)

![GUI-задача Kvist завершена в Hyprland](preview/gui-end.png)

## Возможности

Запрос направляется в одну из пяти ролей:

- `coder` — меняет исходники и запускает проверки;
- `reader` — исследует и объясняет кодовую базу без изменений;
- `devops` — занимается сервисами, Docker, CI/CD и деплоем;
- `chat` — отвечает на вопросы и обсуждает архитектуру без доступа к файлам;
- `auto` — выбирает роль по запросу (режим по умолчанию).

Каждый запуск использует подтверждения пользователя. Записи файлов и опасные
команды видны и могут потребовать подтверждения. Это не IDE и не полноценная
граница безопасности: проверяйте diff и вывод команд.

## Требования

- Python 3.11 или новее;
- API key и модели хотя бы одного поддерживаемого провайдера;
- TTY для интерактивной настройки и подтверждений.

CLI проверяется на Linux и Windows. macOS остаётся экспериментальной. Плагин
для JetBrains поддерживает Linux и Windows и нацелен на IntelliJ Platform 2025.3.

Встроенные провайдеры: OpenAI, Anthropic, OpenRouter, OmniRoute и FreeFlow LLM
(beta). Дополнительно через `/provider` можно добавить совместимый с OpenAI или
Anthropic custom endpoint.

## Установка из исходников

Из корня репозитория:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Для разработки и тестов:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
```

В Windows используйте `py -m venv .venv` и `.venv\\Scripts\\python.exe`.

## Запуск

Параметр workspace обязателен. Несуществующий каталог Kvist создаёт сам.
Относительные пути разрешаются от каталога, из которого запущен Kvist.

```bash
.venv/bin/python main.py --workspace /path/to/project
```

При первом запуске в терминале выберите провайдера, модели и credentials. Они
сохраняются в `~/.kvist/config.json`; секреты не выводятся в консоль.

Чтобы установить команду `kvist` и при необходимости собрать standalone binary:

```bash
.venv/bin/python install.py
```

Установщик использует `dist/kvist` или `dist/kvist.exe`, а при отсутствии
собирает его PyInstaller-ом. Реальный binary кладётся в пользовательский
каталог данных, launcher — в `~/.local/bin` (Linux) или
`%LOCALAPPDATA%\\Kvist\\bin` (Windows). Добавьте этот каталог в `PATH`, если
установщик предупредит. Для пересборки используйте `--rebuild`.

Установленный launcher использует текущий каталог как workspace:

```bash
cd /path/to/project
kvist
```

Если нужен только binary, соберите его вручную:

```bash
.venv/bin/python -m pip install pyinstaller
.venv/bin/python -m PyInstaller kvist.spec
```

Результат — `dist/kvist` в Linux или `dist/kvist.exe` в Windows.

## Команды терминала

Наберите `/`, чтобы открыть completion. Основные команды:

| Команда | Назначение |
| --- | --- |
| `/help`, `/commands` | Справка и полный список команд |
| `/provider` | Выбрать или настроить провайдера |
| `/change-api` | Заменить ключ текущего провайдера (`/change_api`, `/apikey`) |
| `/models` | Выбрать модели `fast`, `smart`, `planner` |
| `/role` | Выбрать `auto`, `coder`, `reader`, `devops` или `chat` |
| `/session` | Создать, переключить или удалить сессию |
| `/resume` | Продолжить последний checkpoint активной сессии |
| `/new` | Сбросить историю и checkpoint активной сессии |
| `/changes` | Показать изменения агента в workspace |
| `/revert` | Безопасно восстановить записанные изменения |
| `/verbose` | Включить или выключить диагностику |
| `/plan` | Включить или выключить отдельный planning-вызов |
| `/strime` | Переключить streaming custom-провайдера |
| `/bye` | Сохранить сессию и выйти (`/exit`, `/quit`) |

Многострочный ввод: `Alt+Enter` или `Esc+Enter`. Planning включён по умолчанию;
`AGENT_SKIP_PLANNING=1` переопределяет сохранённую настройку для запуска.

Имя сессии содержит 1–64 строчных букв, цифр, `-` или `_` и начинается с буквы
или цифры. У каждой сессии свои история и checkpoint. Checkpoint сохраняется
после каждого шага, поэтому прерванный запуск можно продолжить через `/resume`.

## Headless frontend и скрипты

Встроенный stdio frontend использует NDJSON — один JSON-объект на строку:

```bash
.venv/bin/python main.py --workspace /path/to/project --frontend stdio
```

Первое событие — `hello`. Далее идут план, статусы, streaming, запросы
подтверждений, ошибки и финальный ответ. Полный wire contract, ответы на
`input_request`, операции сессий и resume описаны в [`UI_API.ru.md`](UI_API.ru.md)
и [`UI_API.md`](UI_API.md).

Полезные headless-команды:

```bash
.venv/bin/python main.py --workspace /path/to/project --config-info
.venv/bin/python main.py --workspace /path/to/project --session-op status
.venv/bin/python main.py --workspace /path/to/project --session-op create --session-name review
.venv/bin/python main.py --workspace /path/to/project --journal-op changes
.venv/bin/python main.py --workspace /path/to/project --journal-op revert
```

Для stdio доступны overrides `--provider`, все три model-флага, `--role` и
`--approval-mode ask|deny`. `--resume` продолжает checkpoint без чтения goal из
stdin.

## Electron GUI

GUI запускает Kvist как дочерний процесс и общается только через stdio-протокол;
Python-модули и HTTP/WebSocket не используются.

```bash
cd gui
npm ci
npm start -- --workspace /path/to/project
```

Тесты GUI: `npm test`. Native-пакеты собираются через `npm run dist` отдельно
на Linux (`AppImage`, `deb`) и Windows (`nsis`). Подробности — в
[`gui/README.md`](gui/README.md).

## Конфигурация и границы

Глобальные настройки находятся в `~/.kvist/config.json`. Поддерживаемые
environment variables включают `AGENT_LLM_PROVIDER`, `AGENT_FAST_MODEL`,
`AGENT_SMART_MODEL`, `AGENT_PLANNER_MODEL`, `AGENT_VERBOSE`, `AGENT_MAX_STEPS`,
`AGENT_PERSIST_LOGS`, `AGENT_SKIP_PLANNING`, `AGENT_SANDBOX_MODE` и
`AGENT_SANDBOX_NET`. Для интерактивной настройки используйте `/provider`,
`/models`, `/role`; не коммитьте ключи и локальные данные `.kvist`.

Запись файлов и shell-команды ограничены workspace. Read-only действия могут
читать повторяемые пути из `--allow-read DIR`. Политика команд хранится в
`.kvist/permissions.json` (`allow`, `deny`, `ask`); deny и встроенные блокировки
всегда имеют приоритет. При наличии sandbox сеть по умолчанию запрещена, а без
ответа TTY/frontend подтверждение закрывается безопасно (deny).

## Тесты и lint

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

## Связанные документы

- [`UI_API.ru.md`](UI_API.ru.md) — полный frontend-протокол;
- [`gui/README.md`](gui/README.md) — разработка и packaging Electron GUI;
- [`jetbrains-plugin/README.md`](jetbrains-plugin/README.md) — IntelliJ-плагин;
- [`LICENSE`](LICENSE) — лицензия MIT.
