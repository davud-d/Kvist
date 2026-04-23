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

2. Добавить ключ в `API_KEYS.env`:

```env
OPENAI_API_KEY=sk-...
```

Необязательные параметры:

```env
AGENT_FAST_MODEL=gpt-5.4-mini
AGENT_SMART_MODEL=gpt-5.4
AGENT_PLANNER_MODEL=gpt-5.4
AGENT_MAX_STEPS=20
AGENT_WORKSPACE=workspace
AGENT_PERSIST_LOGS=1
AGENT_VERBOSE=0
```

По умолчанию `main.py` использует `gpt-5.4-mini` для обычных шагов и `gpt-5.4` для более качественного рассуждения. Если `AGENT_PLANNER_MODEL` не задан, планировщик тоже использует smart model.

## Запуск

```bash
python main.py
```

Или с указанием рабочей директории:

```bash
python main.py --workspace ./workspace
```

Относительные пути в --workspace вычисляются от текущей директории терминала, из которого был запущен агент.

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
- `workspace/` — рабочая директория и логи