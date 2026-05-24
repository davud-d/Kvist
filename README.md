# DevAgentXD

DevAgentXD is a local coding agent for one workspace at a time.

I use it for the kind of repo work that is small but annoying: find where some
setting is loaded, make a narrow patch, explain a dusty file, check why a
dependency upgrade broke. It runs in the terminal, keeps its hands inside the
workspace, and shows progress while it works.

It is not an IDE and not a magic safety box. Read the diff before you trust it.

## Requirements

- Python 3.11+
- OpenAI API key or a FreeFlow-compatible provider key

The JetBrains plugin has a separate build path; see the note at the bottom.

## Setup

From the repo root:

```bash
pip install -r requirements.txt
```

No `.env` is needed for the first run. If `~/.devagent/config.json` is missing,
DevAgentXD asks for the key required by the active provider and stores the
selected provider in the same file.

Example:

```json
{
	"llm_provider": "openai",
	"openai_api_key": "sk-..."
}
```

Config path:

- Linux/macOS: `~/.devagent/config.json`
- Windows: `C:\Users\<USERNAME>\.devagent\config.json`

Old configs with `OPENAI_API_KEY` in that JSON still load.

If you switch to `FreeFlow LLM`, DevAgentXD keeps the selected provider and can
store provider-specific keys such as `gemini_api_key` or `groq_api_key`.
Selected models are also stored in the same config separately for each provider.

## Run

From source:

```bash
python main.py --workspace "<project_path>"
```

With a built binary:

```bash
devagent --workspace "<project_path>"
```

`--workspace` is required. DevAgentXD creates the folder if it is not there. I prefer
passing an absolute path; relative paths work, but only relative to the terminal
you launched from.

Example tasks:

```text
> Find where settings are loaded and explain the flow
```

```text
> Add validation for empty workspace paths and keep the error message short
```

Use `/bye` to exit. Use `/resume` if the last run was interrupted and a
checkpoint exists. Use `/provider` to switch between `OpenAI` and
`FreeFlow LLM` with the arrow keys. Use `/models` to select `fast`, `smart`,
and `planner` models for the current provider the same way.

Multiline input is a bit old-school: end the line with `\`.

```text
> Refactor the parser so that \
... validation errors include the action type
```

## Preview

### First launch

When DevAgentXD asks for the API key, typed characters are invisible. That is
expected; keep typing and press Enter.

![DevAgentXD first launch with hidden API key input](preview/start.png)

### Agent at work

The agent prints what it is doing while it inspects files, thinks through the
next step, and applies small changes.

![DevAgentXD working in the terminal](preview/working.png)

### Final report

At the end, DevAgentXD summarizes what happened: actions, reasoning notes, and
the result the user should check.

![DevAgentXD final report in the terminal](preview/end.png)

## Knobs worth knowing

```env
AGENT_LLM_PROVIDER=openai
AGENT_FAST_MODEL=gpt-5-mini
AGENT_SMART_MODEL=gpt-5
AGENT_PLANNER_MODEL=gpt-5
AGENT_MAX_STEPS=20
AGENT_PERSIST_LOGS=1
AGENT_VERBOSE=0
```

`AGENT_MAX_STEPS` is the one I change most often. Raise it when the agent keeps
stopping one or two steps too early. `AGENT_VERBOSE=1` is useful when debugging
DevAgentXD itself, and noisy for normal use.

Provider notes:

- `OpenAI` remains the safest option for strict JSON planner/action steps.
- For `OpenAI`, the defaults now prefer the `GPT-5` family, with automatic fallback
	to `GPT-4.1` if the API key does not have `GPT-5` access.
- `FreeFlow LLM` is integrated as an optional provider for Gemini/Groq models.
- `FreeFlow LLM` does not guarantee the same native JSON mode as the official
	OpenAI SDK, so DevAgentXD falls back to plain completions there.
- For `FreeFlow LLM`, all default roles now use `gemini-3.1-pro-preview`.
- **⚠️ `FreeFlow LLM` is currently in beta testing and remains very unstable.**
  Expect intermittent failures, timeouts, and inconsistent responses. Use
  `OpenAI` if you need reliable results.

`AGENT_PERSIST_LOGS=0` stops run logs, but `/resume` still uses
`<project>/.devagent/state.json`.

Web search is deliberately limited. It is meant for external libraries, docs,
release notes, pricing, limits, and other things that can go stale.

```env
SEARCH_PROVIDER=auto
SEARXNG_BASE_URL=
TAVILY_API_KEY=
BRAVE_SEARCH_API_KEY=
```

## Files it creates

Inside the target project:

- `<project>/.devagent/logs/`
- `<project>/.devagent/state.json`

The API key stays in your home directory config, not in the project.

## Sharp edges

File access is kept inside the workspace. Shell commands go through an allowlist.
There are size limits, output limits, and URL fetch blocks for obvious binary
stuff.

Still, this is a tool for supervised work. It can misunderstand the task or make
the wrong edit confidently. Best workflow so far: ask for one contained change,
inspect, then continue.

Some internal error messages are still ugly. The runtime got attention first;
the polish can wait.

## Project layout

Core:

- `main.py` starts the CLI and wires workspace, key loading, models, and UI.
- `agent.py` glues the agent pieces together.
- `agent_runtime.py` runs the step loop and checkpoints progress.
- `agent_state.py` holds `AgentConfig` and runtime dataclasses (`PlanInfo`, etc.).
- `agent_model.py` calls the action LLM, handles streaming, token counting, and model fallback.
- `agent_planner.py` runs the optional planning step before the main action loop.
- `agent_context.py` builds the per-step context prompt with localized labels.

Execution and parsing:

- `executor.py` handles workspace-safe file, shell, git, and web actions.
- `parser.py` and `protocol.py` validate model actions.
- `terminal.py` has the shell command allowlist.
- `agent_web.py` guards web search and URL reads.

LLM and provider wiring:

- `llm_clients.py` configures OpenAI and FreeFlow clients, model lists, and fallback chains.
- `user_config.py` reads/writes `~/.devagent/config.json`, handles key prompts and provider selection.

Utilities:

- `context_utils.py` sliding-window context trimmer for message history.
- `render_throttle.py` non-blocking render throttle for streaming chunk output.
- `language_utils.py` detects the user's request language and picks localized strings.
- `deps.py` scans workspace dependency files for pinned versions.
- `performance_logger.py` structured JSONL debug logger for `.devagent/logs/debug/`.

UI and packaging:

- `ui.py` renders terminal output.
- `agent_logging.py` writes run logs and checkpoints.
- `package_script.py` PyInstaller build helper.

Extras:

- `system_prompt.txt` action-model system prompt.
- `planner_prompt.txt` planner-model system prompt.

`jetbrains-plugin/` contains an IntelliJ Platform plugin that launches DevAgentXD in
the IDE terminal for the current project. It is pinned to IntelliJ Platform
2025.3, supports Windows/Linux, and expects built binaries under
`jetbrains-plugin/bin/` before packaging.
