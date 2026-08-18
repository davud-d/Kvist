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
  <img alt="Local workspace agent" src="https://img.shields.io/badge/Agent-local_workspace-6f42c1?style=flat-square&logo=robotframework&logoColor=white">
  <img alt="Three agent roles" src="https://img.shields.io/badge/Roles-coder_%7C_devops_%7C_chat-8A2BE2?style=flat-square">
  <img alt="Six providers" src="https://img.shields.io/badge/LLM_providers-6-0A7EA4?style=flat-square">
  <img alt="Six prompts" src="https://img.shields.io/badge/System_prompts-6-5C2D91?style=flat-square">
  <img alt="Strict JSON protocol" src="https://img.shields.io/badge/Protocol-strict_JSON-000000?style=flat-square&logo=json&logoColor=white">
  <img alt="Human in the loop" src="https://img.shields.io/badge/Safety-human--in--the--loop-2EA44F?style=flat-square">
  <img alt="Test suite" src="https://img.shields.io/badge/tests-1000%2B_offline-brightgreen?style=flat-square&logo=pytest&logoColor=white">
  <img alt="JetBrains plugin" src="https://img.shields.io/badge/JetBrains_Plugin-2025.3-000000?style=flat-square&logo=jetbrains&logoColor=white">
</p>

<p align="center">
  <img alt="OpenAI" src="https://img.shields.io/badge/OpenAI-supported-412991?style=flat-square&logo=openai&logoColor=white">
  <img alt="Anthropic" src="https://img.shields.io/badge/Anthropic-supported-D97757?style=flat-square&logo=anthropic&logoColor=white">
  <img alt="OpenRouter" src="https://img.shields.io/badge/OpenRouter-supported-6467F2?style=flat-square">
  <img alt="OmniRoute" src="https://img.shields.io/badge/OmniRoute-supported-008080?style=flat-square">
  <img alt="FreeFlow LLM" src="https://img.shields.io/badge/FreeFlow_LLM-beta-orange?style=flat-square">
  <img alt="Custom endpoints" src="https://img.shields.io/badge/Custom_endpoints-OpenAI_%7C_Anthropic-555555?style=flat-square">
</p>

## Preview

### First launch

API key input is intentionally hidden.

![Kvist first launch with hidden API key input](preview/start.png)

### Agent at work

![Kvist working in the terminal](preview/working.png)

### Final report

![Kvist final report in the terminal](preview/end.png)

Kvist is a local terminal coding agent that works with one workspace at a time.
It can inspect and edit project files, run development and infrastructure
commands, verify changes, search current technical documentation, and keep
separate named sessions for a project.

Kvist routes every request to one of three roles:

- `coder` works with source code and project files through a strict JSON action
  protocol.
- `devops` handles deployment, services, Docker, CI/CD, and other system tasks
  with role-aware command approval.
- `chat` answers questions and discusses architecture without touching files or
  running commands.

It is a supervised tool, not an IDE or a complete security boundary. Review the
diff and command output before trusting a result.

## Requirements

- Python 3.11+
- An API key or endpoint for at least one supported LLM provider
- A terminal with TTY support for interactive provider, model, role, session,
  and security prompts

The CLI supports Windows and Linux. macOS is present in the runtime checks but
remains untested/experimental. The JetBrains plugin supports Windows and Linux
only.

The JetBrains plugin has additional build requirements; see
[`jetbrains-plugin/README.md`](jetbrains-plugin/README.md).

## Install

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

For development and tests, install the declared developer requirements:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
```

On a minimal Linux image where `venv` reports that `ensurepip` is unavailable,
you can bootstrap pip only inside the project environment with the official
PyPA fallback:

```bash
python3 -m venv --without-pip .venv
curl --proto '=https' --tlsv1.2 --fail --silent --show-error \
  https://bootstrap.pypa.io/get-pip.py | .venv/bin/python -
