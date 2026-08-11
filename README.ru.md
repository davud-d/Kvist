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
  <img alt="Local workspace agent" src="https://img.shields.io/badge/Agent-local_workspace-6f42c1?style=flat-square&logo=robotframework&logoColor=white">
  <img alt="Three agent roles" src="https://img.shields.io/badge/Roles-coder_%7C_devops_%7C_chat-8A2BE2?style=flat-square">
  <img alt="Six providers" src="https://img.shields.io/badge/LLM_providers-6-0A7EA4?style=flat-square">
  <img alt="Six prompts" src="https://img.shields.io/badge/System_prompts-6-5C2D91?style=flat-square">
  <img alt="Strict JSON protocol" src="https://img.shields.io/badge/Protocol-strict_JSON-000000?style=flat-square&logo=json&logoColor=white">
  <img alt="Human in the loop" src="https://img.shields.io/badge/Safety-human--in--the--loop-2EA44F?style=flat-square">
  <img alt="Test suite" src="https://img.shields.io/badge/tests-1000%2B_offline-brightgreen?style=flat-square&logo=pytest&logoColor=white">
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

## Превью

### Первый запуск

Ввод API key намеренно скрыт.

![Первый запуск Kvist со скрытым вводом API key](preview/start.png)

### Работа агента

![Kvist работает в терминале](preview/working.png)

### Финальный отчёт

![Финальный отчёт Kvist в терминале](preview/end.png)

Kvist — локальный coding agent для терминала, работающий с одним workspace за
раз. Он умеет изучать и редактировать файлы проекта, запускать команды
разработки и инфраструктуры, проверять изменения, искать актуальную техническую
документацию и вести отдельные именованные сессии проекта.

Каждый запрос Kvist направляет в одну из трёх ролей:

- `coder` работает с кодом и файлами проекта через строгий JSON-протокол
  действий;
- `devops` занимается деплоем, сервисами, Docker, CI/CD и другими системными
  задачами с role-aware подтверждением команд;
- `chat` отвечает на вопросы и обсуждает архитектуру, не трогая файлы и не
  запуская команды.

Это инструмент для работы под присмотром, а не IDE и не полная граница
безопасности. Перед тем как доверять результату, проверяйте diff и вывод команд.

## Требования

- Python 3.11+
- API key или endpoint хотя бы одного поддерживаемого LLM-провайдера
- Терминал с TTY для интерактивного выбора провайдера, моделей, роли, сессии и
  подтверждения опасных команд

CLI поддерживает Windows и Linux. macOS учитывается runtime-проверками, но
остаётся непроверенной/экспериментальной платформой. JetBrains-плагин
поддерживает только Windows и Linux.

У JetBrains-плагина есть дополнительные требования к сборке; они описаны в
[`jetbrains-plugin/README.md`](jetbrains-plugin/README.md).

## Установка

Из корня репозитория:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Для разработки и тестов установите объявленные developer-зависимости:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
```

Если в минимальном Linux-образе `venv` сообщает об отсутствии `ensurepip`,
можно установить pip только внутрь проектного окружения официальным fallback
PyPA:

```bash
python3 -m venv --without-pip .venv
curl --proto '=https' --tlsv1.2 --fail --silent --show-error \
  https://bootstrap.pypa.io/get-pip.py | .venv/bin/python -
