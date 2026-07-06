# DevAgentXD

DevAgentXD это локальный coding agent для одного workspace за раз.

Я бы использовал его для мелкой, но надоедливой работы по репозиторию: найти,
где грузится настройка, сделать узкую правку, объяснить старый файл, проверить,
почему после обновления библиотеки что-то поехало. Он работает в терминале,
держится внутри указанной папки и показывает прогресс по ходу дела.

Это не IDE и не волшебная песочница. Дифф всё равно надо читать.

## Что нужно

- Python 3.11+
- OpenAI API key или ключ совместимого провайдера для FreeFlow

JetBrains-плагин собирается отдельно; короткая заметка есть внизу.

## Настройка

Из корня репозитория:

```bash
pip install -r requirements.txt
```

Для первого запуска `.env` не нужен. Если `~/.devagent/config.json` отсутствует,
DevAgentXD спросит ключ для активного провайдера и сохранит имя провайдера в тот
же файл.

Пример:

```json
{
	"llm_provider": "openai",
	"openai_api_key": "sk-..."
}
```

Путь к конфигу:

- Linux/macOS: `~/.devagent/config.json`
- Windows: `C:\Users\<USERNAME>\.devagent\config.json`

Старый вариант с `OPENAI_API_KEY` внутри этого JSON тоже читается.

Если переключиться на `FreeFlow LLM`, DevAgentXD сохранит выбранный провайдер и
при необходимости может держать там же `gemini_api_key` или `groq_api_key`.
Выбранные модели тоже сохраняются в этом же конфиге отдельно для каждого провайдера.

## Запуск

Из исходников:

```bash
python main.py --workspace "<project_path>"
```

Через собранный бинарник:

```bash
devagent --workspace "<project_path>"
```

`--workspace` обязателен. Если папки нет, DevAgentXD её создаст. Я обычно передаю
абсолютный путь: относительные пути тоже работают, но считаются от терминала,
из которого был запуск.

Примеры задач:

```text
> Find where settings are loaded and explain the flow
```

```text
> Add validation for empty workspace paths and keep the error message short
```

Выход — `/bye`. Если прошлый запуск оборвался и checkpoint успел сохраниться,
`/resume` попробует продолжить. Команда `/provider` открывает выбор провайдера
через стрелки на клавиатуре. Команда `/models` так же позволяет стрелками
выбрать `fast`, `smart` и `planner` модели для текущего провайдера.

Многострочный ввод чуть старомодный: поставьте `\` в конце строки.

```text
> Refactor the parser so that \
... validation errors include the action type
```

## Превью

### Первый запуск

Когда DevAgentXD просит API key, символы при вводе не показываются. Это
нормально: вставьте ключ и нажмите Enter.

![Первый запуск DevAgentXD с невидимым вводом API key](preview/start.png)

### Работа агента

Во время задачи агент показывает, что он делает: какие файлы смотрит, какой шаг
выбирает дальше и где применяет правку.

![DevAgentXD работает в терминале](preview/working.png)

### Финальный отчёт

В конце DevAgentXD выводит короткий отчёт: что было сделано, какие были заметки
по ходу работы и какой результат стоит проверить.

![Финальный отчёт DevAgentXD в терминале](preview/end.png)

## Настройки, которые полезно знать

```env
AGENT_LLM_PROVIDER=openai
AGENT_FAST_MODEL=gpt-5-mini
AGENT_SMART_MODEL=gpt-5
AGENT_PLANNER_MODEL=gpt-5
AGENT_MAX_STEPS=20
AGENT_PERSIST_LOGS=1
AGENT_VERBOSE=0
```

`AGENT_MAX_STEPS` я бы трогал первым. Поднимайте, если агент регулярно
останавливается за шаг-два до результата. `AGENT_VERBOSE=1` полезен при отладке
самого DevAgentXD, в обычной работе быстро надоедает.

По провайдерам:

- `OpenAI` остаётся самым безопасным вариантом для строгого JSON-режима.
- Для `OpenAI` по умолчанию используются модели семейства `GPT-5`, с авто-fallback
	на `GPT-4.1`, если у ключа нет доступа к `GPT-5`.
- `FreeFlow LLM` подключён как опциональный провайдер для моделей Gemini/Groq.
- У `FreeFlow LLM` нет такой же гарантии native JSON mode, как у официального
	OpenAI SDK, поэтому здесь используется fallback на обычные completions.
- Для `FreeFlow LLM` по умолчанию все роли используют `gemini-3.1-pro-preview`.
- **⚠️ `FreeFlow LLM` сейчас находится в бета-тестировании и остаётся очень
  нестабильным.** Возможны периодические сбои, таймауты и непоследовательные
  ответы. Используйте `OpenAI`, если нужна стабильная работа.
- `OmniRoute` — это локальный OpenAI-совместимый gateway для маршрутизации запросов
  к нескольким LLM-провайдерам. См. раздел OmniRoute ниже для настройки.

`AGENT_PERSIST_LOGS=0` отключает run-логи, но сессии и `/resume` всё равно
используют `<project>/.devagent/sessions/<имя>.json`.

Web search тут специально ограничен. Он нужен для внешних библиотек,
документации, релизов, цен, лимитов и прочих вещей, которые могут устареть.

```env
SEARCH_PROVIDER=auto
SEARXNG_BASE_URL=
TAVILY_API_KEY=
BRAVE_SEARCH_API_KEY=
```

## OmniRoute

OmniRoute — это локальный OpenAI-совместимый gateway, который маршрутизирует запросы к нескольким LLM-провайдерам.

**DevAgentXD не поставляется с моделями по умолчанию для OmniRoute.** Вы должны вручную вводить точные model ids для OmniRoute, потому что провайдеры и модели OmniRoute динамические.

### Настройка

1. Запустите OmniRoute локально (по умолчанию: `http://localhost:20128`)
2. Получите API key из OmniRoute Dashboard → Endpoints
3. Выберите OmniRoute как провайдера при запуске DevAgentXD
4. Введите точные model ids для каждой роли

