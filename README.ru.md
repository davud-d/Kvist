# DevAgentXD

DevAgentXD это локальный coding agent для одного workspace за раз.

Я бы использовал его для мелкой, но надоедливой работы по репозиторию: найти,
где грузится настройка, сделать узкую правку, объяснить старый файл, проверить,
почему после обновления библиотеки что-то поехало. Он работает в терминале,
держится внутри указанной папки и показывает прогресс по ходу дела.

Это не IDE и не волшебная песочница. Дифф всё равно надо читать.

## Что нужно

- Python 3.11+
- OpenAI API key

JetBrains-плагин собирается отдельно; короткая заметка есть внизу.

## Настройка

Из корня репозитория:

```bash
pip install -r requirements.txt
```

Для первого запуска `.env` не нужен. Если `~/.devagent/config.json` отсутствует,
DevAgentXD спросит OpenAI API key, проверит его через API и запишет:

```json
{
	"openai_api_key": "sk-..."
}
```

Путь к конфигу:

- Linux/macOS: `~/.devagent/config.json`
- Windows: `C:\Users\<USERNAME>\.devagent\config.json`

Старый вариант с `OPENAI_API_KEY` внутри этого JSON тоже читается.

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
`/resume` попробует продолжить.

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
AGENT_FAST_MODEL=gpt-5.4-nano
AGENT_SMART_MODEL=gpt-5.4-mini
AGENT_PLANNER_MODEL=gpt-4o
AGENT_MAX_STEPS=20
AGENT_PERSIST_LOGS=1
AGENT_VERBOSE=0
```

`AGENT_MAX_STEPS` я бы трогал первым. Поднимайте, если агент регулярно
останавливается за шаг-два до результата. `AGENT_VERBOSE=1` полезен при отладке
самого DevAgentXD, в обычной работе быстро надоедает.

`AGENT_PERSIST_LOGS=0` отключает run-логи, но `/resume` всё равно использует
`<project>/.devagent/state.json`.

Web search тут специально ограничен. Он нужен для внешних библиотек,
документации, релизов, цен, лимитов и прочих вещей, которые могут устареть.

```env
SEARCH_PROVIDER=auto
SEARXNG_BASE_URL=
TAVILY_API_KEY=
BRAVE_SEARCH_API_KEY=
```

## Что появляется на диске

Внутри целевого проекта:

- `<project>/.devagent/logs/`
- `<project>/.devagent/state.json`

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

- `main.py` запускает CLI и связывает workspace, ключ, модели и UI.
- `agent.py` склеивает части агента.
- `agent_runtime.py` ведёт цикл шагов и checkpoint.
- `executor.py` отвечает за безопасные действия с файлами, shell, git и web.
- `parser.py` и `protocol.py` проверяют действия модели.
- `terminal.py` хранит allowlist команд.
- `agent_web.py` ограничивает web search и чтение URL.
- `agent_logging.py` пишет run-логи и checkpoint.
- `ui.py` рисует вывод в терминале.

`jetbrains-plugin/` содержит IntelliJ Platform plugin: он запускает DevAgentXD в IDE
Terminal для текущего проекта. Плагин прибит к IntelliJ Platform 2025.3,
поддерживает Windows/Linux и ждёт собранные бинарники в `jetbrains-plugin/bin/`.