.venv/bin/python -m pip install -r requirements-dev.txt
```

PyInstaller нужен только для сборки отдельного бинарника:

```bash
.venv/bin/python -m pip install pyinstaller
.venv/bin/python -m PyInstaller kvist.spec
```

Готовый файл появится как `dist/kvist` в Linux или `dist/kvist.exe` в Windows.

## Запуск

Из исходников:

```bash
.venv/bin/python main.py --workspace "<project_path>"
```

Через собранный бинарник:

```bash
kvist --workspace "<project_path>"
```

`--workspace` — обязательный аргумент CLI. Kvist разворачивает
путь, при необходимости создаёт каталог и затем ограничивает им файловые
действия. Абсолютный путь обычно понятнее; относительный считается от каталога,
из которого запущен Kvist.

Для версионированного NDJSON-протокола используйте `--frontend stdio`. Перед
запуском headless-фронтенда один раз настройте провайдер и ключи в интерактивном
терминале.

В Windows создайте окружение командой `py -m venv .venv` и используйте
`.venv\Scripts\python.exe` вместо `.venv/bin/python`.

Примеры запросов:

```text
> Найди, где загружаются настройки, и объясни поток данных
```

```text
> Добавь проверку пустого workspace path и запусти подходящие тесты
```

Для многострочного ввода используйте `Alt+Enter` или `Esc+Enter`. Автодополнение
slash-команд появляется, как только ввод начинается с `/`.

### Electron GUI

```bash
cd gui
npm ci
npm start -- --workspace "<project_path>"
```

GUI запускает Kvist отдельным процессом с `--frontend stdio`. Он не импортирует
Python-модули и не использует HTTP, REST или WebSocket. Инструкции по упаковке
находятся в `gui/README.md`. В строке ввода доступны настройки провайдера и трёх
моделей, роли агента и fail-closed режимов подтверждения `ask`/`deny` для каждого
запуска. Каждый прогон выполняется в активной именованной сессии workspace;
событие `hello` сообщает её имя и наличие возобновляемого checkpoint. Фронтенды
управляют сессиями через `kvist --session-op ...` и возобновляют checkpoint
через `kvist --frontend stdio --resume` (см. `UI_API.ru.md`); собственного
переключателя сессий в GUI пока нет.

## Интерактивные команды

| Команда | Что делает |
| --- | --- |
| `/help` | Показывает информацию о Kvist и поддержку платформ. Также принимаются `/about` и `/info`. |
| `/new` | Очищает историю и checkpoint активной сессии, сохраняя её текущее имя. |
| `/provider` | Переключает провайдера или создаёт пользовательского провайдера. |
| `/models` | Выбирает модели `fast`, `smart` и `planner` для активного провайдера. |
| `/role` | Выбирает `auto`, `coder`, `devops` или `chat`. Выбор сохраняется глобально. |
| `/session` | Создаёт, переключает или удаляет именованные сессии workspace. Активную сессию удалить нельзя. |
| `/resume` | Продолжает checkpoint, сохранённый в активной сессии. |
| `/changes` | Показывает diff файлов, изменённых агентом в этом workspace (все сессии) с момента последней очистки журнала изменений. |
| `/revert` | Восстанавливает изменённые агентом файлы к записанному состоянию до изменений. Файлы, отредактированные позже вами (или shell-командами), сохраняются и помечаются как пропущенные. |
| `/verbose <true\|false>` | Включает или выключает подробный вывод без перезапуска Kvist. |
| `/strime <true\|false>` | Включает или выключает streaming активного custom-провайдера. |
| `/bye` | Сохраняет сессию и завершает работу. `/exit` и `/quit` — алиасы. |

### Режим verbose

После ввода `/verbose` Kvist показывает серую inline-подсказку в угловых
скобках. Если подробный режим выключен, предлагается `<true>`; если включён —
`<false>`. Нажмите `Tab`, чтобы вставить предложенное значение:

```text
/verbose <true>
```

Значение также можно ввести вручную: `/verbose <true>` или
`/verbose <false>`. Команда `/verbose` без аргумента инвертирует текущее
состояние. Изменение сразу применяется к текущему runtime; после перезапуска
начальное значение снова читается из `AGENT_VERBOSE`.

### Streaming custom-провайдера

Для custom-провайдеров streaming по умолчанию выключен. Когда активен
custom-провайдер, после ввода `/strime` показывается `<true>`, если streaming
выключен, или `<false>`, если он включён. Нажмите `Tab`, чтобы вставить
предложенное значение:

```text
/strime <true>
```

Значение также можно ввести вручную: `/strime <true>` или `/strime <false>`.
Команда `/strime` без аргумента инвертирует текущее состояние. Выбор сохраняется
в конфигурации активного custom-провайдера и сразу применяется к текущему
агенту. Для встроенных провайдеров команда отклоняется.

Имя сессии содержит 1-64 строчные латинские буквы, цифры, дефисы или
подчёркивания и начинается с буквы или цифры. При загрузке Kvist показывает
несколько последних задач и держит в контексте до десяти завершённых задач.

### Контракт жизненного цикла сессий

- Каждая именованная сессия владеет собственной историей задач, историей чата
  и возобновляемым checkpoint выполнения; между сессиями ничего не смешивается.
- Checkpoint сохраняется после каждого шага, поэтому прерванная задача
  (Ctrl+C, сбой, `kill`) продолжается командой `/resume` с последнего
  завершённого шага.
- `/resume` работает только с активной сессией; завершение задачи очищает
  checkpoint только её сессии.
- Переключение сессий сначала сохраняет исходящую сессию и завершается ошибкой,
  не трогая её, если цель отсутствует или повреждена. Переключение никогда не
  создаёт сессию и никогда не переписывает файлы workspace — состояние диалога
  и откат workspace — разные механизмы.
- `/new` сбрасывает активную сессию на месте (история, чат, checkpoint); другие
  именованные сессии и журнал изменений workspace не затрагиваются.
- Активную сессию удалить нельзя; удаление неактивной сессии убирает и её файл,
  и резервную копию.
- Перезапуск восстанавливает последнюю активную сессию. Если маркер активной
  сессии повреждён, Kvist выбирает последнюю валидную сессию и сообщает об
  этом; повреждённый файл сессии даёт явную ошибку, а не «пустую сессию».

### Отслеживание изменений workspace

Журнал изменений (`.kvist/change_journal.json`) общий для workspace: все сессии
работают с одним рабочим деревом, поэтому `/changes` и `/revert` охватывают
правки агента из всех сессий, с указанием сессии по каждому файлу в отчёте.
Журнал записывает исходное содержимое файла *до* его изменения, поэтому сбой
между этими шагами не может потерять данные для отката. `/revert`
перезаписывает только содержимое, чей digest совпадает с последней записью
самого агента — файлы, отредактированные позже, пропускаются, а их базовые
версии остаются в журнале. `/new` не сбрасывает журнал изменений; записи
удаляет только успешный `/revert`.

## Как обрабатывается запрос

В режиме роли `auto` явные запросы сначала обрабатываются детерминированными
правилами. Неоднозначные запросы классифицирует `fast`-модель через
`prompts/router_prompt.txt`; если вызов модели не удался, используется
keyword-router.
Ручной выбор через `/role` отключает автоматическую маршрутизацию.

Для задач `coder` и `devops` Kvist выполняет такую цепочку:

1. Через planner-промпт и настроенную `fast`-модель строит короткий план,
   критерии успеха, риски и классификацию simple/complex. Если planning не
   сработал, используется локальный fallback-план.
2. Выбирает action-модель `fast` для простой работы или `smart` для сложной
   работы и восстановления после ошибок.
3. Запускает пошаговый цикл с промптом роли, строгим разбором действий,
   проверками read-before-edit и обязательной верификации, прогрессом и
   checkpoint.
4. Либо возвращает короткий `final_answer`, либо передаёт структурированный
   результат `report_to_user` reporter-промпту для естественного финального
   отчёта.

В runtime-контекст входят ОС, shell, текущее время, язык запроса, известные
версии зависимостей, история сессии, состояние плана, изменённые файлы,
последние результаты инструментов, факты проверки и блокеры завершения.

`chat` работает по облегчённому пути: модель отвечает напрямую в Markdown,
сохраняет короткую историю диалога и не имеет файловых или терминальных
действий.

## Провайдеры и модели

Настраиваемые `fast`, `smart` и `planner` — это уровни моделей, а не роли
агента. Переменные окружения моделей имеют приоритет над сохранённым выбором;
встроенные defaults используются только при отсутствии обоих вариантов.

В текущей реализации routing, planning, chat и формирование отчёта используют
`fast`-модель. `smart` запускает сложные action loops coder/devops. Настройка
`planner` сохраняется и показывается в UI, но `agent_planner.py` пока её не
использует.

| Провайдер | Текущие defaults | Поведение |
| --- | --- | --- |
| OpenAI | `gpt-5.4-mini` для всех трёх модельных ролей | Streaming и native JSON mode. Настроенные модели сверяются с доступными для API key и могут переключаться по внутреннему списку OpenAI. |
| Anthropic | `claude-haiku-4-5` для `fast`; `claude-sonnet-4-6` для `smart` и `planner` | Адаптер native Anthropic Messages API со streaming; JSON обеспечивается промптом и parser, а не `response_format`. |
| OpenRouter | Автоматических defaults нет | Можно выбрать curated-модель или ввести точный ID `provider/model`. Streaming и native JSON mode включены. |
| OmniRoute | Автоматических defaults нет | Точные динамические model IDs вводятся вручную. Используется локальный OpenAI-compatible endpoint; текущая интеграция отключает streaming и native JSON mode. |
| FreeFlow LLM | `gemini-3.5-flash` для всех трёх ролей | Интеграция Gemini/Groq без streaming, JSON разбирается в best-effort режиме. Нужный ключ зависит от backend выбранной модели. Провайдер всё ещё в beta и может работать нестабильно. |
| Custom | Автоматических defaults нет | Сохранённый OpenAI- или Anthropic-compatible endpoint с ручными model IDs и необязательным API key. |

OpenAI остаётся самым безопасным встроенным вариантом для строгих JSON-шагов.
Доступность провайдеров и моделей может меняться на их стороне; таблица
описывает ID и поведение, которые сейчас зашиты в этом репозитории.

### Конфигурация первого запуска

При первом интерактивном запуске Kvist спрашивает провайдера, недостающие model
IDs и credentials, необходимые выбранным моделям. Ввод секретов скрыт. Выбор и
credentials сохраняются в:

- Linux/macOS: `~/.kvist/config.json`
- Windows: `C:\Users\<USERNAME>\.kvist\config.json`

Это обычный JSON-файл в домашнем каталоге пользователя. Не коммитьте и не
передавайте его. Старые конфиги с `OPENAI_API_KEY` внутри JSON по-прежнему
читаются.

Пример:

```json
{
  "llm_provider": "anthropic",
  "anthropic_api_key": "...",
  "role_selection": "auto",
  "llm_models": {
    "anthropic": {
      "fast": "claude-haiku-4-5",
      "smart": "claude-sonnet-4-6",
      "planner": "claude-sonnet-4-6"
    }
  }
}
```

Распознаются переменные credentials `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY` и `OMNIROUTE_API_KEY`.
Соответствующие переменные endpoint: `ANTHROPIC_BASE_URL`,
`OPENROUTER_BASE_URL` и `OMNIROUTE_BASE_URL`.

### OpenRouter

Endpoint по умолчанию — `https://openrouter.ai/api/v1`. У OpenRouter нет
автоматических defaults моделей, потому что их доступность внешняя. При
настройке или через `/models` выберите curated-модель либо введите точный ID,
например:

