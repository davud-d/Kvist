<p align="center">
  <video src="https://github.com/user-attachments/assets/5ed8c7ee-1269-4c85-95cf-3dd66a51b7b0" controls width="480">
    Видео-превью Kvist x AnyModel
  </video>
</p>

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
  <img alt="VS Code extension" src="https://img.shields.io/badge/VS_Code_Extension-0.1.0-007ACC?style=flat-square&logo=visualstudiocode&logoColor=white">
  <img alt="Local workspace agent" src="https://img.shields.io/badge/Agent-local_workspace-6f42c1?style=flat-square&logo=robotframework&logoColor=white">
  <img alt="Four agent roles plus auto" src="https://img.shields.io/badge/Roles-coder_%7C_reader_%7C_devops_%7C_chat-8A2BE2?style=flat-square">
  <img alt="Six built-in providers" src="https://img.shields.io/badge/LLM_providers-6_builtin-0A7EA4?style=flat-square">
  <img alt="Seven prompts" src="https://img.shields.io/badge/System_prompts-7-5C2D91?style=flat-square">
  <img alt="NDJSON protocol" src="https://img.shields.io/badge/Protocol-NDJSON_v1-000000?style=flat-square&logo=json&logoColor=white">
  <img alt="Human in the loop" src="https://img.shields.io/badge/Safety-human--in--the--loop-2EA44F?style=flat-square">
  <img alt="Test suite" src="https://img.shields.io/badge/tests-1500%2B_collected-brightgreen?style=flat-square&logo=pytest&logoColor=white">
  <img alt="JetBrains plugin" src="https://img.shields.io/badge/JetBrains_Plugin-2025.3-000000?style=flat-square&logo=jetbrains&logoColor=white">
</p>

<p align="center">
  <img alt="OpenAI" src="https://img.shields.io/badge/OpenAI-supported-412991?style=flat-square&logo=openai&logoColor=white">
  <img alt="Anthropic" src="https://img.shields.io/badge/Anthropic-supported-D97757?style=flat-square&logo=anthropic&logoColor=white">
  <img alt="DeepSeek" src="https://img.shields.io/badge/DeepSeek-supported-4D6BFE?style=flat-square&logo=deepseek&logoColor=white">
  <img alt="OpenRouter" src="https://img.shields.io/badge/OpenRouter-supported-6467F2?style=flat-square">
  <img alt="OmniRoute" src="https://img.shields.io/badge/OmniRoute-supported-008080?style=flat-square">
  <img alt="FreeFlow LLM" src="https://img.shields.io/badge/FreeFlow_LLM-beta-orange?style=flat-square">
  <img alt="Custom endpoints" src="https://img.shields.io/badge/Custom_endpoints-OpenAI_%7C_Anthropic-555555?style=flat-square">
</p>

Kvist — локальный coding agent под присмотром пользователя. Он работает с одним
workspace за раз: читает и редактирует файлы проекта, запускает команды
разработки и инфраструктуры, проверяет результат и хранит возобновляемые
именованные сессии.

Модель сама выбирает порядок действий, подходящие проверки и момент завершения.
Сборка, анализ и задачи, в которых нужный результат уже достигнут, могут
завершаться без правок исходников. Инструкции требуют проверять результат и
честно сообщать о незавершённой или непроверенной работе. Число изменённых файлов,
повторы инструментов, имена файлов и длина ответа не блокируют завершение.
Ограничения доступа к workspace и секретам, согласования, остановка пользователем
и лимиты ресурсов продолжают действовать.

Основной интерфейс — терминал. Также есть Electron GUI, интеграции с JetBrains,
VS Code и Zed, а для сторонних frontend-ов — версионируемый NDJSON-протокол.

## Превью

Ниже показан полный цикл одной короткой read-only задачи: `start` — интерфейс
готов, `working` — агент действительно изучает workspace, `end` — ответ
завершён, файлы не изменены.

### Терминальный UI в Kitty

При первом запуске выполните `kvist` в каталоге проекта. Выберите провайдера и
введите его API key в замаскированном prompt `API key:`. Значение сохраняется в
`~/.kvist/config.json` и не выводится в терминал.

![Терминальный UI Kvist готов к задаче](docs/media/terminal-start.png)

![Терминальный агент Kvist работает над задачей](docs/media/terminal-working.png)

![Терминальная задача Kvist завершена](docs/media/terminal-end.png)

### Electron GUI

Electron frontend использует тот же runtime и NDJSON-протокол. Выберите или
откройте проект, введите задачу в composer и отвечайте на появившиеся карточки
подтверждений или input-запросов. API key настраивается runtime, а не вставляется
в текст задачи.

