# DagAI

Минимальный локальный AI-агент для работы внутри изолированного workspace.

## Требования

- Python 3.11+
- OpenAI API key

## Настройка

1. Установить зависимости:

```bash
pip install -r requirements.txt
```

2. Для первого запуска `.env` не нужен.

Если `~/.devagent/config.json` ещё не существует, агент спросит OpenAI API key в терминале, проверит его реальным запросом к OpenAI API и сохранит как JSON:

```json
{
	"openai_api_key": "sk-..."
}
```

Расположение конфига:

- Linux/macOS: `~/.devagent/config.json`
- Windows: `C:\Users\<USERNAME>\.devagent\config.json`

Необязательные параметры:

```env
AGENT_FAST_MODEL=gpt-5.4-nano
AGENT_SMART_MODEL=gpt-5.4-mini
AGENT_PLANNER_MODEL=gpt-4o
AGENT_MAX_STEPS=20
AGENT_PERSIST_LOGS=1
AGENT_VERBOSE=0
```

`AGENT_PERSIST_LOGS=0` отключает только сохранение run-логов. Checkpoint для `/resume` всё равно использует `<project>/.devagent/state.json`.

По умолчанию `main.py` использует `gpt-5.4-nano` для обычных шагов, `gpt-5.4-mini` для более качественного рассуждения и `gpt-4o` для планирования.

## Запуск

Поддерживается только один контракт запуска:

```bash
devagent --workspace "<project_path>"
```

Аргумент `--workspace` обязателен. Путь должен уже существовать. Относительные пути вычисляются от текущей директории терминала и перед запуском приводятся к абсолютному пути.

## Служебные файлы

Внутри целевого проекта агент хранит:

- `<project>/.devagent/logs/` для сохранённых логов запусков
- `<project>/.devagent/state.json` для checkpoint и `/resume`

Команда `/resume` продолжает работу из `<project>/.devagent/state.json`.

## Структура

- `main.py` — точка входа CLI
- `agent.py` — слой композиции `LocalAgent`, который связывает runtime, planner, model, logging и UI
- `agent_state.py` — dataclass-конфиг и изменяемое состояние запуска
- `agent_planner.py` — построение плана и fallback-планирование
- `agent_runtime.py` — основной цикл выполнения, прогресс и обработка feedback
- `agent_context.py` — сборка prompt и контекста для каждого шага
- `agent_model.py` — вызовы моделей, ретраи, подсчёт токенов и выбор модели
- `agent_logging.py` — checkpoint и сохранение логов
- `agent_web.py` — ограничения web-search/fetch и извлечение внешних фактов
- `executor.py` — безопасный исполнитель в workspace
- `parser.py` и `protocol.py` — парсинг команд и описание схемы протокола
- `ui.py` — отрисовка терминального UI и live-статуса
- `<project>/.devagent/` — сохранённые логи и состояние продолжения