```text
openai/gpt-5.4-mini
anthropic/claude-sonnet-4-6
google/gemini-3.5-flash
```

### OmniRoute

Endpoint по умолчанию — `http://localhost:20128/v1`. Запустите OmniRoute,
скопируйте API key со страницы Dashboard → Endpoints, выберите OmniRoute через
`/provider` и задайте model ID для каждой роли. ID может иметь вид
`provider/model`, быть именем router/combo без пробелов или явным значением
`auto`.

Пример конфигурации:

```json
{
  "llm_provider": "omniroute",
  "omniroute_api_key": "...",
  "omniroute_base_url": "http://localhost:20128/v1",
  "llm_models": {
    "omniroute": {
      "fast": "kr/claude-haiku-4.5",
      "smart": "kr/claude-sonnet-4.5",
      "planner": "kr/claude-sonnet-4.5"
    }
  }
}
```

Если endpoint недоступен, проверьте, что OmniRoute запущен и base URL
заканчивается на `/v1`. Ошибка authentication обычно указывает на ключ из
Dashboard, а ответы 404 и 400 — на неверный model ID, endpoint или конфигурацию
upstream-провайдера.

### Пользовательские совместимые endpoints

Выберите `+ Create custom provider...` в `/provider`. Wizard запросит:

- ID провайдера из строчных латинских букв;
- base URL с `http://` или `https://`;
- OpenAI-compatible или Anthropic-compatible протокол;
- необязательный API key и отображаемое имя;
- один model ID для всех уровней или отдельный ID для каждого.

