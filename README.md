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
AGENT_FAST_MODEL=gpt-5.4-mini
AGENT_SMART_MODEL=gpt-5.4
AGENT_PLANNER_MODEL=gpt-5.4
AGENT_MAX_STEPS=20
AGENT_WORKSPACE=workspace
AGENT_PERSIST_LOGS=1
AGENT_VERBOSE=0
```

By default, `main.py` uses `gpt-5.4-mini` for routine steps and `gpt-5.4` for higher-quality reasoning. If `AGENT_PLANNER_MODEL` is not set, the planner also uses the smart model.

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
- `agent.py` — `LocalAgent` composition layer that binds runtime, planning, model, logging, and UI helpers
- `agent_state.py` — runtime config and mutable run state dataclasses
- `agent_planner.py` — plan generation and fallback planning
- `agent_runtime.py` — main execution loop, progress tracking, and feedback handling
- `agent_context.py` — prompt/context assembly for each step
- `agent_model.py` — model calls, retries, token accounting, and model selection
- `agent_logging.py` — checkpoints and persisted run logs
- `agent_web.py` — web-search and fetch guards plus extracted web facts
- `executor.py` — safe workspace executor
- `parser.py` and `protocol.py` — command parsing and schema definitions
- `ui.py` — terminal rendering and live status output
- `workspace/` — working directory and logs