.venv/bin/python -m pip install -r requirements-dev.txt
```

PyInstaller is only needed when building a standalone binary:

```bash
.venv/bin/python -m pip install pyinstaller
.venv/bin/python -m PyInstaller kvist.spec
```

The resulting executable is `dist/kvist` on Linux or `dist/kvist.exe` on
Windows.

## Run

From source:

```bash
.venv/bin/python main.py --workspace "<project_path>"
```

With a built binary:

```bash
kvist --workspace "<project_path>"
```

`--workspace` is required. Kvist resolves the path,
creates the directory if it does not exist, and then confines its file actions
to that workspace. Absolute paths are the least surprising; relative paths are
resolved from the directory where Kvist was started.

Use `--frontend stdio` for the versioned NDJSON frontend protocol. Configure the
provider and credentials once in the interactive terminal before starting a
headless frontend.

On Windows, create the environment with `py -m venv .venv` and use
`.venv\Scripts\python.exe` in place of `.venv/bin/python`.

Example requests:

```text
> Find where settings are loaded and explain the flow
```

```text
> Add validation for empty workspace paths and run the relevant tests
```

Use `Alt+Enter` or `Esc+Enter` for multiline input. Slash-command completion
appears as soon as the input starts with `/`.

### Electron GUI

```bash
cd gui
npm ci
npm start -- --workspace "<project_path>"
```

The GUI starts Kvist as a child process with `--frontend stdio`. It does not
import Python modules or use HTTP, REST, or WebSocket. Packaging instructions
are in `gui/README.md`. Its prompt bar exposes provider/model overrides, agent
role, and fail-closed `ask`/`deny` approval modes for each run. Every run
executes in the workspace's active named session; the `hello` event reports
that session and whether a resumable checkpoint exists. Frontends manage
sessions through `kvist --session-op ...` and resume checkpoints with
`kvist --frontend stdio --resume` (see `UI_API.md`); the GUI does not yet
expose a session picker of its own.

## Interactive commands

| Command | What it does |
| --- | --- |
| `/help` | Show Kvist information and platform support. `/about` and `/info` are also accepted. |
| `/new` | Clear the active session history and checkpoint, keeping the current session name. |
| `/provider` | Switch provider or create a custom provider. |
| `/models` | Select the `fast`, `smart`, and `planner` models for the active provider. |
| `/role` | Select `auto`, `coder`, `devops`, or `chat`. The choice is saved globally. |
| `/session` | Create, switch, or delete named workspace sessions. The active session cannot be deleted. |
| `/resume` | Continue the checkpoint stored in the active session. |
| `/changes` | Show a diff of files the agent changed in this workspace (all sessions) since the change journal was last cleared. |
| `/revert` | Restore agent-changed files to their recorded pre-change contents. Files later modified by you (or by shell commands) are kept and reported as skipped. |
| `/verbose <true\|false>` | Enable or disable verbose runtime output without restarting Kvist. |
| `/strime <true\|false>` | Enable or disable streaming for the active custom provider. |
| `/bye` | Save the session and exit. `/exit` and `/quit` are aliases. |

### Verbose mode

After you type `/verbose`, Kvist displays a grey inline suggestion in angle
brackets. It suggests `<true>` when verbose mode is disabled and `<false>` when
verbose mode is enabled. Press `Tab` to insert the suggested value:

```text
/verbose <true>
```

You can also enter `/verbose <true>` or `/verbose <false>` manually. Entering
`/verbose` without an argument toggles the current state. The change applies to
the active runtime immediately; after a restart, the initial value is read from
`AGENT_VERBOSE` again.

### Custom-provider streaming

Custom providers use non-streaming requests by default. When a custom provider
is active, typing `/strime` shows `<true>` if streaming is disabled or `<false>`
if it is enabled. Press `Tab` to insert the suggested value:

```text
/strime <true>
```

You can also enter `/strime <true>` or `/strime <false>` manually. Entering
`/strime` without an argument toggles the current state. The value is saved in
the active custom-provider configuration and applied to the current agent
immediately. The command is rejected for built-in providers.

Session names contain 1-64 lowercase letters, digits, hyphens, or underscores
and must start with a letter or digit. Kvist shows the last few tasks when a
session is loaded and keeps up to ten completed task summaries in its context.

### Session lifecycle contract

- Each named session owns its own conversation history, chat history, and
  resumable execution checkpoint; nothing leaks between sessions.
- The checkpoint is saved after every step, so an interrupted task (Ctrl+C,
  crash, `kill`) resumes from its last completed step with `/resume`.
- Every externally visible action is recorded durably *before* it runs. For a
  file write, if Kvist dies in the write window, `/resume` reconciles the
  intended write against the file's content (via content hashes) instead of
  blindly replaying it: it continues past a write whose content is present and
  retries one that never happened. If the outcome is genuinely ambiguous (the
  file is neither the old nor the intended content), `/resume` stops and asks
  you to inspect the file rather than risk applying a change twice — the
  checkpoint is preserved so you can retry.
- Actions whose effect cannot be proven from the workspace after a crash —
  `run_shell` and `delegate_task` (they may deploy, send, or build, and their
  output is lost in the crash) — are **never replayed**. `/resume` reports them
  as ambiguous and asks you to verify their effect. A safe refusal is
  intentional: Kvist does not guess whether an irreversible command already ran.
- Recovering "the intended content is present" is not the same as "the agent
  wrote it": if Kvist cannot prove authorship after a crash, it still continues
  (without re-writing) but does not grant `/revert` ownership of that content,
  so a change you made yourself is never destroyed.
- `/resume` operates only on the active session; completing a task clears only
  that session's checkpoint.
- Switching sessions saves the outgoing session first and fails without
  touching it if the target is missing or corrupt. Switching never creates a
  session and never rewrites workspace files — conversation state and
  workspace rollback are separate concepts.
- `/new` resets the active session in place (history, chat, checkpoint); other
  named sessions and workspace change tracking are untouched.
- The active session cannot be deleted; deleting an inactive session removes
  both its file and its recovery copy.
- A restart restores the last active session. If the active-session marker is
  damaged, Kvist falls back to the most recent valid session and says so; a
  corrupt session file fails loudly instead of being treated as empty.

### Workspace change tracking

The change journal (`.kvist/change_journal.json`) is workspace-global: all
sessions share one working tree, so `/changes` and `/revert` cover the agent's
file edits from every session, with per-file session attribution in the diff
report. The journal records each file's pre-change contents *before* the file
is mutated, so a crash between the two steps can never lose rollback data (the
"last successful write" hash advances only after the write returns, so a failed
write never makes the previous agent content unrevertable). `/revert` only
overwrites content whose hash matches the agent's own last successful write —
files you edited afterwards, and pre-existing files you deleted, are skipped and
keep their baselines in the journal. Restores are atomic, so an interrupted
`/revert` never leaves a half-written file and can simply be re-run. `/new` does
not reset change tracking; only `/revert` clears the entries it restores.

Because the journal is the *safety* record for rollback, it is not best-effort:
if it is corrupt, a reversible agent write is **refused before the file is
touched** (so the pre-change content is never lost with no way to recover it),
and `/changes`/`/revert` report the corruption instead of pretending history is
empty. If a write reaches disk but its journal commit cannot be confirmed
(a partial commit), the action is reported as failed rather than a clean
success, and the content is treated as unowned so `/revert` will not touch it.

## How a request is processed

In `auto` role mode, deterministic rules handle clear requests first. Ambiguous
requests are classified by the `fast` model through
`prompts/router_prompt.txt`; a
keyword router is the fallback when that model call fails. A manual `/role`
selection bypasses automatic routing.

For `coder` and `devops` tasks, Kvist then:

1. Uses the `planner` prompt with the configured `fast` model to build a short
   plan, success criteria, risks, and a simple/complex classification. A local
   fallback plan is used if planning fails.
2. Chooses the `fast` action model for simple work or the `smart` model for
   complex work and recovery from errors.
3. Runs a step loop with the role prompt, strict action parsing, read-before-edit
   and verification guards, progress tracking, and checkpoints.
4. Either returns a short `final_answer` or passes a structured
   `report_to_user` result to the reporter prompt for a natural final report.

The runtime context includes the OS, shell, current time, request language,
known dependency versions, session history, plan state, changed files, recent
tool results, verification evidence, and completion blockers.

`chat` uses a lighter path: the model responds directly in Markdown, retains a
short chat history, and has no file or terminal actions.

## Providers and models

The configured `fast`, `smart`, and `planner` roles are model tiers, not agent
roles. Model environment variables take precedence over saved model selections;
built-in defaults are used only when no override or saved choice exists.

In the current implementation, routing, planning, chat, and report rendering
use the `fast` model. The `smart` model runs complex coder/devops action loops.
The `planner` selection is saved and displayed, but `agent_planner.py` does not
consume it yet.

| Provider | Current defaults | Behavior |
| --- | --- | --- |
| OpenAI | `gpt-5.4-mini` for all three model roles | Streaming and native JSON mode. The configured models are checked against models accessible to the API key and can fall back within the OpenAI list. |
| Anthropic | `claude-haiku-4-5` for `fast`; `claude-sonnet-4-6` for `smart` and `planner` | Native Anthropic Messages API adapter with streaming; JSON is enforced by the prompt/parser rather than `response_format`. |
| OpenRouter | No automatic defaults | Choose from the curated list or enter an exact `provider/model` ID. Streaming and native JSON mode are enabled. |
| OmniRoute | No automatic defaults | Enter exact dynamic model IDs manually. Uses an OpenAI-compatible local endpoint; streaming and native JSON mode are disabled by the current integration. |
| FreeFlow LLM | `gemini-3.5-flash` for all three roles | Gemini/Groq integration, non-streaming, best-effort JSON parsing. The required key depends on the selected model backend. This provider is still beta and may be unstable. |
| Custom | No automatic defaults | A saved OpenAI- or Anthropic-compatible endpoint with manual model IDs and an optional API key. |

OpenAI remains the safest built-in choice for strict JSON action steps. Provider
and model availability can change upstream; the table above describes the IDs
and behavior currently encoded in this repository.

### First-run configuration

On the first interactive run, Kvist asks for a provider, any missing model IDs,
and the credentials required by the selected models. Secret input is masked.
Selections and credentials are stored in:

- Linux/macOS: `~/.kvist/config.json`
- Windows: `C:\Users\<USERNAME>\.kvist\config.json`

The file is plain JSON in the user's home directory. Do not commit or share it.
Legacy configs with `OPENAI_API_KEY` inside the JSON are still accepted.

Example:

```json
{
  "llm_provider": "anthropic",
  "anthropic_api_key": "...",
  "role_selection": "auto",
  "llm_models": {
    "anthropic": {
      "fast": "claude-haiku-4-5",
      "smart": "claude-sonnet-4-6",
      "planner": "claude-sonnet-4-6"
    }
  }
}
```

Recognized credential variables are `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, and
`OMNIROUTE_API_KEY`. The corresponding base URL variables are
`ANTHROPIC_BASE_URL`, `OPENROUTER_BASE_URL`, and `OMNIROUTE_BASE_URL`.