Custom providers сохраняются в `custom_providers` и выбираются как
`custom:<id>`. Например:

```json
{
  "llm_provider": "custom:local-vllm",
  "custom_providers": {
    "local-vllm": {
      "label": "Local vLLM",
      "protocol": "openai",
      "base_url": "http://localhost:8000/v1",
      "api_key": "",
      "models": {
        "fast": "local-model",
        "smart": "local-model",
        "planner": "local-model"
      }
    }
  }
}
```

## Runtime-настройки

Полезные переменные окружения и их текущие defaults:

```env
AGENT_LLM_PROVIDER=openai
AGENT_FAST_MODEL=gpt-5.4-mini
AGENT_SMART_MODEL=gpt-5.4-mini
AGENT_PLANNER_MODEL=gpt-5.4-mini
AGENT_MAX_STEPS=50
AGENT_PERSIST_LOGS=1
AGENT_VERBOSE=0
AGENT_API_INTERVAL=
AGENT_SANDBOX_MODE=auto
AGENT_SANDBOX_NET=deny
AGENT_CHILD_ENV_ALLOW=       # дополнительные безопасные переменные через запятую
AGENT_TOOL_PATHS=            # абсолютные каталоги toolchain через разделитель путей
AGENT_TERMINAL_PROFILE=dev
AGENT_TERMINAL_TIMEOUT=15
AGENT_TERMINAL_MAX_OUTPUT=12000
AGENT_WRITE_APPROVAL=1
AGENT_RLIMIT_CPU=          # dev: 120, restricted: 8 (секунды)
AGENT_RLIMIT_AS_MB=        # dev: 2048, restricted: 256
AGENT_RLIMIT_NPROC=        # dev: 4096, restricted: 256
AGENT_FETCH_EXTRA_DOMAINS= # доп. домены для fetch_url, через запятую
```

