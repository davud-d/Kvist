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

Основной интерфейс — терминал. Также есть Electron GUI, плагины для JetBrains и
VS Code, а для сторонних frontend-ов — версионируемый NDJSON-протокол.

## Превью

Ниже показан полный цикл одной короткой read-only задачи: `start` — интерфейс
готов, `working` — агент действительно изучает workspace, `end` — ответ
завершён, файлы не изменены.

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

CLI проверяется на Linux и Windows. macOS остаётся экспериментальной. IDE-плагины
поддерживают Linux и Windows: JetBrains-плагин нацелен на IntelliJ Platform
2025.3, а расширение VS Code — на VS Code 1.85+. Оба запускают Kvist для
открытого проекта и умеют писать сообщения коммитов из source-control UI.

Встроенные провайдеры: OpenAI, Anthropic, DeepSeek, OpenRouter, OmniRoute и
FreeFlow LLM (beta). Дополнительно через `/provider` можно добавить совместимый
с OpenAI или Anthropic custom endpoint.

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
python installer/install.py && echo "Install OK" || echo "error install"
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

Если workspace не указан, Kvist автоматически создаёт и использует каталог
`Документы/Kvist Workspace` (на Linux учитывается каталог документов из XDG).
Несуществующий явно указанный каталог Kvist создаёт сам.
Относительные пути разрешаются от каталога, из которого запущен Kvist.

```bash
.venv/bin/python main.py --workspace /path/to/project
# Без --workspace используется Документы/Kvist Workspace
.venv/bin/python main.py
```

При первом запуске в терминале выберите провайдера, модели и credentials. Они
сохраняются в `~/.kvist/config.json`; секреты не выводятся в консоль.

### AnyModel (провайдер по умолчанию)