### Конфигурация

Пример `~/.devagent/config.json`:

```json
{
  "llm_provider": "omniroute",
  "omniroute_api_key": "ВАШ_КЛЮЧ",
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

Переменные окружения:

```bash
AGENT_LLM_PROVIDER=omniroute
OMNIROUTE_API_KEY=...
OMNIROUTE_BASE_URL=http://localhost:20128/v1
AGENT_FAST_MODEL=kr/claude-haiku-4.5
AGENT_SMART_MODEL=kr/claude-sonnet-4.5
AGENT_PLANNER_MODEL=kr/claude-sonnet-4.5
```

### Формат Model ID

Model ids для OmniRoute обычно следуют формату `provider/model`:

```text
kr/claude-sonnet-4.5
kr/claude-haiku-4.5
if/qwen3-coder-plus
```

Вы также можете использовать имена combo/router без слешей или ввести `auto` для авто-маршрутизации OmniRoute (только если намеренно).

### Ручной ввод моделей

Когда вы выбираете OmniRoute или запускаете `/models`:

1. DevAgentXD запрашивает точный model id для каждой роли (fast, smart, planner)
2. Введите model id вручную
3. Валидация проверяет формат (без пробелов, без URL, правильная структура `provider/model`)
4. Модели сохраняются в конфиг для будущих сессий

### Устранение проблем

**"OmniRoute endpoint is unavailable"**
- Проверьте, что OmniRoute запущен
- Проверьте, что base URL — `http://localhost:20128/v1`

**"OmniRoute authentication failed"**
- Проверьте API key из Dashboard → Endpoints

**"OmniRoute model or endpoint was not found"**
- Проверьте точный model id (например, `kr/claude-sonnet-4.5`)
- Проверьте, что провайдер подключен в OmniRoute

**"OmniRoute returned 400 bad_request"**
- Обычно означает неправильный model id, неподдерживаемую форму запроса, сломанную трансформацию провайдера или upstream провайдер отклонил запрос
- Попробуйте другой точный model id или проверьте логи OmniRoute

## Что появляется на диске

Внутри целевого проекта:

- `<project>/.devagent/logs/`
- `<project>/.devagent/sessions/<имя>.json`
- `<project>/.devagent/active_session.json`

API key остаётся в домашнем конфиге, а не в проекте.

## Острые углы

Доступ к файлам ограничен workspace. Shell-команды идут через allowlist. Есть
лимиты размера файлов, вывода и запрет на чтение очевидных бинарных URL.

Но это инструмент для работы под присмотром. Он может не так понять задачу или
уверенно поправить не тот кусок. Самый нормальный режим: одна небольшая правка,
проверка, потом следующая.

Часть внутренних ошибок пока выглядит некрасиво. Сначала доводился runtime,
полировку ещё можно подтянуть.

## Структура проекта

Ядро:

- `main.py` запускает CLI и связывает workspace, ключ, модели и UI.
- `agent.py` склеивает части агента.
- `agent_runtime.py` ведёт цикл шагов и checkpoint.
- `agent_state.py` хранит `AgentConfig` и runtime-датаклассы (`PlanInfo` и т.д.).
- `agent_model.py` вызывает action-модель, стриминг, подсчёт токенов и fallback моделей.
- `agent_planner.py` запускает опциональный шаг планирования перед основным циклом.
- `agent_context.py` собирает контекст для каждого шага с локализованными метками.

Исполнение и парсинг:

- `executor.py` отвечает за безопасные действия с файлами, shell, git и web.
- `parser.py` и `protocol.py` проверяют действия модели.
- `terminal.py` хранит allowlist shell-команд.
- `agent_web.py` ограничивает web search и чтение URL.

LLM и провайдеры:

- `llm_clients.py` настраивает клиенты OpenAI и FreeFlow, списки моделей и fallback-цепочки.
- `user_config.py` читает/пишет `~/.devagent/config.json`, запрашивает ключи и выбор провайдера.

Утилиты:

- `context_utils.py` sliding-window тример контекста для истории сообщений.
- `render_throttle.py` неблокирующий throttle для потокового вывода.
- `language_utils.py` определяет язык запроса пользователя и подбирает локализованные строки.
- `deps.py` сканирует файлы зависимостей workspace для получения версий.
- `performance_logger.py` структурированный JSONL-логгер для `.devagent/logs/debug/`.

UI и сборка:

- `ui.py` рисует вывод в терминале.
- `agent_logging.py` пишет run-логи и checkpoint.
- `package_script.py` скрипт сборки через PyInstaller.

Промпты:

- `prompts/coder_prompt.txt` системный промпт для роли coder.
- `prompts/devops_prompt.txt` системный промпт для роли devops.
- `prompts/chat_prompt.txt` системный промпт для роли chat.
- `prompts/router_prompt.txt` системный промпт для роутера ролей.
- `prompts/planner_prompt.txt` системный промпт для planner-модели.

`jetbrains-plugin/` содержит IntelliJ Platform plugin: он запускает DevAgentXD в IDE
Terminal для текущего проекта. Плагин прибит к IntelliJ Platform 2025.3,
поддерживает Windows/Linux и ждёт собранные бинарники в `jetbrains-plugin/bin/`.