- `AGENT_MAX_STEPS` ограничивает цикл действий coder/devops.
- `AGENT_PERSIST_LOGS=0` отключает логи завершённых запусков, но не файлы
  сессий и checkpoint для `/resume`.
- `AGENT_VERBOSE=1` показывает выбор модели, retries и другую диагностику.
- `AGENT_API_INTERVAL` переопределяет задержку между API-вызовами. Без него
  OpenAI, Anthropic и OpenRouter используют 2,5 секунды, остальные провайдеры —
  настроенный default в 5 секунд.
- `AGENT_TERMINAL_PROFILE=restricted` жёстко блокирует запуск интерпретаторов,
  package managers, build tools и shells, кроме точных version probes через
  доверенный системный executable.
- Окружение дочернего процесса строится из allowlist, а не наследуется целиком.
  `AGENT_CHILD_ENV_ALLOW=PROJECT_MODE,FEATURE_FLAG` явно разрешает дополнительные
  несекретные и не управляющие загрузчиком переменные, в том числе для
  `env_overrides`; секретные и execution-control имена всё равно блокируются.
  Каждый terminal run получает новый приватный и изначально пустой child `HOME`.
- Child `PATH` сначала добавляет существующие проектные каталоги инструментов
  (`.venv/bin`, `venv/bin`, `node_modules/.bin`) внутри workspace, затем
  проверенные записи `AGENT_TOOL_PATHS`, затем доверенные системные каталоги.
  Каждая запись `AGENT_TOOL_PATHS` должна быть абсолютным существующим каталогом
  без world-writable прав; она монтируется read-only в Bubblewrap. Полный `HOME`,
  `.local/bin`, nvm, pyenv и cargo автоматически не добавляются. Стандартные
  Python virtualenv работают: `.venv/bin/python`, ссылающийся на доверенный
  системный интерпретатор, запускается с корректным `sys.prefix` и site-packages.
- Terminal timeout ограничивается диапазоном 1-300 секунд в профиле `dev` и
  1-30 секунд в `restricted`; захватываемый вывод — диапазоном
  1 000-200 000 символов.
- Дочерние процессы Unix получают профильные resource limits (CPU, память,
  число процессов, размер файла, открытые файлы). `AGENT_RLIMIT_CPU`,
  `AGENT_RLIMIT_AS_MB` и `AGENT_RLIMIT_NPROC` переопределяют дефолты профиля.
- `AGENT_WRITE_APPROVAL=0` отключает интерактивное подтверждение с diff-превью
  при перезаписи существующих файлов (см. «Модель безопасности»). Нужно для CI
  и других неинтерактивных запусков; при включённом гейте и без TTY перезапись
  детерминированно завершается ошибкой.

## Web search

Web-доступ предназначен для актуальной внешней документации, API references,
release notes, migration guides, цен и лимитов. Он не используется, если ответ
уже есть в workspace. Результаты поиска и загруженный контент помечаются как
недоверенные до добавления в контекст модели.

```env
SEARCH_PROVIDER=auto
SEARXNG_BASE_URL=
TAVILY_API_KEY=
BRAVE_SEARCH_API_KEY=
WEB_SEARCH_TIMEOUT=8
WEB_SEARCH_MAX_RESULTS=5
```

Автоматический fallback-порядок: SearXNG → Tavily → Brave. Если задать
`SEARCH_PROVIDER` как `searxng`, `tavily` или `brave`, этот провайдер будет
проверен первым, затем остальные настроенные провайдеры. Search timeout
ограничивается 1-30 секундами, число результатов — 1-20.

`fetch_url` принимает только HTTP(S), ограничивает размер ответа, отклоняет
очевидные бинарные форматы и разрешает общее чтение только с доменов из runtime
allowlist. Список покрывает ходовые сайты официальной документации (Python,
MDN, Go, Rust, Kubernetes, Docker, nginx, GitHub и другие) и расширяется через
`AGENT_FETCH_EXTRA_DOMAINS=example.dev,docs.internal.io`. Загруженный контент
всегда помечается как недоверенный независимо от домена.

