# Configuration and behavior

Reference for settings, providers, the sandbox and headless use. For what Kvist
is and how to install it, see the [README](../README.md).

Global settings live in `~/.kvist/config.json`. Set `KVIST_CONFIG_DIR` to keep the
file in another folder. Per-project state is under `<workspace>/.kvist/`
(sessions, change journal, logs, `permissions.json`). Do not commit either one.

## Providers

`/provider` selects and configures a provider. Available:

- AnyModel (the default on a blank config), OpenAI, Anthropic, DeepSeek,
  OpenRouter, OmniRoute (a local OpenAI-compatible gateway on
  `http://localhost:20128/v1`) and FreeFlow LLM (beta);
- ChatGPT Plus/Pro through browser sign-in, with no API key;
- 36 other OpenAI-compatible services (Qwen, Moonshot, Mistral, Groq, xAI,
  Together, Cohere and others; the full list is `MANUAL_OPENAI_PROVIDERS` in
  `kernel/llm_clients.py`);
- any endpoint of your own, OpenAI-compatible or Anthropic-compatible.

Every provider has a **fast** model for routine steps and a **smart** model for
complex tasks, parse errors and stalls. Choose them with `/models`. `/change-api`
replaces the key. `AGENT_LLM_PROVIDER` overrides the saved provider for one launch.

### AnyModel

If `ANYMODEL_API_KEY` is set, Kvist starts without prompts. Otherwise the first
launch shows a screen: Enter opens the registration page and asks for the key,
Esc goes to the provider list. The key is checked against AnyModel's catalog
immediately, so a wrong key fails at once. The same endpoint returns the
per-model billing coefficient used for cost tracking:

```bash
curl -H "Authorization: Bearer $ANYMODEL_API_KEY" https://anymodel.org/v1/models
```

### ChatGPT Plus/Pro

Select **ChatGPT Plus/Pro** in `/provider` (id `chatgpt`), or in the web or
desktop provider settings, and choose **Continue with ChatGPT**. Your system
browser opens OpenAI's sign-in page. You need an eligible Plus or Pro plan and
must allow plan usage for Kvist. No OpenAI API key is involved.

Kvist follows OpenAI's open-source Sign in with ChatGPT flow: dynamic client
registration, authorization code with PKCE and a temporary callback at
`http://127.0.0.1:<port>/auth/callback`. The listener binds to loopback only and
closes on success, cancellation or after three minutes. Tokens are stored in the
`chatgpt_oauth` section of `~/.kvist/config.json` (atomic writes, mode `0600` on
Unix). Kvist does not import credentials from Codex or OpenCode, and OAuth
credentials are excluded from configuration sync, so sign in on each computer.
On a remote server, sign in on that machine.

Requests go to `https://api.openai.com/v1/responses` with `store=false`. A reply
counts as complete only after `response.completed`; interrupted streams and
plan-limit errors are shown as errors. Requests carry a stable `prompt_cache_key`
so supported models can reuse an unchanged prefix, and cached-token counts from
the server are kept in the usage figures. Caching does not remove requests, and
cached input still counts toward rate limits.

