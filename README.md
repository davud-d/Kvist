# DagAI

Minimal local AI agent for working inside a sandboxed workspace.

## Requirements

- Python 3.11+
- OpenAI API key

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Put your API key into `API_KEYS.env`:

```env
OPENAI_API_KEY=sk-...
```

Optional settings:

```env
AGENT_FAST_MODEL=gpt-4.1-mini
AGENT_SMART_MODEL=gpt-4.1
AGENT_MAX_STEPS=20
AGENT_WORKSPACE=workspace
AGENT_VERBOSE=0
```

## Run

```bash
python main.py
```

Or specify a custom workspace:

```bash
python main.py --workspace ./workspace
```

## Project Layout

- `main.py` — CLI entry point
- `agent.py` — agent logic and orchestration
- `executor.py` — safe workspace executor
- `workspace/` — working directory and logs