## Файлы в workspace

Локальное состояние проекта хранится в `.kvist/`:

```text
<workspace>/.kvist/
├── active_session.json
├── change_journal.json
├── sessions/
│   └── <name>.json
└── logs/
    ├── run_001.json
    └── debug/logs.jsonl
```

Документы сессий, маркер активной сессии и журнал изменений записываются
атомарно с долговечной резервной копией (`*.json.bak`), приватными правами
файлов и редакцией учётных данных. Фронтенды никогда не трогают эти файлы
напрямую — headless-управление сессиями идёт через `--session-op`
(см. `UI_API.ru.md`).

`logs/` появляется только при включённом run logging и сохранённом запуске.
Performance JSONL logging по умолчанию выключен в коде. API keys остаются в
конфиге домашнего каталога, а не в workspace.

## Модель безопасности

Kvist использует обязательный для каждого запуска authorization pipeline и на
Linux по умолчанию выбирает OS-песочницу, если найден доверенный Bubblewrap:

- Файловые действия разрешают пути внутри workspace и отклоняют symlink-побеги.
- Доступ к `.env`, внутреннему состоянию `.kvist`, секретным материалам,
  слишком большим файлам и не-UTF-8 целям редактирования ограничен. Аргументы
  shell-команд, указывающие на секретные пути (`.env*`, `.ssh`, `.aws`,
  `.gnupg`, ключи/сертификаты, `credentials`), отклоняются для любой команды,
  включая read-only `cat` и `grep`, на относительных и абсолютных путях.
- Под Bubblewrap защита секретов обеспечивается ОС, а не только проверкой argv:
  перед запуском Kvist перечисляет защищённые файлы/каталоги workspace и
  маскирует их внутри песочницы (файлы подменяются read-only `/dev/null`,
  каталоги — пустым tmpfs). Произвольный дочерний код — Python, Node, shell,
  AWK/sed, конструирующие путь во время выполнения, скомпилированные бинарники,
  symlink — читает пустой объект или получает permission denied. Дайджест
  набора защищённых путей привязан к подтверждению.
- Shell-команды проходят platform-aware allowlist и role-aware risk policy.
  Всё, что не read-only — интерпретаторы и сборщики (`python`, `node`, `make`,
  `cargo`, `go`, `awk`), установка пакетов, `git push`, неизвестные команды —
  требует подтверждения человеком. Project allow может покрыть только
  допустимые SAFE/MODERATE команды, но не intrinsic DANGEROUS; без TTY
  обязательное подтверждение отклоняется.
- Жёсткий запрет отделён от команды, требующей подтверждения. Запрещённые
  executable, выход из workspace, секретные/внутренние пути, shell substitution
  и опасные формы `find`/AWK/sed не показывают prompt, не снимаются allow-правилом
  и не доходят до создания процесса.
- Version probe задаётся отдельно для каждого executable и сравнивает весь argv.
  Path-qualified файл не наследует SAFE-классификацию системной команды;
  lookup- и target-identity executable входят в контекст авторизации.
- Action-предикаты `find` (`-delete`, `-exec`, `-ok`, `-fprintf`, ...)
  отклоняются; чистый поиск не требует подтверждения. `awk system()`/`getline`
  с пайпом и `sed` в in-place/execute/write формах отклоняются.
- Read-only Git-команды используют прямой синтаксис `git SUBCOMMAND` и
  запускаются с отключёнными filesystem monitor/index hooks, external
  diff/textconv, implicit signature verification, remerge drivers и recursive
  submodule helpers. Глобальные Git-опции перед subcommand отклоняются fail
  closed; status/diff также fail closed при filter/include в repository config.
- Docker run/create/build/buildx/service с host-монтированием, `--privileged`,
  небезопасными build entitlements или host namespaces (`--network=host`,
  `--pid=host`, ...), публикацией host ports, push image и вложенными
  destructive operations считаются опасными для любой роли.
- Разрушительные команды и повышение привилегий через `rm`, `sudo`, `dd`,
  `mkfs`, `shutdown` и `reboot` запрещены обычной валидацией.
- Перезапись существующего файла (`write_file`, `append_file`,
  `replace_in_file`) показывает unified diff и запрашивает подтверждение;
  создание нового файла — нет. `AGENT_WRITE_APPROVAL=0` отключает гейт.
- Shell operators и command substitution ограничены, рабочий каталог остаётся
  внутри workspace, а дочерние процессы Unix получают профильные resource
  limits, включая защиту от форк-бомбы (`RLIMIT_NPROC`).