### OpenRouter

The default endpoint is `https://openrouter.ai/api/v1`. OpenRouter has no
automatic model defaults because model availability is external. During setup
or `/models`, choose a curated model or enter an exact ID such as:

```text
openai/gpt-5.4-mini
anthropic/claude-sonnet-4-6
google/gemini-3.5-flash
```

### OmniRoute

The default endpoint is `http://localhost:20128/v1`. Start OmniRoute, copy the
API key from its Dashboard → Endpoints page, select OmniRoute with `/provider`,
and provide a model ID for each role. IDs may use `provider/model`, a router or
combo name without spaces, or the explicit value `auto`.

Example configuration:

```json
{
  "llm_provider": "omniroute",
  "omniroute_api_key": "...",
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

If the endpoint is unavailable, verify that OmniRoute is running and that the
base URL ends in `/v1`. Authentication errors normally point to the Dashboard
key; 404 and 400 responses usually indicate an incorrect model ID, endpoint, or
upstream provider configuration.

### Custom compatible endpoints

Choose `+ Create custom provider...` in `/provider`. The wizard asks for:

- a lowercase provider ID;
- an `http://` or `https://` base URL;
- the OpenAI-compatible or Anthropic-compatible protocol;
- an optional API key and display name;
- one model ID for every tier or separate IDs for each tier.