![GUI Kvist готов в Hyprland](docs/media/gui-start.png)

![GUI-агент Kvist работает над задачей в Hyprland](docs/media/gui-working.png)

![GUI-задача Kvist завершена в Hyprland](docs/media/gui-end.png)

## Возможности

Запрос направляется в одну из пяти ролей:

- `coder` — меняет исходники и запускает проверки;
- `reader` — исследует и объясняет кодовую базу без изменений;
- `devops` — занимается сервисами, Docker, CI/CD и деплоем;
- `chat` — отвечает на вопросы и обсуждает архитектуру без доступа к файлам;
- `auto` — выбирает роль по запросу (режим по умолчанию).

Если в задаче рискованно само направление, агент предлагает сначала показать
план: он читает проект без изменений, предлагает короткий план и меняет файлы
только после вашего согласия. При отказе спрашивает, что изменить, и
предлагает план снова. Мелкие правки обходятся без предложения.

Каждый запуск использует подтверждения пользователя. Записи файлов и опасные
команды видны и могут потребовать подтверждения. Это не IDE и не полноценная
граница безопасности: проверяйте diff и вывод команд.

## Скиллы

Kvist расширяется устанавливаемыми скиллами: пакетами с инструкциями и, при желании, с
Python-инструментами, которые работают в песочнице ОС только с теми правами, которые вы
подтвердили. Встроенный скилл **browser** даёт агенту собственный Chromium на отдельном
виртуальном экране: он открывает страницу, которую сам собрал, кликает по ней, читает ошибки
консоли и сети и снимает скриншоты, а вы смотрите живое превью (в GUI справа сверху или по
локальной ссылке). Другие встроенные скиллы запускают тесты, читают PDF/xlsx/docx и
навигируют по Python-коду. `Esc` ставит на паузу задачу, помощников и работу скиллов, ничего не
теряя; Ctrl+C или «Остановить всё» отменяет всё и убирает за собой. Пока скиллы работают
только в Linux.

```bash
kvist --skill-op install --skill-path bundled:browser
kvist --skill-op install-deps --skill browser
kvist --skill-op approve --skill browser
```

Формат пакета, права, API `ctx`, гарантии и ограничения песочницы — в
[`docs/SKILLS.ru.md`](docs/SKILLS.ru.md) ([EN](docs/SKILLS.md)).

## Требования

- Python 3.11 или новее;
- API key и модели хотя бы одного поддерживаемого провайдера;
- TTY для интерактивной настройки и подтверждений.

CLI проверяется в CI на Linux, Windows и macOS (Apple Silicon): установка и файловые
и shell-инструменты агента. Официально поддерживаются Linux и Windows; macOS — по
мере возможности, без гарантий, что там работают все функции. Изоляция на уровне ОС
(Bubblewrap) есть только на Linux. IDE-интеграции
поддерживают Linux и Windows (macOS — по мере возможности: бинарники для Apple
Silicon и Intel поставляются, но на Mac не проверены): JetBrains-плагин нацелен на IntelliJ Platform
2025.3, а расширение VS Code — на VS Code 1.85+. Оба запускают Kvist для
открытого проекта и умеют писать сообщения коммитов из source-control UI. Zed
запускает Kvist как внешний ACP-агент — это запись в настройках, а не плагин, —
и получает тот же цикл агента, сессии, подтверждения и `/commit-message`;
чего он получить не может, написано в
[`frontends/zed/README.ru.md`](frontends/zed/README.ru.md).

Встроенные провайдеры: OpenAI, Anthropic, DeepSeek, OpenRouter, OmniRoute и
FreeFlow LLM (beta). Дополнительно через `/provider` можно добавить совместимый
с OpenAI или Anthropic custom endpoint.

## Установка из релиза