Usage counts against your ChatGPT plan and is shared with other apps that use it.
Check limits at [ChatGPT Settings, Usage](https://chatgpt.com/settings/usage).
Kvist cannot read your ChatGPT conversations. `/change-api` starts
reauthorization. Headless operations take a JSON payload:

```bash
python -m kernel --config-op chatgpt-login --config-payload '{}'     # browser sign-in
python -m kernel --config-op chatgpt-accounts --config-payload '{}'  # identities, no tokens
python -m kernel --config-op chatgpt-logout --config-payload '{}'    # revoke the session
# {"new":true} adds an account; {"account":"<id>"} selects or reauthorizes one.
```

If remote revocation cannot be confirmed, Kvist signs out locally and tells you
to disconnect the app in ChatGPT Settings.

## Models, context and reasoning

**Context budget.** The prompt budget comes from the active model's documented
maximum *input* size (`kernel/context_budget.py`), not from its context window.
OpenAI reserves 128k of the window for output, so a 400k-window GPT-5 model
rejects prompts over 272k; Anthropic limits only the input. The budget is 85% of
the input limit, because tiktoken under-counts for the Anthropic and DeepSeek
tokenizers: a 1M model gets 850k and a 272k model gets 231k. Models newer than
the built-in table inherit the limit of their family. If a provider still
rejects a prompt as too long, Kvist lowers the budget for that model and resends.
`AGENT_MAX_CONTEXT_TOKENS` pins a value. `/context` shows current usage.

**`/settings-api`** edits transport settings saved under `api_settings` in the
config: minimum gap between requests, timeout, retry count and backoff, the
prompt budget (`auto`, `1m`, `250k`), loading the model catalog from `/v1/models`
at launch, the in-run response cache and model fallbacks. Only values that differ
from the defaults are stored. `AGENT_API_INTERVAL` and `AGENT_MAX_CONTEXT_TOKENS`
win over saved values.

**Reasoning effort.** `/effort` sets `false`, `low`, `medium` (default), `high`,
`xhigh` or `max`, and Kvist translates it for each provider. DeepSeek maps
`medium` and `xhigh` to `high`; `/effort false` turns thinking off. A higher
level does not guarantee fewer tool calls or a better result. If a provider
rejects the setting, Kvist retries without it and warns. `AGENT_REASONING_EFFORT`
overrides the saved level for one launch.

**Native tool calling.** On the first task of a launch Kvist sends one small
request to see whether the model calls tools natively. If it does, the run is one
growing conversation of calls and results. If not, or if the provider refuses
tools in the middle of a run, the run uses Kvist's JSON protocol.
`AGENT_NATIVE_TOOLS=0` forces the JSON protocol.

## Sessions

Session names have 1 to 64 lowercase letters, digits, `-` or `_` and start with
a letter or digit. Each session keeps its own history and checkpoint. A launch
starts a fresh session; `/new` does the same and keeps the old one reachable
through `/session`. A checkpoint is written after every step that changes
something and after every batch of reads, so `/resume` can continue an
interrupted run. The change journal behind `/changes` and `/revert` covers the
whole workspace and restores a file only if the agent wrote its current content.

## Sandbox and permissions

Writes and shell commands stay inside the workspace. Read-only actions may use
extra directories given with repeatable `--allow-read DIR`.

- **Linux.** Commands run in Bubblewrap with a private HOME and `/tmp`, hidden
  `/home` and `/root`, protected secret paths masked, and no network.
  `AGENT_SANDBOX_MODE` is `auto`, `workspace` or `bubblewrap`. `workspace` is
  weaker: no secret masking and no isolation of `.kvist`.
- **macOS.** `/usr/bin/sandbox-exec` provides offline execution on the host.
- **Windows.** There is no offline host backend. A command that needs one is
  refused instead of running with network access.

A command may ask for two separate one-time permissions: `sandbox: "host"` (host
tools and files) and `network: true`. Each needs a justification and its own
confirmation, project allow rules do not silence them, and neither can be
remembered. Host mode keeps a private HOME, a sanitized environment and command
validation, and grants no administrator rights.

`<workspace>/.kvist/permissions.json` holds `allow`, `deny` and `ask` patterns.
Deny rules and built-in blocks always win. With no terminal or frontend to answer,
approvals are refused. `fetch_url` opens well-known documentation hosts without
asking; for any other host it asks once: yes, always yes for this host (saved as
`web_domains`), or no.

## Project instruction files

`KVIST.md` (coder, reader, devops) and `KVIST-CHAT.md` (chat) are created from a
template in the workspace root on the first launch and appended to the role's
system prompt on every step. If the workspace has a `.gitignore`, both names are
added to it once; without one nothing is written.

The files are part of the project, so Kvist treats them as untrusted: they are
quoted into the prompt as delimited data, with instructions around them that
limit them to commands, layout and conventions. This makes a hostile file harder
to use and easier to notice. It is not a guarantee. The real limits are the ones
that never read the prompt: command validation, secret masking, the workspace
boundary and approvals.

## Terminal interfaces

The Ink interface (`frontends/tui/ink/`, Node.js 22+) is a separate process that
drives the Python core. The release installer provides Node.js and the built UI.
From source:

```bash
(cd frontends/tui/ink && npm install && npm run build)   # frontends/tui/ink/dist-ui/kvist-tui.mjs
.venv/bin/python -m kernel --frontend tui      # Ink
.venv/bin/python -m kernel --frontend claude   # Claude Code style skin of the same app
.venv/bin/python -m kernel --frontend terminal # classic terminal
```

`/tui` saves the choice. If Node.js, the built file, a provider or a TTY is
missing, Kvist starts the classic terminal and says why. The Claude-style skin
uses the alternate screen, so it scrolls itself (`PgUp`/`PgDn`,
`Ctrl+Home`/`Ctrl+End`, mouse wheel) and handles the mouse: drag selects, double
click selects a word, triple click a line, and `Shift` plus drag uses the
terminal's own selection. The welcome mascot is drawn as an image in terminals
with the kitty graphics protocol or Sixel, and as block characters elsewhere;
`KVIST_GLYPHS=octants` or `quadrants` overrides the choice.

A dangerous command or a file overwrite opens an approval screen: arrows or 1/2/3
to choose, Esc to refuse. "Don't ask again" saves a rule to `permissions.json`
and is never offered for a dangerous command. Enter sends, Esc twice or `Ctrl+C`
stops a run, `Ctrl+D` quits.

## Headless use and the protocol

Configure a provider once in a terminal, then drive Kvist from another process:

```bash
.venv/bin/python -m kernel --workspace /path/to/project --frontend stdio
```

The stream is newline-delimited JSON. The first event is `hello`; then come
status, streamed text, approval requests, errors and the final answer. Replies to
`input_request` are JSON objects on stdin. The complete protocol is in
[`UI_API.md`](UI_API.md).

```bash
.venv/bin/python -m kernel --workspace DIR --config-info             # saved provider and models, no LLM call
.venv/bin/python -m kernel --workspace DIR --session-op status       # also: create, switch, list
.venv/bin/python -m kernel --workspace DIR --journal-op changes      # also: revert
echo '{"files": [{"path": "app.py", "patch": "+print(1)"}]}' \
  | .venv/bin/python -m kernel --commit-message
```

Per-run overrides: `--provider`, `--fast-model`, `--smart-model` (give both
models together), `--role`, `--approval-mode ask|deny`. `--resume` continues the
active checkpoint without reading a goal; `--resume SESSION_ID` selects a
session by its id (the file name without `.json`).

## Environment variables

| Variable | Effect |
| --- | --- |
| `AGENT_LLM_PROVIDER`, `AGENT_FAST_MODEL`, `AGENT_SMART_MODEL` | Provider and models for one launch |
| `AGENT_REASONING_EFFORT` | Reasoning effort for one launch |
| `AGENT_MAX_STEPS` | Steps before Kvist asks whether to continue (default 150) |
| `AGENT_MAX_CONTEXT_TOKENS` | Pins the prompt budget |
| `AGENT_NATIVE_TOOLS` | `0` forces the JSON protocol |
| `AGENT_SANDBOX_MODE`, `AGENT_SANDBOX_NET` | Sandbox type and network policy |
| `AGENT_VISION_MODEL` | Image-understanding model on the same provider |
| `AGENT_VERBOSE`, `AGENT_PERSIST_LOGS` | Diagnostics and run logs |

Prefer `/provider`, `/models`, `/role` and `/effort` for interactive setup.
