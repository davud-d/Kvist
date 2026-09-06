# Kvist

[Русская версия](README.ru.md)

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
  <img alt="Electron GUI" src="https://img.shields.io/badge/Interface-Electron_GUI-47848F?style=flat-square&logo=electron&logoColor=white">
  <img alt="Local workspace agent" src="https://img.shields.io/badge/Agent-local_workspace-6f42c1?style=flat-square&logo=robotframework&logoColor=white">
  <img alt="Four agent roles plus auto" src="https://img.shields.io/badge/Roles-coder_%7C_reader_%7C_devops_%7C_chat-8A2BE2?style=flat-square">
  <img alt="Six built-in providers" src="https://img.shields.io/badge/LLM_providers-6_builtin-0A7EA4?style=flat-square">
  <img alt="Seven prompts" src="https://img.shields.io/badge/System_prompts-7-5C2D91?style=flat-square">
  <img alt="NDJSON protocol" src="https://img.shields.io/badge/Protocol-NDJSON_v1-000000?style=flat-square&logo=json&logoColor=white">
  <img alt="Human in the loop" src="https://img.shields.io/badge/Safety-human--in--the--loop-2EA44F?style=flat-square">
  <img alt="Test suite" src="https://img.shields.io/badge/tests-1500%2B_collected-brightgreen?style=flat-square&logo=pytest&logoColor=white">
  <img alt="JetBrains plugin" src="https://img.shields.io/badge/JetBrains_Plugin-2025.3-000000?style=flat-square&logo=jetbrains&logoColor=white">
</p>

<p align="center">
  <img alt="OpenAI" src="https://img.shields.io/badge/OpenAI-supported-412991?style=flat-square&logo=openai&logoColor=white">
  <img alt="Anthropic" src="https://img.shields.io/badge/Anthropic-supported-D97757?style=flat-square&logo=anthropic&logoColor=white">
  <img alt="DeepSeek" src="https://img.shields.io/badge/DeepSeek-supported-4D6BFE?style=flat-square&logo=deepseek&logoColor=white">
  <img alt="OpenRouter" src="https://img.shields.io/badge/OpenRouter-supported-6467F2?style=flat-square">
  <img alt="OmniRoute" src="https://img.shields.io/badge/OmniRoute-supported-008080?style=flat-square">
  <img alt="FreeFlow LLM" src="https://img.shields.io/badge/FreeFlow_LLM-beta-orange?style=flat-square">
  <img alt="Custom endpoints" src="https://img.shields.io/badge/Custom_endpoints-OpenAI_%7C_Anthropic-555555?style=flat-square">
</p>

Kvist is a supervised local coding agent. It works in one project workspace at
a time, reads and edits project files, runs development or infrastructure
commands, verifies the result, and keeps resumable named sessions.

It is a terminal application first, with an Electron GUI and a versioned NDJSON
frontend protocol for other clients.

## Preview

Kvist has two user-facing entry points. Each preview below shows the same short
read-only task through its full lifecycle: `start` means the interface is ready,
`working` means the agent is actively inspecting the workspace, and `end` means
the answer is complete with no files edited.

### Terminal UI in Kitty

On the first terminal launch, run `kvist` in the project directory. Choose a
provider, then enter its API key at the masked `API key:` prompt. The value is
stored in `~/.kvist/config.json`; it is not echoed in the terminal.

![Kvist terminal ready in Kitty](preview/terminal-start.png)

![Kvist terminal agent working in Kitty](preview/terminal-working.png)

![Kvist terminal completed task in Kitty](preview/terminal-end.png)

### Electron GUI

The Electron frontend uses the same runtime and NDJSON protocol. Select or open
a project, type the task in the composer, and answer any approval or input card
shown in the conversation. Provider credentials are configured by the runtime,
not pasted into the task prompt.

![Kvist GUI ready on Hyprland](preview/gui-start.png)

![Kvist GUI agent working on Hyprland](preview/gui-working.png)

![Kvist GUI completed task on Hyprland](preview/gui-end.png)

## What it does

Kvist routes a request to one of five roles:

- `coder` — edits source files and runs project checks;
- `reader` — investigates and explains a codebase without making changes;
- `devops` — handles services, Docker, CI/CD, and deployment tasks;
- `chat` — answers questions and discusses architecture without touching files;
- `auto` — selects a role from the request (the default).

Every run has a human-in-the-loop approval gate. File writes and risky commands
are visible and may require confirmation. This is a supervised tool, not an IDE
and not a complete security boundary: review the diff and command output.

