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
AGENT_FAST_MODEL=gpt-4.1-mini
AGENT_SMART_MODEL=gpt-4.1
AGENT_MAX_STEPS=20
AGENT_WORKSPACE=workspace
AGENT_VERBOSE=0
```

## Запуск

```bash
python main.py
```

Или с указанием рабочей директории:

```bash
python main.py --workspace ./workspace
```

## Структура

- `main.py` — точка входа CLI
- `agent.py` — логика и оркестрация агента
- `executor.py` — безопасный исполнитель в workspace
- `workspace/` — рабочая директория и логи