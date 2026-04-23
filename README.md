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

2. The first run does not require any `.env` file.

If `~/.devagent/config.json` does not exist yet, the agent asks for your OpenAI API key in the terminal, validates it with a real OpenAI API request, and stores it as JSON:

```json
{
	"openai_api_key": "sk-..."
}
```

Config location:

- Linux/macOS: `~/.devagent/config.json`
- Windows: `C:\Users\<USERNAME>\.devagent\config.json`

Optional settings:

```env
AGENT_FAST_MODEL=gpt-5.4-nano
AGENT_SMART_MODEL=gpt-5.4-mini
AGENT_PLANNER_MODEL=gpt-4o
AGENT_MAX_STEPS=20
AGENT_PERSIST_LOGS=1
AGENT_VERBOSE=0
```

By default, `main.py` uses `gpt-5.4-nano` for routine steps, `gpt-5.4-mini` for higher-quality reasoning, and `gpt-4o` for planning.

## Run

Only one launch contract is supported:

```bash
devagent --workspace "<project_path>"
```

The `--workspace` argument is required. The path must already exist. Relative paths are resolved from the current terminal directory and converted to an absolute path before startup.

## Runtime Files

Inside the target project, the agent stores:

- `<project>/.devagent/logs/` for persisted run logs
- `<project>/.devagent/state.json` for checkpoint and `/resume`

The `/resume` command restores execution from `<project>/.devagent/state.json`.

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
- `<project>/.devagent/` — persisted logs and resume state