Custom providers are saved under `custom_providers` and selected as
`custom:<id>`. For example:

```json
{
  "llm_provider": "custom:local-vllm",
  "custom_providers": {
    "local-vllm": {
      "label": "Local vLLM",
      "protocol": "openai",
      "base_url": "http://localhost:8000/v1",
      "api_key": "",
      "models": {
        "fast": "local-model",
        "smart": "local-model",
        "planner": "local-model"
      }
    }
  }
}
```

## Runtime settings

Useful environment variables and their current defaults:

```env
AGENT_LLM_PROVIDER=openai
AGENT_FAST_MODEL=gpt-5.4-mini
AGENT_SMART_MODEL=gpt-5.4-mini
AGENT_PLANNER_MODEL=gpt-5.4-mini
AGENT_MAX_STEPS=50
AGENT_PERSIST_LOGS=1
AGENT_VERBOSE=0
AGENT_API_INTERVAL=
AGENT_SANDBOX_MODE=auto
AGENT_SANDBOX_NET=deny
AGENT_CHILD_ENV_ALLOW=       # comma-separated non-secret child variables
AGENT_TOOL_PATHS=            # pathsep-separated absolute toolchain dirs
AGENT_TERMINAL_PROFILE=dev
AGENT_TERMINAL_TIMEOUT=15
AGENT_TERMINAL_MAX_OUTPUT=12000
AGENT_WRITE_APPROVAL=1
AGENT_RLIMIT_CPU=          # dev: 120, restricted: 8 (seconds)
AGENT_RLIMIT_AS_MB=        # dev: 2048, restricted: 256
AGENT_RLIMIT_NPROC=        # dev: 4096, restricted: 256
AGENT_FETCH_EXTRA_DOMAINS= # comma-separated extra fetch_url domains
```