## Requirements

- Python 3.11 or newer;
- an API key and models for one supported provider;
- a TTY for the interactive terminal configuration and approval prompts.

The CLI is tested on Linux and Windows. macOS is experimental. The optional
JetBrains plugin supports Linux and Windows and targets IntelliJ Platform 2025.3.

Built-in providers are OpenAI, Anthropic, DeepSeek, OpenRouter, OmniRoute, and
FreeFlow LLM (beta). You can also add custom OpenAI-compatible or
Anthropic-compatible endpoints from `/provider`.

## Install from source

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

For development and tests:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
```

On Windows use `py -m venv .venv` and `.venv\\Scripts\\python.exe`.

## Quick start

For Unix-like systems (Linux, FreeBSD, OpenBSD) in a terminal:

```bash
git clone https://github.com/davud-d/Kvist && echo "Clone OK" || echo "error clone"
cd Kvist
python3 -m venv .venv && echo "venv OK" || echo "error venv" # or python -m venv .venv
source .venv/bin/activate && echo "venv activated" || echo "error activate"
uv pip install -r requirements.txt || echo "uv not found" && pip install -r requirements.txt
python installer/install.py && echo "Install OK" || echo "error install"
```
**Note**: *works in bash only; if you use a different shell - adapt it*

For Windows PowerShell:

```powershell
git clone https://github.com/davud-d/Kvist; if ($?) { echo "Clone OK" } else { echo "error clone" }
cd Kvist
python -m venv .venv; if ($?) { echo "venv OK" } else { echo "error venv" }
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force
.venv\Scripts\Activate.ps1; if ($?) { echo "venv activated" } else { echo "error activate" }
uv pip install -r requirements.txt; if (!$?) { pip install -r requirements.txt }
python installer\install.py; if ($?) { echo "Install OK" } else { echo "error install" }
```
**Note**: *works in PowerShell **5.1** only; if you use a different PowerShell version - adapt it*

## Run

The workspace argument is required. If the directory does not exist, Kvist
creates it. Relative paths are resolved from the directory where Kvist starts.

```bash
.venv/bin/python main.py --workspace /path/to/project
```

The first interactive launch asks for the provider, models, and credentials and
saves them in `~/.kvist/config.json` (secrets are not printed). Start a new
terminal in the project and run Kvist again to reuse that configuration.

To install a reusable `kvist` launcher and build a standalone binary when needed:

```bash
.venv/bin/python installer/install.py
```

The `installer/` directory also holds two desktop installers, both cross-platform
(Linux + Windows):

```bash
.venv/bin/python installer/install_jetbrains.py   # install the plugin into every JetBrains IDE found
.venv/bin/python installer/install_gui.py         # install the GUI app + launcher/desktop shortcut
```

The installer uses `dist/kvist` or `dist/kvist.exe` when available, otherwise
builds it with PyInstaller. It installs the binary under the per-user data
directory and a launcher under `~/.local/bin` on Linux or `%LOCALAPPDATA%\\Kvist\\bin`
on Windows. On Windows the installer adds that bin directory to your user `PATH`
automatically (open a new terminal afterwards); on Linux add it yourself if the
installer reports it is missing. Re-run with `--rebuild` after changing the source.

The installed launcher uses the current directory as the workspace:

```bash
cd /path/to/project
kvist
```

Build manually if you need the artifact only:

```bash
.venv/bin/python -m pip install pyinstaller
.venv/bin/python -m PyInstaller kvist.spec
```

The result is `dist/kvist` on Linux or `dist/kvist.exe` on Windows.

## Terminal commands

Type `/` to open command completion. The most useful commands are:

| Command | Purpose |
| --- | --- |
| `/help`, `/commands` | Show help or the complete command list |
| `/provider` | Select or configure a provider |
| `/change-api` | Replace the current provider key (`/change_api` and `/apikey` also work) |
| `/models` | Select `fast`, `smart`, and `planner` models |
| `/role` | Select `auto`, `coder`, `reader`, `devops`, or `chat` |
| `/session` | Create, switch, or delete named sessions |
| `/resume` | Continue the active session's latest checkpoint |
| `/new` | Start a new session; the current one stays available in `/session` |
| `/changes` | Show agent changes recorded in this workspace |
| `/revert` | Restore recorded agent changes when safe |
| `/verbose` | Toggle diagnostic output |
| `/plan` | Toggle the separate planning call (saved globally) |
| `/strime` | Toggle streaming for a custom provider |
| `/bye` | Save and exit (`/exit` and `/quit` are aliases) |

Multiline input: `Alt+Enter` or `Esc+Enter`. The planning stage is enabled by
default; `AGENT_SKIP_PLANNING=1` overrides the saved preference for a launch.

Named sessions have 1–64 lowercase letters, digits, `-`, or `_`, and start with
a letter or digit. Each session stores its own history and checkpoint. A
checkpoint is written after each step, so `/resume` can continue an interrupted
run. The change journal powers `/changes` and `/revert`.

## Headless frontends and scripting

The built-in stdio frontend speaks newline-delimited JSON (NDJSON). Configure a
provider once in the terminal, then launch it from another process:

```bash
.venv/bin/python main.py --workspace /path/to/project --frontend stdio
```

The first event is `hello`; events include plans, status, streamed text,
approval requests, errors, and the final response. Replies to `input_request`
must be JSON objects on stdin. The complete wire contract, including session
operations and resume behavior, is in [`UI_API.md`](UI_API.md).

Useful headless commands:

```bash
# Inspect saved provider/model/credential state without an LLM call
.venv/bin/python main.py --workspace /path/to/project --config-info