Каждый push в `main` пересобирает скользящий
[релиз](https://github.com/davud-d/Kvist/releases/tag/latest-plugin-build).
Один скрипт кладёт команду `kvist`, Node.js, на котором работает её терминальный
интерфейс, плагин JetBrains (в каждую найденную IDE JetBrains) и расширение VS
Code (VS Code, Insiders, VSCodium) куда нужно. Python и собственный Node.js не
требуются, по умолчанию права администратора не используются и системные пакеты
не устанавливаются, кроме того, что оставлено отмеченным в свёрнутой строке
«Сборка из исходников» (клик по ней или → и ←). В ней системные пакеты для
самостоятельной сборки Kvist: Python 3 с venv и pip, заголовки Python и
binutils. Они отмечены с самого начала, строка показывает сколько их; если
снять галочки, sudo не понадобится, а отмеченное ставится вашим менеджером
пакетов через sudo, пароль спросят в терминале.

В терминале скрипт скачивает агент и закреплённую версию Node.js (около 100 МБ,
с проверкой SHA-256) во временную папку и открывает экран установки. На нём вы
отмечаете, что ставить (агент, Node.js, необязательный статический ffmpeg для
видео-превью на Linux x86_64 и macOS, плагины редакторов, строка PATH), видите точные
файлы и команды каждого пункта и следите за ходом; первая настройка продолжается в том же окне.
Node.js и ffmpeg берутся с того из нескольких серверов, который быстрее отвечает из вашей сети, и
принимаются, только если совпадают с SHA-256, записанным в скрипте. Всё на нём работает
только с клавиатуры (стрелки, Пробел, Enter, Esc), только мышью (клик по строке
ставит галочку, кнопки нажимаются) или вперемешку. Пока вы не нажмёте
«Установить», ничего не записывается.

```bash
# Linux и macOS (по мере возможности)
curl -fsSL https://github.com/davud-d/Kvist/releases/download/latest-plugin-build/install.sh | bash

# то же без экрана (CI, нет терминала): ставит набор по умолчанию
KVIST_INSTALL_PLAIN=1 bash install.sh
```

```powershell
# Windows
irm https://github.com/davud-d/Kvist/releases/download/latest-plugin-build/install.ps1 | iex
```

Ассеты можно скачать и по отдельности: `kvist-linux-x64`,
`kvist-windows-x64.exe`, `kvist-macos-arm64`, `kvist-macos-x64`,
`kvist-jetbrains.zip`, `kvist-vscode.vsix`. На macOS скрипт берёт binary под
процессор, на котором запущен; macOS-бинарники пересобираются на каждый десятый
push и могут отставать от `main`. Чтобы
установить из файлов, уже лежащих на диске, запустите `bash install.sh --from <dir>`
или `.\install.ps1 -From <dir>`. Zed регистрируется отдельно:
`scripts/installer/install_zed.py`.

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

## Быстрый старт

Для Unix-подобных систем (Linux, FreeBSD, OpenBSD) в терминале:

```bash
git clone https://github.com/davud-d/Kvist && echo "Clone OK" || echo "error clone"
cd Kvist
python3 -m venv .venv && echo "venv OK" || echo "error venv" # или python -m venv .venv
source .venv/bin/activate && echo "venv activated" || echo "error activate"
uv pip install -r requirements.txt || echo "uv not found" && pip install -r requirements.txt
python scripts/installer/install.py && echo "Install OK" || echo "error install"
```
**Нюанс**: *работает только для bash, если у вас другой shell - адаптируйте*

Для Windows PowerShell:

```powershell
git clone https://github.com/davud-d/Kvist; if ($?) { echo "Clone OK" } else { echo "error clone" }
cd Kvist
python -m venv .venv; if ($?) { echo "venv OK" } else { echo "error venv" }
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force
.venv\Scripts\Activate.ps1; if ($?) { echo "venv activated" } else { echo "error activate" }
uv pip install -r requirements.txt; if (!$?) { pip install -r requirements.txt }
python installer\install.py; if ($?) { echo "Install OK" } else { echo "error install" }
```
**Нюанс**: *работает только для powershell **5.1**, если у вас другой powershell - адаптируйте*

## Запуск

Если workspace не указан, Kvist работает в каталоге, из которого его запустили.
Если это домашний каталог или корень файловой системы, используется
`Документы/Kvist Workspace` (на Linux учитывается каталог документов из XDG).
Несуществующий явно указанный каталог Kvist создаёт сам.
Относительные пути разрешаются от каталога, из которого запущен Kvist.

```bash
.venv/bin/python -m kernel --workspace /path/to/project
# Без --workspace используется текущий каталог (запасной: Документы/Kvist Workspace)
.venv/bin/python -m kernel
```

При первом запуске в терминале выберите провайдера, модели и credentials. Они
сохраняются в `~/.kvist/config.json`; секреты не выводятся в консоль.

### Веб-клиент

```bash
kvist-web        # из исходников: .venv/bin/kvist-web
```

`kvist-web` открывает Kvist в браузере по адресу `http://127.0.0.1:9671` и при первом
запуске проводит через подключение модели. Рабочей папкой становится каталог, из которого
вы запустили команду; другую задаёт `kvist-web --workspace /путь/к/проекту`. Установщик
кладёт команду рядом с `kvist`; подробности в [frontends/web-local/README.md](frontends/web-local/README.md).

### AnyModel (провайдер по умолчанию)

При пустом конфиге по умолчанию используется [AnyModel](https://anymodel.org).
Если в окружении уже задан `ANYMODEL_API_KEY`, Kvist стартует сразу, без
вопросов. Иначе на первом запуске показывается отдельный экран:

```
Нет ключа AnyModel. Регистрация займёт минуту.

  Enter — открыть регистрацию в браузере (email + пароль)
  Esc   — выбрать другого провайдера

https://anymodel.org/app/register
```

`Enter` открывает страницу регистрации в браузере и просит вставить ключ
после регистрации; ключ сразу проверяется через каталог AnyModel (неверный
ключ отклоняется сразу же, с повторным вводом, а не молча ломает запуск
позже). `Esc` возвращает к обычному выбору любого другого встроенного или
стороннего провайдера.

Тот же эндпоинт, по которому проверяется ключ, отдаёт и коэффициент
биллинга на модель — на нём строится подсчёт стоимости шага:

```bash
curl -H "Authorization: Bearer $ANYMODEL_API_KEY" https://anymodel.org/v1/models
```

Чтобы сменить провайдера, задайте `AGENT_LLM_PROVIDER` или выполните
`/provider` — дефолт применяется только к конфигу, в котором провайдер ещё
не сохранён.

Чтобы установить команду `kvist` и при необходимости собрать standalone binary:

```bash
.venv/bin/python scripts/installer/install.py
```

В каталоге `scripts/installer/` есть IDE- и desktop-установщики для Linux, Windows и (по мере возможности) macOS:

```bash
.venv/bin/python scripts/installer/install_jetbrains.py   # ставит плагин во все найденные JetBrains IDE
.venv/bin/python scripts/installer/install_vscode.py      # ставит расширение в VS Code / Insiders / VSCodium
.venv/bin/python scripts/installer/install_gui.py         # ставит GUI и ярлык / пункт меню приложений
.venv/bin/python scripts/installer/install_zed.py         # регистрирует kvist-core как ACP-агент Zed
```

### Zed

Zed связывается с кодящими агентами через
[Agent Client Protocol](https://agentclientprotocol.com/), поэтому Kvist не
поставляет туда расширение: агенты-расширения в Zed объявлены устаревшими, и
`extension.toml` в этом репозитории нет. Сначала запустите установщик CLI, затем:

```bash
.venv/bin/python scripts/installer/install_cli.py         # кладёт kvist-core на диск
.venv/bin/python scripts/installer/install_zed.py         # пишет одну запись agent_servers
```

Эта запись указывает Zed на `kvist-core --acp`. Zed перечитывает настройки сам,
поэтому перезапуск не нужен. Вручную всё сводится к:

```bash
/path/to/kvist-core --acp
```

Какой паритет есть у этого с интеграциями для VS Code и JetBrains — и перечень
того, для чего в Zed нет публичного API (например, строчное дополнение), —
в [`frontends/zed/README.ru.md`](frontends/zed/README.ru.md)
([EN](frontends/zed/README.md)).

Расширение VS Code собирается в `.vsix` из `frontends/ide/vscode/`:

```bash
cd frontends/ide/vscode
./build-plugin.sh      # Linux или macOS host
# или на Windows: .\build-plugin.ps1 / build-plugin.bat
```

Скрипт собирает binary Kvist для текущей OS через PyInstaller, копирует его в
`frontends/ide/vscode/bin/...`, компилирует расширение и кладёт пакет в
`frontends/ide/vscode/.build/vsix/`.

Установщик использует `dist/kvist` или `dist/kvist.exe`, а при отсутствии
собирает его PyInstaller-ом. Реальный binary кладётся в пользовательский
каталог данных, launcher — в `~/.local/bin` (Linux и macOS) или
`%LOCALAPPDATA%\\Kvist\\bin` (Windows). На Windows установщик сам добавляет этот
каталог в пользовательский `PATH` (после этого откройте новый терминал); на Linux и macOS
добавьте его вручную, если установщик предупредит (он выводит команду для вашей оболочки). Для пересборки используйте
`--rebuild`.

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

Результат — `dist/kvist` в Linux и macOS или `dist/kvist.exe` в Windows.

## Команды терминала

Наберите `/`, чтобы открыть completion. Основные команды:

| Команда | Назначение |
| --- | --- |
| `/help`, `/commands` | Справка и полный список команд |
| `/provider` | Выбрать или настроить провайдера |
| `/change-api` | Заменить ключ текущего провайдера (`/change_api`, `/apikey`) |
| `/models` | Выбрать модели `fast` и `smart` |
| `/web-search-provider` | Выбрать провайдера веб-поиска (Tavily, Brave, SearXNG, auto) и ввести его API-ключ; хранится в `~/.kvist/config.json` |
| `/settings-api` | Настроить интервал между запросами, таймаут, ретраи, окно контекста, динамическую загрузку моделей, кэш ответов и фолбэк моделей (`/api` — алиас, сохраняется глобально) |
| `/role` | Выбрать `auto`, `coder`, `reader`, `devops` или `chat` |
| `/session` | Создать, переключить или удалить сессию |
| `/resume` | Продолжить последний checkpoint активной сессии |
| `/new` | Начать новую сессию; текущая остаётся доступной в `/session` |
| `/changes` | Показать изменения агента в workspace |
| `/revert` | Безопасно восстановить записанные изменения |
| `/verbose` | Включить или выключить диагностику |
| `/tui` | Выбрать терминальный интерфейс (Ink, в стиле Claude или классический); выбор сохраняется глобально. В Node-интерфейсах открывает список и переключает сразу; в классическом терминале `/tui on` / `/tui claude` / `/tui off` — явный выбор |
| `/strime` | Переключить streaming custom-провайдера |
| `/bye` | Сохранить сессию и выйти (`/exit`, `/quit`) |

Многострочный ввод: `Alt+Enter` или `Esc+Enter`.

На первой задаче после запуска Kvist отправляет один крошечный запрос и проверяет,
умеют ли текущие провайдер и модель вызывать инструменты нативно. Если умеют,
прогон идёт одним растущим разговором из вызовов инструментов и их результатов:
модель помнит его между шагами, а провайдер может его кэшировать. Если нет,
прогон идёт на JSON-протоколе Kvist, как раньше. Если провайдер отказывается от
инструментов посреди прогона, этот прогон переходит на JSON-протокол.
`AGENT_NATIVE_TOOLS=0` оставляет все прогоны на JSON-протоколе.

Имя сессии содержит 1–64 строчных букв, цифр, `-` или `_` и начинается с буквы
или цифры. У каждой сессии свои история и checkpoint. Checkpoint сохраняется
после каждого шага, который что-то меняет, и после каждой пачки чтений, поэтому
прерванный запуск можно продолжить через `/resume`.

## Headless frontend и скрипты

Терминальный интерфейс на Ink (`frontends/tui/ink/`, Node 22+, TypeScript) — отдельный процесс,
который управляет Python-ядром через `--frontend stdio-multi`. Он пишет в обычный
scrollback терминала, поэтому нативная прокрутка и копирование работают. Один раз
соберите его:

```bash
(cd frontends/tui/ink && npm install && npm run build)   # один самодостаточный файл: frontends/tui/ink/dist-ui/kvist-tui.mjs
.venv/bin/python -m kernel --frontend tui   # или /tui, сохраняется как `frontend`
.venv/bin/python -m kernel --frontend claude   # то же приложение в облике Claude Code
```

Интерфейс в стиле Claude — второй скин того же приложения, а не отдельный процесс:
вызовы `●` с результатами `⎿`, строка со спиннером `✻` и временем, поле ввода между
двумя линиями и логотип Kvist в приветственном блоке. Как и полноэкранный режим
Claude Code, он работает на альтернативном экране: приветственный блок — самый верх
окна, поле ввода остаётся внизу, а список команд открывается над ним. Прокрутки
терминала там нет, поэтому приложение прокручивает ленту само (`PgUp`/`PgDn`,
`Ctrl+Home`/`Ctrl+End`, колесо мыши) и забирает мышь себе: протяжка выделяет текст,
двойной клик — слово, тройной — строку; выделенное копируется при отпускании кнопки.
С зажатым `Shift` работает обычное выделение самого терминала.

Белка в приветственном блоке рисуется настоящими картинками там, где терминал это умеет: по
графическому протоколу kitty (kitty, Ghostty, Konsole) или через Sixel (Windows Terminal 1.22+,
WezTerm, mintty, foot, iTerm2 и другие; Kvist спрашивает терминал при запуске). В остальных случаях
это блочные символы: октанты (вдвое чётче) в Windows Terminal, у которого шрифт Cascadia Code их
содержит, и квадранты во всех остальных терминалах. `KVIST_GLYPHS=octants` или `quadrants` заменяет
это предположение, например для Windows Terminal с другим шрифтом.

`--frontend terminal` или `--no-banner` отменяют сохранённый выбор на один запуск.
Ink-интерфейсу нужен провайдер, настроенный в классическом терминале; если нет Node,
собранного файла, провайдера или TTY, Kvist запускает классический терминал и
сообщает причину. Инсталлятор (`scripts/installer/install_cli.py`) собирает этот файл, если
доступен npm, и упаковывает его в бинарник `kvist`; при запуске бинарнику нужен
Node.js 22+ в `PATH`. Поддерживаются
все слэш-команды. `/help`, `/clear`, `/debug` и `/quit` выполняет сам интерфейс,
остальные — `/role`, `/effort`, `/verbose`, `/style`, `/models`, `/session`,
`/new`, `/changes`, `/provider`, `/change-api`, `/settings-api`, `/strime`,
`/del-provider`, `/full-reset`, `/revert` и `/resume` — выполняет ядро, а их диалоги
(списки с поиском, скрытый ввод ключей, подтверждения) рисует интерфейс. Правки
файлов показываются диффом (в две колонки от 100 колонок), команды shell — вместе с
выводом. Опасная команда или перезапись файла открывают экран разрешения: выбор
стрелками или цифрами 1/2/3, одиночный клик переставляет курсор, двойной
подтверждает, Esc отклоняет. Пункт «больше не спрашивать» сохраняет правило в
`<workspace>/.kvist/permissions.json` и не предлагается для заведомо опасных команд.
Клавиши: Enter — отправить, двойной Esc или Ctrl+C — остановить запуск, Ctrl+D — выйти.

Встроенный stdio frontend использует NDJSON — один JSON-объект на строку:

```bash
.venv/bin/python -m kernel --workspace /path/to/project --frontend stdio
```

Первое событие — `hello`. Далее идут план, статусы, streaming, запросы
подтверждений, ошибки и финальный ответ. Полный wire contract, ответы на
`input_request`, операции сессий и resume описаны в [`docs/UI_API.ru.md`](docs/UI_API.ru.md)
и [`docs/UI_API.md`](docs/UI_API.md).

Полезные headless-команды:

```bash
.venv/bin/python -m kernel --workspace /path/to/project --config-info
.venv/bin/python -m kernel --workspace /path/to/project --session-op status
.venv/bin/python -m kernel --workspace /path/to/project --session-op create --session-name review
.venv/bin/python -m kernel --workspace /path/to/project --journal-op changes
.venv/bin/python -m kernel --workspace /path/to/project --journal-op revert

# Сообщение коммита по набору изменений (так его вызывает плагин JetBrains; UI_API.ru.md §8b)
echo '{"files": [{"path": "app.py", "patch": "+print(1)"}]}' \
  | .venv/bin/python -m kernel --commit-message
```

Для stdio доступны overrides `--provider`, оба model-флага, `--role` и
`--approval-mode ask|deny`. `--resume` продолжает checkpoint без чтения goal из
stdin.

`--resume SESSION_ID` продолжает checkpoint конкретной сессии в Classic, Ink
и stdio. Используйте id (имя файла без `.json`), а не отображаемое название.
Если сессия или checkpoint отсутствует, команда завершится с ошибкой.

## Electron GUI

GUI запускает Kvist как дочерний процесс и общается только через stdio-протокол;
Python-модули и HTTP/WebSocket не используются.

```bash
cd frontends/gui/electron
npm ci
npm start -- --workspace /path/to/project
```

Тесты GUI: `npm test`. Native-пакеты собираются через `npm run dist` отдельно
на Linux (`AppImage`, `deb`), Windows (`nsis`) и, по мере возможности, macOS (`dmg`, `zip`). Подробности — в
[`frontends/gui/electron/README.md`](frontends/gui/electron/README.md).

В `frontends/gui/electron/package.json` задано `"workspaces": []`, чтобы electron-builder считал
каталог `frontends/gui/electron/` собственным корнем workspace и не поднимался выше к постороннему
монорепо, ломая сборку.

## Плагин JetBrains

Плагин запускает Kvist во вкладке терминала IDE (Tools > Run Kvist или кнопка
на панели) и пишет сообщения коммитов. Установка:
`.venv/bin/python scripts/installer/install_jetbrains.py`, затем перезапуск IDE.

### Сообщения коммитов

В окне коммита (и в немодальном окне Commit) над полем сообщения появляется
кнопка Kvist. Она отправляет в `kvist --commit-message` отмеченные в окне
изменения, включая частично выбранные строки, и 15 последних заголовков
коммитов проекта, а ответ вставляет в поле.

- Сообщение всегда на английском. Стиль повторяет историю проекта:
  Conventional Commits, если проект их использует, иначе обычное предложение
  в повелительном наклонении. Тело добавляется, только когда diff сам себя не
  объясняет.
- Пока идёт генерация, кнопка крутится. При наведении она превращается в
  красный квадрат «Стоп», и нажатие отменяет генерацию, а не запускает ещё
  одну.
- Используются провайдер и **fast**-модель из `~/.kvist/config.json`. Сначала
  один раз запустите Kvist в терминале: кнопка никогда не спрашивает ключ.
- Секретные файлы (`.env`, ключи, сертификаты, ...) и `.kvist/` отбрасываются
  до того, как модель что-либо увидит.
- Нужен встроенный Git-плагин IDE; без него доступен только запуск Kvist.
- Плагин требует IntelliJ Platform 2025.3 или новее.

Сборка и подробности — в [`frontends/ide/jetbrains/README.md`](frontends/ide/jetbrains/README.md),
протокол вызова — в [`docs/UI_API.ru.md`](docs/UI_API.ru.md) §8b.

## Расширение VS Code

Расширение запускает Kvist во встроенном терминале VS Code для открытого
workspace и добавляет команду `Kvist: Generate Commit Message with Kvist` в
Command Palette и заголовок Source Control. Генерация сообщения коммита
передаёт текущий Git diff и последние заголовки коммитов в
`kvist --commit-message`; используются провайдер и **fast**-модель из
`~/.kvist/config.json`, поэтому сначала один раз настройте Kvist в терминале.

Локальная сборка VSIX использует тот же подход с bundled binary, что и
JetBrains-плагин:

```bash
cd frontends/ide/vscode
./build-plugin.sh
```

Скрипт работает на Linux и macOS. На Windows используйте `.\build-plugin.ps1` в
PowerShell или `build-plugin.bat` в Command Prompt. Готовый пакет лежит в `frontends/ide/vscode/.build/vsix/`; его
можно установить через `code --install-extension <file>.vsix` или Extensions >
Install from VSIX. Подробности — в [`frontends/ide/vscode/README.md`](frontends/ide/vscode/README.md).

## Zed

Zed запускает Kvist как внешнего агента по Agent Client Protocol: процесс агента
— это `kvist-core --acp`, говорящий JSON-RPC по stdin/stdout. Расширения Zed
здесь нет и `extension.toml` тоже: агенты-расширения в Zed объявлены
устаревшими, а публичный способ добавить агента — запись в настройках.

```bash
.venv/bin/python scripts/installer/install_cli.py
.venv/bin/python scripts/installer/install_zed.py
```

`install_zed.py` пишет одну запись `agent_servers` в JSONC-файл настроек Zed,
сохраняя все ваши комментарии, и отказывается регистрировать неустановленный
бинарник. `--uninstall` удаляет ровно эту запись. Агент получает тот же цикл
действий, песочницу, сессии, подтверждения и `/commit-message`, что и терминал;
роль и глубина рассуждений передаются как Session Config Options
ACP, а всё остальное (провайдер, модели, стиль, режим песочницы) остаётся в
`~/.kvist/config.json` и переменных `AGENT_*`.

Чего для Zed нет публичного API — строчного дополнения как ghost text, боковой
панели Kvist, кнопки коммита в source-control UI — перечислено вместе с
официальными источниками в [`frontends/zed/README.ru.md`](frontends/zed/README.ru.md)
([EN](frontends/zed/README.md)).

## Конфигурация и границы

Глобальные настройки находятся в `~/.kvist/config.json`. Поддерживаемые
environment variables включают `AGENT_LLM_PROVIDER`, `AGENT_FAST_MODEL`,
`AGENT_SMART_MODEL`, `AGENT_VERBOSE`, `AGENT_MAX_STEPS`,
`AGENT_PERSIST_LOGS`, `AGENT_NATIVE_TOOLS`,
`AGENT_SANDBOX_MODE` и `AGENT_SANDBOX_NET`. Для интерактивной настройки используйте `/provider`,
`/models`, `/role`; не коммитьте ключи и локальные данные `.kvist`.

Для команд агента есть два независимых разрешения на один запуск:
`sandbox: "host"` — доступ к системным инструментам и файлам вне песочницы;
`network: true` — доступ к сети. Для каждого запроса нужно обоснование
`justification`. Если нужны оба разрешения, пользователь получает два запроса.
Правила allow их не отменяют; запомнить разрешения нельзя. Команда, каталог и
причина показываются в classic/Ink TUI, Electron GUI, веб-интерфейсе, VS Code,
JetBrains и Zed. Новых элементов управления в трее нет.

По умолчанию `run_shell` использует настроенную файловую песочницу без сети,
даже при `AGENT_SANDBOX_NET=allow` (переменная продолжает действовать для
внутренних вызовов терминала). В Linux сеть изолируется через Bubblewrap,
в том числе при доступе к файлам хоста; в macOS для запуска на хосте без сети
используется `/usr/bin/sandbox-exec`. В Windows без отдельного системного
компонента такого режима нет: Kvist отказывает в запуске. То же происходит
при отсутствии необходимого механизма в Linux/macOS. Запуск на хосте с сетью
требует обоих подтверждений. Системные компоненты автоматически не устанавливаются.
На хосте сохраняются отдельный HOME, очищенное окружение и проверки команд и
путей; административные права не выдаются, файловой изоляции больше нет.

В корне workspace лежат два файла инструкций: `KVIST.md` — для ролей рабочего
цикла (coder, reader, devops) и `KVIST-CHAT.md` — для чата. Kvist создаёт их из
шаблона при первом запуске и добавляет их содержимое к системному промпту роли,
поэтому команды сборки, устройство проекта и его конвенции описываются один раз,
а не повторяются в каждой задаче; правки применяются со следующего шага. Если в
workspace уже есть `.gitignore`, оба имени однократно дописываются в него под
комментарием — без `.gitignore` ничего не записывается. Файлы лежат в workspace,
поэтому их содержимое считается недоверенным: оно попадает в промпт как
ограниченный блок данных, окружённый инструкциями, которые сводят его роль к
командам, устройству и конвенциям и запрещают менять роль агента, правила
безопасности и протокол действий. Это удорожает враждебный файл инструкций и
делает попытку заметной, но не является гарантией. Настоящей границей остаются
механизмы, не читающие промпт: валидация команд, маскирование секретов, границы
workspace и запросы подтверждения.

Запись файлов и shell-команды ограничены workspace. Read-only действия могут
читать повторяемые пути из `--allow-read DIR`. Политика команд хранится в
`.kvist/permissions.json` (`allow`, `deny`, `ask`); deny и встроенные блокировки
всегда имеют приоритет. При наличии sandbox сеть по умолчанию запрещена, а без
ответа TTY/frontend подтверждение закрывается безопасно (deny). `fetch_url`
открывает известные сайты документации без вопросов; любой другой сайт
спрашивает: да, всегда да для этого сайта (сохраняется как `web_domains` в
`permissions.json`) или нет.

## Структура репозитория

```text
kernel/            Python-ядро (python -m kernel): цикл агента, исполнитель, провайдеры, сессии, промпты
frontends/
  tui/classic/     классический терминал (Python, prompt_toolkit)
  tui/ink/         терминальный интерфейс на Ink (TypeScript), скины kvist и claude-code
  gui/electron/    десктопное приложение (Electron)
  ide/vscode/      расширение VS Code и чат-панель, общая с JetBrains
  ide/jetbrains/   плагин IntelliJ
  zed/             агент для Zed по ACP
scripts/           установщики, run_task.py, package_script.py
branding/          иконки и логотипы
docs/              протокол фронтендов (UI_API) и медиа для README
site/              лендинг kvist.site
```

Тесты каждого компонента лежат рядом с ним (`kernel/tests/`, `frontends/*/tests/`, …).

## Тесты и lint

```bash
.venv/bin/python -m pytest              # ядро, классический терминал, Zed, скрипты
.venv/bin/python -m ruff check .
(cd frontends/tui/ink && npm test)      # терминальный интерфейс на Ink
(cd frontends/ide/vscode && npm test)   # расширение VS Code и чат-панель
(cd frontends/gui/electron && npm test) # Electron GUI
```

## Связанные документы

- [`docs/UI_API.ru.md`](docs/UI_API.ru.md) — полный frontend-протокол;
- [`docs/SKILLS.ru.md`](docs/SKILLS.ru.md) — скиллы: формат пакета, права, API `ctx`, песочница ([EN](docs/SKILLS.md));
- [`frontends/gui/electron/README.md`](frontends/gui/electron/README.md) — разработка и packaging Electron GUI;
- [`frontends/ide/jetbrains/README.md`](frontends/ide/jetbrains/README.md) — IntelliJ-плагин;
- [`frontends/ide/vscode/README.md`](frontends/ide/vscode/README.md) — расширение VS Code;
- [`frontends/zed/README.ru.md`](frontends/zed/README.ru.md) — внешний агент Zed ([EN](frontends/zed/README.md));
- [`scripts/installer/README.ru.md`](scripts/installer/README.ru.md) — установщики ([EN](scripts/installer/README.md));
- [`LICENSE`](LICENSE) — лицензия MIT.