- Непосредственно перед spawn Kvist повторяет hard validation и сравнивает argv,
  нормализованный cwd и identity каталога, роль, состояние policy, режимы
  sandbox/network, digest очищенного environment, identity executable и SHA-256
  прямо выбранных scripts/build-control files. Изменение отменяет подтверждение.
- Логи скрывают распространённые форматы токенов и обрезают большие значения.

### Проектная политика разрешений

`<workspace>/.kvist/permissions.json` хранит правила уровня проекта, по
аналогии с `.claude/settings.json` в Claude Code:

```json
{
  "allow": ["git status", "python *"],
  "deny": ["pip install *"],
  "ask": ["npm run"]
}
```

Паттерны сопоставляются по префиксу токенов с shell-glob на каждый токен
(`python *` покрывает любой Python argv); executable дополнительно сравнивается
в канонической форме. Порядок: project `deny` → hard validation → intrinsic
`DANGEROUS` → project `ask` → подтверждение executable-origin/auto-fallback →
допустимый project `allow` → обычная risk policy. `DANGEROUS` всегда требует
подтверждения конкретного запуска и не предлагается как «always allow».
`allow` снимает повторный prompt только с допустимых SAFE/MODERATE команд и не
разблокирует `rm`, чтение секретов или нарушение sandbox. Пересекающиеся правила
разрешаются в пользу `deny`; каждый сегмент pipeline проверяется отдельно.
Модель не может редактировать `.kvist`; malformed или symlinked policy fail
closed.

`AGENT_SANDBOX_MODE=auto` на Linux выбирает доверенный системный `bwrap`.
Песочница монтирует только workspace и необходимые runtime/library paths, без
host root, `/home`, `/root`, других репозиториев и каталогов credentials. `/tmp`
эфемерен, `HOME` пуст, `.kvist` и все защищённые секреты workspace замаскированы,
process namespaces изолированы, а сеть по умолчанию unshared.
`AGENT_SANDBOX_NET=allow` следует включать только для команды, которой
действительно нужна сеть.

Если Bubblewrap недоступен в `auto`, включается явно отмеченный restricted
workspace fallback: interpreters, scripts, build/package tools и неизвестные
arbitrary-code executables требуют подтверждения каждого запуска даже при
совпавшем project allow, и жёстко отклоняются, если нет cgroup v2 scope для
контроля detached-потомков. Явный `AGENT_SANDBOX_MODE=workspace` — более слабый
режим, где одобренный процесс сохраняет host-права текущего пользователя; он
сопровождается предупреждением и **не** даёт ни маскировки секретов, ни изоляции
`.kvist`. В этих слабых режимах каждый run всё равно получает новый приватный,
проверенно-пустой child `HOME`. Явный `bubblewrap` без доверенного binary
блокирует shell execution и не деградирует незаметно.

Каждый запуск выполняется под супервизором дерева процессов: команда, которая
завершилась по timeout или нормально, не может оставить detached (`setsid`)
потомков — под Bubblewrap их убивает PID namespace, а cgroup v2 scope сносит всё
поддерево и по timeout, и по завершению. Вывод захватывается в ограниченные
in-memory буферы во время работы процесса (без неограниченной буферизации и без
блокирующего повторного drain), поэтому дочерний процесс не может исчерпать
память родителя. Скрипты с `#!` аттестуют интерпретатор (прямой, `env NAME`,
`env -S NAME ARGS`): он должен быть доверенным системным бинарником или файлом
workspace, а его identity/содержимое привязаны к подтверждению.

### Текущие ограничения границы

- Workspace mode не является OS isolation. Read-only набор automatic fallback
  также видит несекретные host paths, разрешённые command validation.
- Прямые scripts и распространённые build-control files хэшируются, но
  transitive imports, модули `python -m`, Make includes, исходники package
  scripts и другие dependency graphs полностью не аттестуются.
- Child PATH сначала добавляет проектные каталоги инструментов и проверенные
  `AGENT_TOOL_PATHS`, затем фиксированные системные каталоги (без `/usr/local`),
  но transitive helpers, запускаемые доверенными системными tools, отдельно по
  содержимому не аттестуются.
- Проверка secret paths покрывает named/canonical direct arguments, а под
  Bubblewrap защищённые файлы/каталоги маскируются на уровне ОС, так что
  сконструированные во время выполнения пути до них не доходят.
  Repository/history-aware tools всё ещё могут вывести защищённый content, не
  названный в argv. Strong Bubblewrap скрывает host credential trees и маскирует
  секреты workspace, но в явном режиме `workspace` ни маскировка секретов, ни
  изоляция `.kvist` не действуют.