- `AGENT_MAX_STEPS` limits the coder/devops action loop.
- `AGENT_PERSIST_LOGS=0` disables completed run logs but does not disable
  session files or `/resume` checkpoints.
- `AGENT_VERBOSE=1` prints model selection, retries, and other diagnostics.
- `AGENT_API_INTERVAL` overrides the delay between API calls. Without it,
  OpenAI, Anthropic, and OpenRouter use 2.5 seconds; other providers use the
  configured 5-second default.
- `AGENT_TERMINAL_PROFILE=restricted` hard-blocks non-probe interpreter,
  package-manager, build-tool, and shell execution. Exact trusted version probes
  remain available.
- Child processes receive a constructed environment, not the full parent
  environment. `AGENT_CHILD_ENV_ALLOW=PROJECT_MODE,FEATURE_FLAG` explicitly
  passes additional non-secret, non-loader variables; the same names may then be
  used in `env_overrides`. Secret and execution-control names remain blocked.
  Every terminal run also gets a fresh, private, initially empty child `HOME`.
- The child `PATH` prepends existing project-local tool directories (`.venv/bin`,
  `venv/bin`, `node_modules/.bin`) inside the workspace, then validated
  `AGENT_TOOL_PATHS` entries, then trusted system directories. Each
  `AGENT_TOOL_PATHS` entry must be an absolute, existing,
  non-group/world-writable narrow directory outside the workspace and
  sensitive host trees; it is read-only mounted into Bubblewrap. Your full `HOME`,
  `.local/bin`, nvm, pyenv, and cargo directories are never added automatically.
  Standard Python virtualenvs work: a `.venv/bin/python` symlinking to a trusted
  system interpreter runs with the correct `sys.prefix` and site-packages.
- Terminal timeouts are clamped to 1-300 seconds in the `dev` profile and
  1-30 seconds in `restricted`; captured output is clamped to 1,000-200,000
  characters.
- Unix child processes get profile-based resource limits (CPU, address space,
  process count, file size, open files). `AGENT_RLIMIT_CPU`,
  `AGENT_RLIMIT_AS_MB`, and `AGENT_RLIMIT_NPROC` override the profile defaults.
- `AGENT_WRITE_APPROVAL=0` disables the interactive diff-preview approval that
  otherwise guards overwriting existing files (see "Safety model"). Use it for
  CI and other non-interactive runs; with the gate enabled and no TTY,
  overwrites fail deterministically.

## Web search

Web access is intended for current external documentation, API references,
release notes, migration guides, prices, and limits. It is not used when the
answer is already in the workspace. Search and fetched content are marked as
untrusted before being added to model context.

```env
SEARCH_PROVIDER=auto
SEARXNG_BASE_URL=
TAVILY_API_KEY=
BRAVE_SEARCH_API_KEY=
WEB_SEARCH_TIMEOUT=8
WEB_SEARCH_MAX_RESULTS=5
```

