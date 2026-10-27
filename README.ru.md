<p align="center">
  <img src="docs/media/kvist_x_anymodel.webp" width="480" alt="Видео-превью Kvist x AnyModel">
</p>

# Kvist

[English version](README.md)

<p align="center">
  <a href="https://github.com/davud-d/Kvist"><img alt="GitHub repository" src="https://img.shields.io/badge/GitHub-Kvist-181717?style=for-the-badge&logo=github"></a>
  <a href="https://github.com/davud-d/Kvist/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/davud-d/Kvist?style=for-the-badge&logo=github&color=yellow"></a>
  <a href="https://github.com/davud-d/Kvist/issues"><img alt="GitHub issues" src="https://img.shields.io/github/issues/davud-d/Kvist?style=for-the-badge&logo=github"></a>
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/github/license/davud-d/Kvist?style=for-the-badge&color=blue"></a>
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Linux" src="https://img.shields.io/badge/Linux-supported-FCC624?style=flat-square&logo=linux&logoColor=black">
  <img alt="Windows" src="https://img.shields.io/badge/Windows-supported-0078D4?style=flat-square&logo=windows11&logoColor=white">
  <img alt="VS Code 1.106+" src="https://img.shields.io/badge/VS_Code-1.106%2B-007ACC?style=flat-square&logo=visualstudiocode&logoColor=white">
  <img alt="JetBrains 2025.3" src="https://img.shields.io/badge/JetBrains-2025.3-000000?style=flat-square&logo=jetbrains&logoColor=white">
</p>

Kvist — кодинг-помощница, которая работает в вашем терминале внутри одного каталога
проекта. Вы описываете задачу, она читает код, правит файлы, запускает команды и
проверяет результат. Запись файлов и рискованные команды проходят через
подтверждение, а каждое изменение записывается, чтобы его можно было откатить.

## Зачем он, если есть Claude Code и Codex

Claude Code построен вокруг моделей Anthropic, Codex CLI — вокруг моделей OpenAI.
Kvist нужен, когда вы хотите выбирать модель сами.

- **Любой провайдер.** Достаточно одного ключа от OpenAI, Anthropic, DeepSeek,
  OpenRouter, локального шлюза или одного из примерно 40 других
  OpenAI-совместимых сервисов. Можно войти по подписке ChatGPT Plus/Pro или
  добавить свой endpoint. Переключение — `/provider`; у каждого провайдера есть
  быстрая и умная модель, умная берётся для сложных задач.
- **Работает и с дешёвыми моделями.** Если у модели нет tool calling, Kvist
  переходит на простой JSON-протокол, поэтому подойдут недорогие и бесплатные
  модели.
- **Все действия проверяются в одном месте.** Каждая запись файла и каждая
  shell-команда идёт через один executor. На Linux команды выполняются в
  песочнице Bubblewrap без сети по умолчанию, секретные файлы (`.env`, SSH-ключи,
  сертификаты) в ней скрыты, а `.env` Kvist не читает вообще. На Windows (и на
  Linux без Bubblewrap) отключить команде сеть нечем: Kvist говорит об этом при
  запуске и в каждом подтверждении.
- **Откат.** Правки Kvist пишутся в журнал. `/changes` показывает их,
  `/revert` восстанавливает файлы.
- **Задача переживает прерывание.** Сессии именованные и сохраняются. После
  сбоя или остановки `/resume` продолжает с последнего checkpoint.
- **Ваш редактор.** Одна и та же Kvist работает в терминале, в приложении на
  Electron, в браузере (`kvist-web`), в VS Code, IDE JetBrains и Zed.
- **Локально и под MIT.** Данные уходят только в запросах к выбранному вами
  провайдеру.

Kvist в стадии beta (версия 0.1.0b1). Это инструмент под присмотром, а не
граница безопасности: читайте diff и вывод команд, прежде чем соглашаться.

## Установка

Проще всего скрипт из релиза. Он ставит команду `kvist` и Node.js, нужный её
терминальному интерфейсу. Если на машине есть JetBrains или VS Code, он может
поставить и плагин с расширением. Python, права администратора и системные
пакеты не нужны, пока вы не отметили строку со средствами сборки.

```bash
# Linux (macOS работает, но без гарантий)
curl -fsSL https://github.com/davud-d/Kvist/releases/download/latest-plugin-build/install.sh | bash
```

```powershell
# Windows
irm https://github.com/davud-d/Kvist/releases/download/latest-plugin-build/install.ps1 | iex
```

В терминале скрипт открывает экран, где вы отмечаете компоненты, видите файлы и
команды каждого пункта и следите за ходом. Пока не нажата кнопка «Установить»,
ничего не записывается. Загрузки проверяются по SHA-256. Без терминала (CI)
`KVIST_INSTALL_PLAIN=1 bash install.sh` ставит набор по умолчанию.