- Между финальным сравнением identity/hash и OS process creation остаётся узкое
  race window. Очистка detached-потомков опирается на Bubblewrap PID namespace
  или cgroup v2 scope; в явном режиме `workspace` без cgroup v2 и на Windows
  timeout cleanup не гарантирует завершение каждого descendant.

## Архитектура промптов и действий

Активные файлы промптов:

- `prompts/router_prompt.txt` — выбирает `coder`, `devops` или `chat` для
  запросов, не обработанных детерминированной маршрутизацией;
- `prompts/planner_prompt.txt` — возвращает план задачи, критерии успеха, риски и
  complexity в JSON;
- `prompts/coder_prompt.txt` — строгий протокол работы с кодом и workspace;
- `prompts/devops_prompt.txt` — строгий DevOps-протокол с учётом системы и
  правил подтверждения человеком;
- `prompts/chat_prompt.txt` — прямой Markdown-диалог без инструментов;
- `prompts/report_prompt.txt` — превращает структурированный результат задачи в
  короткий пользовательский отчёт.

Coder и DevOps могут выдавать следующие валидируемые действия:

```text
final_answer, report_to_user,
read_file, read_multiple_files, write_file, append_file, replace_in_file,
list_dir, list_tree, find_files, search_code,
git_status, git_diff, run_shell,
web_search, fetch_url, delegate_task
```

`parser.py` проверяет точную схему, `executor.py` реализует действия, а
`agent_runtime.py` блокирует преждевременное завершение, нарушения read-only,
повторяющиеся тупиковые шаги и непроверенные правки.

## Структура проекта

Основной runtime:

- `main.py` запускает REPL и обрабатывает slash-команды и интерактивную
  настройку;
- `agent.py` координирует routing, planning, chat, сессии и цикл действий;
- `agent_runtime.py` выполняет шаги, отслеживает доказательства и формирует
  финальные отчёты;
- `agent_state.py` определяет конфигурацию, план и состояние запуска;
- `agent_context.py` собирает и priority-trim runtime-контекст каждого шага;
- `agent_model.py` загружает промпты ролей, выбирает уровни моделей, обрабатывает
  streaming, retries и fallback из JSON mode;
- `agent_planner.py` запускает planner и предоставляет локальный fallback-план.

Провайдеры и маршрутизация:

- `llm_clients.py` определяет провайдеры, списки моделей, defaults, валидацию,
  адаптеры и fallback candidates;
- `user_config.py` читает и пишет `~/.kvist/config.json` и custom providers;
- `role_router.py` сочетает deterministic, LLM, keyword и default routing;
- `language_policy.py` и `language_utils.py` сохраняют язык пользовательского
  ответа.

Исполнение и сохранение состояния:

- `executor.py`, `terminal.py`, `command_policy.py` и `sandbox.py` реализуют
  действия, shell validation, подтверждения, risk grading и bubblewrap;
- `parser.py` и `protocol.py` задают строгий протокол действий;
- `agent_logging.py` хранит именованные сессии, checkpoints и run logs;
- `agent_web.py` ограничивает условия использования web-действий;
- `deps.py` определяет версии зависимостей по Python project и lock-файлам;
- `ui.py` и `render_throttle.py` эффективно отображают терминальный интерфейс.

Сборка и интеграции:

- `kvist.spec` задаёт сборку PyInstaller и включает `prompts/`;
- `package_script.py` создаёт архив исходников из видимых Git файлов;
- `frontend_protocol.py`, `event_stream_ui.py`, `event_session.py` и
  `event_stdio.py` реализуют версионированную NDJSON-границу фронтенда;
- `gui/` содержит Electron-фронтенд и тесты его моста;
- `jetbrains-plugin/` содержит интеграцию IntelliJ Platform для Windows и Linux,
  закреплённую на линейке платформы 2025.3.

## Тесты

Полный набор тестов запускается из корня репозитория:

```bash
.venv/bin/python -m pytest -ra
```

Быстрый smoke-набор без credentials:

```bash
.venv/bin/python -m pytest -m offline_smoke -q
```

Статическая проверка ошибок:

```bash
.venv/bin/python -m ruff check .
```

Тесты GUI-моста:

```bash
cd gui && npm test
```

Отдельные prompt-тесты проверяют, что runtime использует верхний каталог
`prompts/`, а документированные схемы JSON-действий синхронизированы с parser.