# Manage the active workspace's sessions
.venv/bin/python main.py --workspace /path/to/project --session-op status
.venv/bin/python main.py --workspace /path/to/project --session-op create --session-name review
.venv/bin/python main.py --workspace /path/to/project --session-op switch --session-name review

# Read or revert the durable change journal
.venv/bin/python main.py --workspace /path/to/project --journal-op changes
.venv/bin/python main.py --workspace /path/to/project --journal-op revert
```

Per-run stdio overrides include `--provider`, all three model flags
(`--fast-model`, `--smart-model`, `--planner-model`), `--role`, and
`--approval-mode ask|deny`. Model overrides are supplied as a complete set.
`--resume` resumes the active checkpoint without reading a goal line.

## Electron GUI

The GUI starts Kvist as a child process and uses only the stdio protocol; it does
not import Python modules or use HTTP/WebSockets.

```bash
cd gui
npm ci
npm start -- --workspace /path/to/project
```

Run `npm test` for GUI tests. `npm run dist` builds native Linux (`AppImage`,
`deb`) or Windows (`nsis`) packages; build separately on each target OS. See
[`gui/README.md`](gui/README.md) for packaging details.

## Configuration and boundaries

Global settings live in `~/.kvist/config.json`. Runtime environment variables
include `AGENT_LLM_PROVIDER`, `AGENT_FAST_MODEL`, `AGENT_SMART_MODEL`,
`AGENT_PLANNER_MODEL`, `AGENT_VERBOSE`, `AGENT_MAX_STEPS`,
`AGENT_PERSIST_LOGS`, `AGENT_SKIP_PLANNING`, `AGENT_SANDBOX_MODE`, and
`AGENT_SANDBOX_NET`. Prefer `/provider`, `/models`, and `/role` for interactive
configuration; do not commit keys or local `.kvist` data.

Two instruction files sit in the workspace root: `KVIST.md` for the action-loop
roles (coder, reader, devops) and `KVIST-CHAT.md` for chat. Kvist creates both
from a template on first launch and appends their contents to the role system
prompt, so a project states its build commands, layout and conventions once
instead of repeating them in every task; edits take effect from the next step.
If the workspace already has a `.gitignore`, both names are appended to it once
under a comment — without a `.gitignore` nothing is written. Because these files
live in the workspace, their contents are treated as untrusted: they are quoted
into the prompt as delimited data, bounded by instructions that scope them to
commands, layout and conventions and rule out changing the agent's role, safety
rules or action protocol. That raises the cost of a hostile instruction file and
makes the attempt reportable; it is not a guarantee. The gates that do not read
the prompt — command validation, secret masking, the workspace boundary, and the
approval prompts — remain the actual boundary.

Writes and shell commands stay inside the workspace. Read-only actions may use
repeatable `--allow-read DIR` paths. Project command policy is stored at
`.kvist/permissions.json` with `allow`, `deny`, and `ask` patterns; deny and
hard safety blocks always win. Network access is denied by the restricted
sandbox when available, and approval is fail-closed when no TTY/frontend answer
exists.

## Tests and lint

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

## Related docs

- [`UI_API.md`](UI_API.md) — complete frontend protocol;
- [`gui/README.md`](gui/README.md) — Electron development and packaging;
- [`jetbrains-plugin/README.md`](jetbrains-plugin/README.md) — IntelliJ plugin;
- [`LICENSE`](LICENSE) — MIT license.