Ассеты релиза можно скачать и по отдельности: `kvist-linux-x64`,
`kvist-linux-arm64`, `kvist-windows-x64.exe`, `kvist-macos-arm64`, `kvist-macos-x64`,
`kvist-jetbrains.zip`, `kvist-vscode.vsix`. Чтобы поставить из файлов на диске,
запустите `bash install.sh --from <dir>` или `.\install.ps1 -From <dir>`.

### Из исходников

Нужен Python 3.11 или новее.

```bash
git clone https://github.com/davud-d/Kvist
cd Kvist
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/installer/install.py   # по желанию: кладёт `kvist` в PATH
```

В Windows используйте `py -m venv .venv`, `.venv\Scripts\python.exe` и
`scripts\installer\install.py`. Установщик спрашивает, что настроить (CLI,
JetBrains, VS Code, десктопное приложение, Zed). Подробности — в
[`scripts/installer/README.ru.md`](scripts/installer/README.ru.md).

## Первый запуск

```bash
cd /path/to/project
kvist
```

Без `--workspace` Kvist работает в текущем каталоге. В домашнем каталоге или
корне файловой системы она использует `Документы/Kvist Workspace`.

На чистой машине Kvist предлагает провайдера по умолчанию, [AnyModel](https://anymodel.org):
нажмите Enter, зарегистрируйтесь в браузере и вставьте ключ. Esc открывает выбор
другого провайдера, ключ вводится в замаскированном поле `API key:`. Выбор
сохраняется в `~/.kvist/config.json`. Если `ANYMODEL_API_KEY` уже задан, Kvist
запускается без вопросов.

Дальше напишите задачу, например «найди, почему падают тесты в
`tests/test_parser.py`, и исправь».

![Терминальный UI Kvist готов к задаче](docs/media/terminal-start.png)

![Kvist в терминале работает над задачей](docs/media/terminal-working.png)

![Терминальная задача Kvist завершена](docs/media/terminal-end.png)

## Как проходит задача

Для каждого запроса Kvist выбирает роль (`/role` переопределяет выбор):

| Роль | Что делает |
| --- | --- |
| `coder` | Меняет исходники и запускает проверки проекта |
| `reader` | Исследует и объясняет код, ничего не пишет |
| `devops` | Сервисы, Docker, CI/CD, деплой |
| `chat` | Отвечает на вопросы и обсуждает устройство, файлов не трогает |

Если в задаче рискованно само направление, Kvist предлагает начать с плана. Она
читает проект без изменений, предлагает короткий план и правит файлы только
после вашего согласия. Мелкие правки этот шаг пропускают.

На каждом шаге модель присылает короткую пачку действий: прочитать, найти,
записать, выполнить команду, поискать в сети. Они выполняются по порядку и
останавливаются на первой неудаче. Kvist заканчивает, когда модель считает работу
сделанной. Ей велено проверять результат и сообщать, что не удалось доделать
или проверить.

`Esc` ставит запуск на паузу и сохраняет состояние. `Ctrl+C` останавливает его
и убирает за собой. Прерванная задача остаётся в сессии: `/resume` или новое
сообщение продолжат её.

## Интерфейсы

- **Терминал.** Основной. Пять видов одного приложения: Ink, в стиле Claude Code,
  терминал OpenCode (`/tui opencode`, нужен Bun;
  [`frontends/tui/opencode`](frontends/tui/opencode/README.ru.md)), Classic (поверх
  фотографии, kitty или Ghostty) и Classic Legacy на prompt_toolkit;
  переключение — `/tui`.
- **Голос.** Шар, с которым разговариваешь: скажи «Kvist», и она слушает,
  отвечает коротко вслух (`/tui voice`, kitty или Ghostty;
  [`frontends/voice`](frontends/voice/README.ru.md)).
- **Приложение Electron.** `scripts/installer/install_gui.py` или
  [`frontends/gui/electron`](frontends/gui/electron/README.md).
- **Браузер.** `kvist-web` открывает `http://127.0.0.1:9671` и помогает подключить
  модель ([подробности](frontends/web-local/README.md)).
- **VS Code и JetBrains.** Чат-панель, которая запускает Kvist для открытого
  проекта, и генерация сообщений коммитов в окне source control
  ([VS Code](frontends/ide/vscode/README.md),
  [JetBrains](frontends/ide/jetbrains/README.md)).
- **Zed.** Регистрируется как внешний ACP-агент через
  `scripts/installer/install_zed.py`; чего Zed дать не может, написано в
  [`frontends/zed`](frontends/zed/README.ru.md).
- **Свой клиент.** Версионируемый NDJSON-протокол по stdio:
  [`docs/UI_API.ru.md`](docs/UI_API.ru.md).

![Kvist в GUI работает над задачей в Hyprland](docs/media/gui-working.png)

## Команды терминала

Наберите `/`, чтобы увидеть список. Основные:

| Команда | Назначение |
| --- | --- |
| `/help`, `/commands` | Справка и полный список команд |
| `/provider`, `/change-api`, `/models` | Провайдер, его ключ, быстрая и умная модели |
| `/role`, `/effort`, `/style` | Роль, глубина рассуждений (от `false` до `max`), стиль работы |
| `/session`, `/new`, `/resume` | Именованные сессии и продолжение прерванного запуска |
| `/changes`, `/diff`, `/revert` | Что изменила Kvist и откат |
| `/context`, `/status`, `/permissions` | Заполнение контекста, состояние, разрешения команд |
| `/doctor` (или `kvist --doctor`) | Проверяет модули, интерфейсы, провайдера, API-ключи и настройки; спрашивает разрешение, прежде чем отправить модели тестовый запрос, на который она должна ответить «pong» (`--yes` отвечает «да») |
| `/web-search-provider` | Tavily, Brave или SearXNG для веб-поиска |
| `/settings-api` | Интервал запросов, таймауты, ретраи, окно контекста, фолбэки |
| `/tui` | Выбрать терминальный интерфейс |
| `/bye` | Сохранить и выйти (`/exit`, `/quit`) |

Многострочный ввод: `Alt+Enter` или `Esc+Enter`. Изображения: `Ctrl+V` или
`/image PATH` ([подробности](docs/vision.md)).

## Скиллы

Скиллы — устанавливаемые пакеты с инструкциями и, при желании, с
Python-инструментами, которые работают в песочнице ОС только с подтверждёнными
вами правами. Встроенный скилл **browser** даёт Kvist собственный Chromium на
виртуальном экране. Она открывает страницу, которую собрала, кликает по ней,
читает ошибки консоли и сети, снимает скриншоты, а вы смотрите живое превью.
Другие встроенные скиллы запускают тесты, читают PDF, xlsx и docx и
навигируют по коду. Скиллам с инструментами в песочнице нужен Linux.

Встроенный скилл **visualize** работает в Linux, macOS и Windows без дополнительных
зависимостей. Модель подключает его и передаёт описание страницы: большие схемы
архитектуры с масштабированием, графики с таблицами и экспортом CSV либо
интерактивные 3D-сцены из кубов, сфер и конусов со связями. Скилл проверяет
описание, сохраняет самостоятельный HTML-файл и открывает системный браузер
по умолчанию. Файл работает без интернета и после выхода из Kvist. Если
графический браузер недоступен, возвращаются путь к файлу и причина ошибки.
Пример запроса: «Объясни архитектуру проекта большой интерактивной схемой
и открой её в браузере». При первом использовании запрашиваются права скилла.

```bash
kvist --skill-op install --skill-path bundled:browser
kvist --skill-op install-deps --skill browser
kvist --skill-op approve --skill browser
```

Формат пакета, права и песочница описаны в [`docs/SKILLS.ru.md`](docs/SKILLS.ru.md).

## Инструкции проекта

При первом запуске Kvist создаёт в workspace `KVIST.md` (для coder, reader и
devops) и `KVIST-CHAT.md`. Один раз опишите там команды сборки, устройство
проекта и конвенции, и Kvist будет читать их на каждом шаге. Файлы лежат в
проекте, поэтому Kvist считает их содержимое данными, которые не могут менять
его правила безопасности.

## Платформы

| | Поддержка |
| --- | --- |
| Linux | Поддерживается, включая песочницу Bubblewrap |
| Windows | Поддерживается, без Bubblewrap |
| macOS | Проверяется в CI на Apple Silicon, работа всех функций не гарантируется |

## Другая документация

- [`docs/configuration.ru.md`](docs/configuration.ru.md): провайдеры, вход через
  ChatGPT, бюджет контекста, песочница и разрешения, сессии, headless-режим,
  переменные окружения ([EN](docs/configuration.md));
- [`docs/UI_API.ru.md`](docs/UI_API.ru.md): протокол для клиентов ([EN](docs/UI_API.md));
- [`docs/SKILLS.ru.md`](docs/SKILLS.ru.md): скиллы ([EN](docs/SKILLS.md));
- [`docs/vision.md`](docs/vision.md): ввод изображений;
- [`scripts/installer/README.ru.md`](scripts/installer/README.ru.md): установщики ([EN](scripts/installer/README.md)).

## Разработка

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest              # ядро, классический терминал, Zed, скрипты
.venv/bin/python -m ruff check .
(cd frontends/tui/ink && npm install && npm test)
(cd frontends/tui/opencode && bun install && bun test)
(cd frontends/ide/vscode && npm install && npm test)
(cd frontends/gui/electron && npm ci && npm test)
```

```text
kernel/            Python-ядро (python -m kernel)
frontends/         tui/classic, tui/ink, tui/opencode, gui/electron, ide/vscode, ide/jetbrains, zed, web-local, tray
scripts/           установщики и скрипты релиза
docs/              протокол, скиллы, конфигурация, медиа
site/              лендинг kvist.site
```

Лицензия: [MIT](LICENSE).