The automatic fallback order is SearXNG → Tavily → Brave. Setting
`SEARCH_PROVIDER` to one of `searxng`, `tavily`, or `brave` tries that provider
first and then the remaining configured providers. Search timeouts are clamped
to 1-30 seconds and result limits to 1-20.

`fetch_url` accepts only HTTP(S), limits response size, rejects obvious binary
formats, and only allows general fetching from the runtime domain allowlist.
The allowlist covers common official documentation sites (Python, MDN, Go,
Rust, Kubernetes, Docker, nginx, GitHub, and others) and can be extended with
`AGENT_FETCH_EXTRA_DOMAINS=example.dev,docs.internal.io`. Fetched content is
always marked untrusted regardless of the domain.

## Files created in a workspace

Kvist stores project-local state under `.kvist/`:

```text
<workspace>/.kvist/
├── active_session.json
├── change_journal.json
├── sessions/
│   └── <name>.json
└── logs/
    ├── run_001.json
    └── debug/logs.jsonl
```

Session documents, the active-session marker, and the change journal are
written atomically with a durable recovery copy (`*.json.bak`), private file
modes, and credential redaction. Frontends must never touch these files
directly — headless session management goes through `--session-op` (see
`UI_API.md`).

`logs/` appears only when run logging is enabled and a run is persisted.
Performance JSONL logging is disabled in code by default. API keys remain in
the home-directory config, not in the workspace.

## Safety model

Kvist uses a non-bypassable authorization pipeline and selects an OS sandbox by
default on Linux when a trusted Bubblewrap installation is available:

- File actions resolve paths inside the workspace and reject symlink escapes.
- `.env` files, internal `.kvist` state, secret material, oversized files, and
  non-UTF-8 edit targets are guarded. Shell arguments that point at secret
  paths (`.env*`, `.ssh`, `.aws`, `.gnupg`, key/certificate files,
  `credentials`) are rejected for every command, including read-only ones like
  `cat` and `grep`, on both relative and absolute paths.
- Under Bubblewrap the secret guard is enforced by the OS, not just argv
  scanning: before launch Kvist enumerates protected workspace files/directories
  and masks them inside the sandbox (files shadowed by read-only `/dev/null`,
  directories replaced by empty tmpfs). Arbitrary child code — Python, Node,
  shell redirection, AWK/sed constructing the path at runtime, compiled helpers,
  symlinks — reads an empty/permission-denied object instead of the secret. The
  protected-set digest is bound to the approval, so adding a secret between
  approval and spawn invalidates it.
- Shell commands pass a platform-aware allowlist and a role-aware risk policy.
  Anything that is not read-only — interpreters and build runners (`python`,
  `node`, `make`, `cargo`, `go`, `awk`), package installs, `git push`,
  unknown commands — requires human approval. Eligible SAFE/MODERATE commands
  may use a project allow-rule; intrinsic DANGEROUS commands may not. Without a
  TTY, required approval is denied.
- Hard denials are distinct from approval-required commands. Forbidden
  executables, workspace escapes, secret/internal paths, shell substitution,
  and dangerous `find`/AWK/sed forms never prompt, cannot be project-allowed,
  and never reach process creation.
- Exact version probes are executable-specific and compare the complete argv.
  A path-qualified executable cannot inherit a trusted command's SAFE status,
  and executable lookup/target identities are bound to authorization.
- `find` action predicates (`-delete`, `-exec`, `-ok`, `-fprintf`, ...) are
  rejected; pure searches stay approval-free. `awk system()`/piped `getline`
  and `sed` in-place/execute/write forms are rejected.
- Read-only Git commands use direct `git SUBCOMMAND` syntax and are launched
  with filesystem-monitor/index-hook, external-diff/textconv, implicit signature
  verification, remerge-driver, and recursive-submodule helper paths disabled.
  Git global options before the subcommand fail closed; status/diff also fail
  closed on repository filter/include configuration.
- Docker daemon reads (including logs and inspect) require approval because
  host daemon state can contain secrets. Docker run/create/build/buildx/service forms with host mounts,
  `--privileged`, insecure build entitlements, or host namespaces
  (`--network=host`, `--pid=host`, ...), host-port publication, image push, and
  nested destructive operations are graded dangerous for every role.
- Destructive and privilege-escalation executables such as `rm`, `sudo`, `dd`,
  `mkfs`, `shutdown`, and `reboot` are forbidden by normal validation.