При пустом конфиге по умолчанию используется [AnyModel](https://anymodel.org).
Если в окружении уже задан `ANYMODEL_API_KEY`, Kvist стартует сразу, без
вопросов. Иначе на первом запуске показывается отдельный экран:

```
Нет ключа AnyModel. Регистрация займёт минуту.

  Enter — открыть регистрацию в браузере (email + пароль)
  Esc   — выбрать другого провайдера

https://anymodel.org/register
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
.venv/bin/python installer/install.py
```

В каталоге `installer/` есть IDE- и desktop-установщики для Linux и Windows:

```bash
.venv/bin/python installer/install_jetbrains.py   # ставит плагин во все найденные JetBrains IDE
.venv/bin/python installer/install_vscode.py      # ставит расширение в VS Code / Insiders / VSCodium
.venv/bin/python installer/install_gui.py         # ставит GUI и ярлык / пункт меню приложений
```

Расширение VS Code собирается в `.vsix` из `vscode-extension/`:

```bash
cd vscode-extension
./build-plugin.sh      # Linux host
# или на Windows: .\build-plugin.ps1 / build-plugin.bat
```

Скрипт собирает binary Kvist для текущей OS через PyInstaller, копирует его в
`vscode-extension/bin/...`, компилирует расширение и кладёт пакет в
`vscode-extension/.build/vsix/`.

Установщик использует `dist/kvist` или `dist/kvist.exe`, а при отсутствии
собирает его PyInstaller-ом. Реальный binary кладётся в пользовательский
каталог данных, launcher — в `~/.local/bin` (Linux) или
`%LOCALAPPDATA%\\Kvist\\bin` (Windows). На Windows установщик сам добавляет этот
каталог в пользовательский `PATH` (после этого откройте новый терминал); на Linux
добавьте его вручную, если установщик предупредит. Для пересборки используйте
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

Результат — `dist/kvist` в Linux или `dist/kvist.exe` в Windows.

## Команды терминала

Наберите `/`, чтобы открыть completion. Основные команды:

| Команда | Назначение |
| --- | --- |
| `/help`, `/commands` | Справка и полный список команд |
| `/provider` | Выбрать или настроить провайдера |
| `/change-api` | Заменить ключ текущего провайдера (`/change_api`, `/apikey`) |
| `/models` | Выбрать модели `fast`, `smart`, `planner` |
| `/settings-api` | Настроить интервал между запросами, таймаут, ретраи, окно контекста, динамическую загрузку моделей, кэш ответов и фолбэк моделей (`/api` — алиас, сохраняется глобально) |
| `/role` | Выбрать `auto`, `coder`, `reader`, `devops` или `chat` |
| `/session` | Создать, переключить или удалить сессию |
| `/resume` | Продолжить последний checkpoint активной сессии |
| `/new` | Начать новую сессию; текущая остаётся доступной в `/session` |
| `/changes` | Показать изменения агента в workspace |
| `/revert` | Безопасно восстановить записанные изменения |
| `/verbose` | Включить или выключить диагностику |
| `/plan` | Включить или выключить отдельный planning-вызов |
| `/tui` | Выбрать терминальный интерфейс (Ink или классический); выбор сохраняется глобально. В Ink-интерфейсе открывает список и переключает сразу; в классическом терминале `/tui on` / `/tui off` — явный выбор |
| `/strime` | Переключить streaming custom-провайдера |
| `/bye` | Сохранить сессию и выйти (`/exit`, `/quit`) |

Многострочный ввод: `Alt+Enter` или `Esc+Enter`. Planning включён по умолчанию;
`AGENT_SKIP_PLANNING=1` переопределяет сохранённую настройку для запуска.

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

Терминальный интерфейс на Ink (`src/`, Node 22+, TypeScript) — отдельный процесс,
который управляет Python-ядром через `--frontend stdio-multi`. Он пишет в обычный
scrollback терминала, поэтому нативная прокрутка и копирование работают. Один раз
соберите его:

```bash
npm install && npm run build   # один самодостаточный файл: dist-ui/kvist-tui.mjs
.venv/bin/python main.py --frontend tui   # или /tui, сохраняется как `frontend`
```

`--frontend terminal` или `--no-banner` отменяют сохранённый выбор на один запуск.
Ink-интерфейсу нужен провайдер, настроенный в классическом терминале; если нет Node,
собранного файла, провайдера или TTY, Kvist запускает классический терминал и
сообщает причину. Инсталлятор (`installer/install_cli.py`) собирает этот файл, если
доступен npm, и упаковывает его в бинарник `kvist`; при запуске бинарнику нужен
Node.js 22+ в `PATH`. Поддерживаются
все слэш-команды. `/help`, `/clear`, `/debug` и `/quit` выполняет сам интерфейс,
остальные — `/role`, `/effort`, `/plan`, `/verbose`, `/style`, `/models`, `/session`,
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

# Сообщение коммита по набору изменений (так его вызывает плагин JetBrains; UI_API.ru.md §8b)
echo '{"files": [{"path": "app.py", "patch": "+print(1)"}]}' \
  | .venv/bin/python main.py --commit-message
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

## Плагин JetBrains

Плагин запускает Kvist во вкладке терминала IDE (Tools > Run Kvist или кнопка
на панели) и пишет сообщения коммитов. Установка:
`.venv/bin/python installer/install_jetbrains.py`, затем перезапуск IDE.

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

Сборка и подробности — в [`jetbrains-plugin/README.md`](jetbrains-plugin/README.md),
протокол вызова — в [`UI_API.ru.md`](UI_API.ru.md) §8b.

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
cd vscode-extension
./build-plugin.sh
```

На Windows используйте `.\build-plugin.ps1` в PowerShell или `build-plugin.bat`
в Command Prompt. Готовый пакет лежит в `vscode-extension/.build/vsix/`; его
можно установить через `code --install-extension <file>.vsix` или Extensions >
Install from VSIX. Подробности — в [`vscode-extension/README.md`](vscode-extension/README.md).

## Конфигурация и границы

Глобальные настройки находятся в `~/.kvist/config.json`. Поддерживаемые
environment variables включают `AGENT_LLM_PROVIDER`, `AGENT_FAST_MODEL`,
`AGENT_SMART_MODEL`, `AGENT_PLANNER_MODEL`, `AGENT_VERBOSE`, `AGENT_MAX_STEPS`,
`AGENT_PERSIST_LOGS`, `AGENT_SKIP_PLANNING`, `AGENT_NATIVE_TOOLS`,
`AGENT_SANDBOX_MODE` и `AGENT_SANDBOX_NET`. Для интерактивной настройки используйте `/provider`,
`/models`, `/role`; не коммитьте ключи и локальные данные `.kvist`.

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
- [`vscode-extension/README.md`](vscode-extension/README.md) — расширение VS Code;
- [`LICENSE`](LICENSE) — лицензия MIT.
