# frontends/zed/ — Kvist as a Zed external agent (experimental)

⚠️ **Status: experimental integration with limitations**

The Zed integration works, but is **heavily limited by Zed's public Extension API**.
Agent Client Protocol v1 and Zed's Extension API do not provide many capabilities
available in VS Code and JetBrains. This integration exists for those who want to
try Kvist in Zed while understanding its limitations.

Kvist runs inside Zed over the **Agent Client Protocol**. This is not a Zed
extension: there is no `extension.toml` here and no claim of any extension API.
Zed deprecated extension-provided agents in v1.5.0 and points at the ACP
Registry instead, and the way to run your own agent is to register it as an
[external agent](https://zed.dev/docs/ai/external-agents).

| File | What it is |
| --- | --- |
| `settings-example.jsonc` | The exact `agent_servers` member to paste into Zed's settings. |
| `README.ru.md` | The same document in Russian. |
| `icon.svg` | A 16×16 mark. `agent_servers` has no icon field, so the new-thread menu shows the agent's name only. An ACP Registry listing does take `icon.svg` (16×16 recommended), which is what this mark is for. |

## Install and launch

```bash
python scripts/installer/install_cli.py                  # builds/installs kvist-core first
python scripts/installer/install_zed.py                  # writes one agent_servers member
python scripts/installer/install_zed.py --uninstall      # removes it again
```

`install_zed.py` edits Zed's settings surgically and refuses to write an entry
pointing at a binary that is not installed yet; see
[`scripts/installer/README.md`](../../scripts/installer/README.md#how-zed-is-configured). Zed
re-reads its settings on its own — no restart.

To run the agent by hand instead:

```bash
/path/to/kvist-core --acp
```

`--acp` speaks JSON-RPC on stdin/stdout. The working directory comes from ACP
`session/new`, so `--workspace` is not used. `--login` runs the provider-credential
setup in that terminal and exits — secrets never travel over the ACP connection.

## How Zed launches the agent

Zed spawns an `agent_servers` entry as `<login shell> -c '<command> <args>'`, where
the login shell is `$SHELL` — on this machine, fish. It passes no `--no-config`, and
the agent cannot pass one either: the wrapper shell starts and finishes its own
startup before the binary exists. (Zed 1.20.2, `crates/agent_servers/src/acp.rs` —
`ShellBuilder::new(&Shell::System, …).non_interactive()`.)

That startup writes to the same stdout the protocol speaks on. A greeting, a prompt
tool or a system-info banner puts those bytes ahead of the first JSON-RPC frame, and
Zed refuses the whole stream as invalid UTF-8 — the log says `stream did not contain
valid UTF-8` and the toast says `response to 'initialize' never received: oneshot
canceled`. Neither names the shell; both are what an unparsable stream looks like.

The entry's `env.XDG_CONFIG_HOME` points at a directory of the agent's own, so the
wrapper shell finds no `config.fish` or `config` of the user's to load. The user's
shell startup files are never edited — they stay exactly as they are, and the shell
in any other terminal is untouched. fish drops its own skeleton into that directory
on first launch, which runs nothing under `fish -c`.

What this costs is that tools the agent spawns also stop reading
`~/.config/<tool>/config`. The agent's own settings are unaffected: they live in
`~/.kvist/`, outside XDG.

## Parity with the VS Code and JetBrains integrations

### 1. Full behavioural parity

Identical behaviour, whatever the host renders it with:

| Capability | Notes |
| --- | --- |
| The whole action loop | Plan, execute, feed back, finish. The same `LocalAgent`, `SafeExecutor` and session store as the terminal — ACP is another frontend over them, not a second agent. |
| Writing files | Same sandbox, same workspace boundary, same `.env`/`.kvist` refusals, same change journal. |
| Dangerous commands and overwrites | Same rules, same outcomes. A refusal stays a refusal; project policy cannot unlock an intrinsic one. |
| Cancel a turn | `session/cancel` ends the turn and seals the session exactly like Ctrl+C in the terminal. |
| Session identity | Session ids are stable and derivable (`workspace` + session name), so an id from yesterday still loads after a restart. Nothing is a throwaway UUID. |
| Session lifecycle | New, list, load, resume, delete, close. A resumed run continues the conversation it belongs to. |
| Slash commands | The same registry and the same handlers as the terminal and the TUI, including `/commit-message`. |
| Role, reasoning effort | The same knobs and the same effect on the run. |
| Plan before editing | The agent may offer "plan first or start right away"; the proposed plan arrives as a message, and accepting or rejecting it is a two-button question. |
| Commit messages | `/commit-message` describes the workspace diff. It never stages, commits or pushes. |
| Secrets | Provider keys are entered in a terminal (`kvist --login`) and stored in `~/.kvist/config.json`. ACP form elicitation is forbidden for sensitive data, so no key is ever asked for, logged, or written to session history over this protocol. |

### 2. Native Zed/ACP equivalent

Same capability, delivered through ACP and drawn by Zed rather than by Kvist:

| Kvist in VS Code / JetBrains | In Zed |
| --- | --- |
| Streaming chat reply | `session/update` text deltas |
| Reasoning / thought display | ACP thought chunks (Zed chooses how to show them) |
| Proposed plan | A message with the plan's Markdown (ACP's plan update is a status list and cannot hold free text) |
| Tool-call cards | ACP tool calls with `ToolKind` and `ToolCallLocation` |
| Live shell output | Tool-call output updates; cumulative snapshots are reconciled into deltas |
| Background shell jobs (`run_shell` with `background: true`) | One ACP tool call per job: `in_progress` with output updates (the tail snapshot replaces `rawOutput`), then `completed` (exit code 0, or stopped) or `failed` (non-zero exit, timeout). The runtime's notes to the model (`job_notice`) are not sent to Zed. |
| Confirmations, choices, free-text questions | `session/request_permission` and elicitation. A choice with exactly two answers (plan first or not, accept the plan or not) is shown as permission buttons when the client has no form elicitation; a longer choice or a free-text question needs form elicitation and is otherwise refused. |
| Open a terminal on Kvist | `/terminal`: ACP `client.create_terminal` when the client offers it, otherwise the exact command line to run yourself |
| Open a file from a result | `ToolCallLocation` paths that Zed can jump to |
| Role / effort / verbose pickers | ACP Session Config Options: `role` (`mode` category), `effort` (`thought_level` category) and `verbose` (boolean or select on older clients) |
| Style selection | ACP Session Config Option `style` (select with the styles from `prompts/styles/agents/`; the chat style follows from the pick) |
| Model, provider, API settings | Session Config Option `advanced` (menu "⚙️ Advanced") opens a terminal with dialogs `/models`, `/provider`, `/settings-api`, custom provider creation |

### 3. Not reproducible literally — no public Zed API

**Extension API limitations are the main reason this integration remains experimental.**

| Missing | Why | Nearest native equivalent |
| --- | --- | --- |
| **Inline completion** (`kvist --complete` as ghost text) | Zed's `edit_predictions.provider` accepts only `zed`, `copilot`, `mercury`, `codestral`, `ollama`, `open_ai_compatible_api` or `none` ([Edit Prediction](https://zed.dev/docs/ai/edit-prediction)). There is no value for an external agent, and NES is not part of ACP v1 — it exists only as an [RFD](https://agentclientprotocol.com/llms.txt). | Run `kvist --complete` from a terminal or a script. Zed's own route for a third-party model is an OpenAI-compatible `/v1/completions` server; Kvist does not currently ship one. |
| **A Kvist side panel** (transcript, banner, live telemetry) | No public extension API for an arbitrary side webview ([Extensions](https://zed.dev/docs/extensions) offers languages, themes, snippets, debugger adapters, MCP servers). | Zed's own agent panel, fed by ACP updates. |
| **A commit-message button in the SCM UI** | No public API for a source-control button. | `/commit-message` in the agent panel, or `kvist --commit-message` in a terminal (the VS Code and JetBrains plugins call that same command). |
| **Kvist's own cards, themes and status banner** | They live in the terminal UI, the Ink TUI and the Electron GUI. | Zed's rendering of ACP updates. |
| **Zed Agent profiles and Skills applying to Kvist** | They configure Zed's built-in agents, not external ACP agents ([External Agents](https://zed.dev/docs/ai/external-agents)). | `KVIST.md` / `KVIST-CHAT.md` in the workspace, and the style prompts. |
| **Configuring the provider with Zed's API-key UI** | Zed's key settings configure Zed's models, not an external agent's provider. | `kvist --login`, or the terminal UI's provider picker. |
| **A Zed Extension Registry listing** | Extension-provided agents are deprecated ([Agent Server Extensions](https://zed.dev/docs/extensions/agent-servers)); a listing would belong in the ACP Registry, and Kvist is not published there. | This directory's manual registration. |
| **Audio or image prompts** | Not implemented in `acp_frontend.py`, so they are not advertised — `PromptCapabilities(image=False, audio=False)`. | Text and file paths only. |
| **Next-edit suggestions from the agent** | Not in ACP v1 (see above). | None. |
| **The closed-session bar in place of the input** (after `end_session`) | Zed draws its own message box; ACP gives an agent no way to hide or replace it. | The core's lock: every prompt to a closed session is answered with the fixed closed-session reply and `stopReason: "end_turn"`, before any model call. Not `refusal`: Zed answers that by truncating the thread back to before the person's message ([`acp_thread.rs`](https://github.com/zed-industries/zed/blob/main/crates/acp_thread/src/acp_thread.rs)), which would erase the reply that explains the lock, and marks the turn as an error. A new thread starts a new session. |
| **Automatic command execution in terminal** | `create_terminal` in ACP supports `initial_input`, but Zed may ignore it or handle it differently depending on version. | Manually enter commands in the opened terminal. |

## What the agent advertises

`acp_frontend.py` declares only what works end to end: `load_session`, session
list/delete/close/resume, additional directories, embedded context, and terminal
authentication. It declares **no** NES, no MCP servers, no model providers, and
no image or audio prompts.

## Sources

Re-checked on 2026-09-26 before this was written, and again on 2026-09-27 for
the `agent_servers` fields and the registry's icon support:

* <https://zed.dev/docs/ai/external-agents> — now only a deprecation notice
  pointing at the ACP Registry; the `agent_servers` fields are documented on
  the agent-servers page below.
* <https://zed.dev/docs/extensions>
* <https://zed.dev/docs/extensions/agent-servers>
* <https://zed.dev/docs/extensions/capabilities>
* <https://zed.dev/docs/ai/edit-prediction>
* <https://agentclientprotocol.com/llms.txt>
* <https://agentclientprotocol.com/protocol/v1/overview>
* <https://agentclientprotocol.com/protocol/v1/schema>
* <https://agentclientprotocol.com/protocol/v1/libraries/python>
* <https://agentclientprotocol.com/get-started/registry.md>
* <https://github.com/agentclientprotocol/registry>

Re-checked on 2026-09-30 for the closed-session reply:
`agent-client-protocol` 0.12.1 (`StopReason = end_turn | max_tokens |
max_turn_requests | refusal | cancelled`),
<https://agentclientprotocol.com/protocol/prompt-turn> ("refusal: The Agent
refuses to continue"), and Zed's `crates/acp_thread/src/acp_thread.rs` on
`main`, which truncates a refused user prompt out of the thread.