- Overwriting an existing file (`write_file`, `append_file`,
  `replace_in_file`) shows a unified diff and asks for approval; creating a
  new file does not. `AGENT_WRITE_APPROVAL=0` disables the gate.
- Shell operators and command substitution are restricted, working directories
  stay inside the workspace, and Unix child processes receive profile-based
  resource limits including a fork-bomb brake (`RLIMIT_NPROC`).
- Executable scripts with a `#!` line have their interpreter attested: the
  shebang is parsed (direct, `/usr/bin/env NAME`, `env -S NAME ARGS`), the
  interpreter is resolved with the sandboxed PATH, must be a trusted system
  binary or a workspace file, and its identity/content is bound to the approval.
- The whole process tree is supervised. A command that times out or completes
  cannot leave detached (`setsid`) descendants running: under Bubblewrap the PID
  namespace reaps them, and a cgroup v2 scope tears down the subtree on timeout
  and on completion. Output is captured with bounded in-memory buffers while the
  process runs (no unbounded buffering, no blocking second drain), so a runaway
  child cannot exhaust the parent's memory.
- Before process creation, Kvist repeats hard validation and compares the exact
  argv, normalized cwd and directory identity, role, policy state, sandbox and
  network mode, sanitized environment digest, executable identities, and hashes
  of directly selected scripts/build-control files. A changed input invalidates
  the approval.
- Logs redact common token formats and truncate large values.

### Project permission policy

`<workspace>/.kvist/permissions.json` holds per-project rules, similar to
`.claude/settings.json` in Claude Code:

```json
{
  "allow": ["git status", "python *"],
  "deny": ["pip install *"],
  "ask": ["npm run"]
}
```

Patterns are token-prefix matches with shell-style globbing per token
(`python *` covers any Python argv). Executable aliases are also checked in
canonical form. Resolution order is: project `deny` → hard validation →
intrinsic `DANGEROUS` → project `ask` → executable-origin/automatic-fallback
approval → eligible project `allow` → default risk behavior. `DANGEROUS`
commands always need confirmation for that execution and are never offered as
"always allow". `allow` can suppress prompts only for eligible SAFE/MODERATE
commands; it cannot unlock `rm`, secret reads, sandbox violations, or similar
hard blocks. Overlapping rules resolve to `deny`, and every pipeline segment
must pass. The agent itself cannot edit the policy because `.kvist/` is blocked
from model-driven file actions. Malformed or symlinked policy files fail closed.

`AGENT_SANDBOX_MODE=auto` selects a trusted system `bwrap` on Linux. The sandbox
mounts only the workspace plus required system runtime/library paths; it does
not bind the host root, `/home`, `/root`, unrelated repositories, or credential
directories. `/tmp` is ephemeral, `HOME` is an empty directory, `.kvist` and
every protected workspace secret are masked, process namespaces are isolated,
and networking is unshared by default. Set `AGENT_SANDBOX_NET=allow` only when
the command needs network access.

If Bubblewrap is unavailable in `auto`, Kvist uses a visibly restricted
workspace fallback: interpreters, scripts, build/package tools, and unknown
arbitrary-code executables require confirmation on every execution, even when a
project allow-rule matches, and are hard-denied outright if no cgroup v2 scope
is available to control detached descendants. Explicit
`AGENT_SANDBOX_MODE=workspace` is a weaker user-selected mode in which approved
children retain the current user's host permissions — it is warned and provides
**no** secret masking and **no** `.kvist` isolation, so use it only when you
accept running project code with your own permissions. These weaker modes still
use a new private, verified-empty child `HOME` for every run. Explicit
`bubblewrap` with no trusted binary blocks shell execution instead of degrading.

### Current boundary limits

- Workspace mode is not OS isolation. The automatic fallback also permits its
  small read-only command set to see non-secret host paths allowed by command
  validation.
- Direct scripts and common build-control files are hashed, but transitive
  imports, `python -m` modules, Make includes, package-script sources, and other
  dependency graphs are not fully attested.
- The child PATH prepends existing project-local tool directories and validated
  `AGENT_TOOL_PATHS`, then fixed system locations (not `/usr/local`), but
  transitive helpers launched by trusted system tools are not individually
  content-attested.
- Secret-path checks cover named and canonical direct arguments, and under
  Bubblewrap protected files/directories are OS-masked so runtime-constructed
  paths cannot reach them. Repository/history-aware tools can still surface
  protected content not named in argv. Strong Bubblewrap prevents access to host
  credential trees and masks workspace secrets, but in explicit `workspace` mode
  neither the secret mask nor `.kvist` isolation applies.
- A narrow filesystem race remains between the final identity/hash comparison
  and the OS process-creation call. Detached-descendant cleanup relies on the
  Bubblewrap PID namespace or a cgroup v2 scope; in explicit `workspace` mode
  without cgroup v2, and on Windows, timeout cleanup cannot guarantee termination
  of every descendant process.

## Prompt and action architecture

The active prompt files are:

- `prompts/router_prompt.txt` — selects `coder`, `devops`, or `chat` for
  requests not handled by deterministic routing.
- `prompts/planner_prompt.txt` — returns the task plan, success criteria, risks,
  and complexity as JSON.
- `prompts/coder_prompt.txt` — strict coding and workspace action protocol.
- `prompts/devops_prompt.txt` — strict DevOps protocol with system-awareness and
  human approval rules.
- `prompts/chat_prompt.txt` — direct Markdown conversation with no tools.
- `prompts/report_prompt.txt` — turns a structured completed-task result into a
  concise user-facing report.

Coder and DevOps can emit these validated actions:

```text
final_answer, report_to_user,
read_file, read_multiple_files, write_file, append_file, replace_in_file,
list_dir, list_tree, find_files, search_code,
git_status, git_diff, run_shell,
web_search, fetch_url, delegate_task
```

`parser.py` enforces the exact schema; `executor.py` implements the actions;
`agent_runtime.py` blocks premature completion, read-only violations, repeated
dead ends, and unverified edits.

## Project layout

Core runtime:

- `main.py` starts the REPL and handles slash commands and interactive setup.
- `agent.py` coordinates routing, planning, chat, sessions, and the action loop.
- `agent_runtime.py` executes steps, tracks evidence, and renders final reports.
- `agent_state.py` defines configuration, plan, and run state.
- `agent_context.py` builds and priority-trims the per-step runtime context.
- `agent_model.py` loads role prompts, selects model tiers, streams responses,
  retries API calls, and handles JSON-mode fallback.
- `agent_planner.py` runs the planner and supplies a local fallback plan.

Providers and routing:

- `llm_clients.py` defines providers, model lists, defaults, validation, adapters,
  and fallback candidates.
- `user_config.py` reads and writes `~/.kvist/config.json` and custom providers.
- `role_router.py` combines deterministic, LLM, keyword, and default routing.
- `language_policy.py` and `language_utils.py` keep user-facing output in the
  request language.

Execution and persistence:

- `executor.py`, `terminal.py`, `command_policy.py`, and `sandbox.py` implement
  actions, shell validation, approvals, risk grading, and optional bubblewrap.
- `parser.py` and `protocol.py` define the strict action protocol.
- `agent_logging.py` stores named sessions, checkpoints, and run logs.
- `agent_web.py` limits when web actions may be used.
- `deps.py` detects dependency versions from Python project and lock files.
- `ui.py` and `render_throttle.py` render the terminal interface efficiently.

Packaging and integrations:

- `kvist.spec` is the PyInstaller definition and bundles `prompts/`.
- `package_script.py` creates a source archive from Git-visible files.
- `frontend_protocol.py`, `event_stream_ui.py`, `event_session.py`, and
  `event_stdio.py` implement the versioned NDJSON frontend boundary.
- `gui/` contains the Electron frontend and its bridge tests.
- `jetbrains-plugin/` contains the IntelliJ Platform integration for Windows and
  Linux, pinned to the 2025.3 platform line.

## Tests

Run the full test suite from the repository root:

```bash
.venv/bin/python -m pytest -ra
```

Run the fast, credential-free smoke subset:

```bash
.venv/bin/python -m pytest -m offline_smoke -q
```

Run the static error gate:

```bash
.venv/bin/python -m ruff check .
```

Run the GUI bridge tests:

```bash
cd gui && npm test
```

Prompt-specific tests ensure the runtime uses the top-level `prompts/` directory
and that the documented JSON action schemas stay synchronized with the parser.
