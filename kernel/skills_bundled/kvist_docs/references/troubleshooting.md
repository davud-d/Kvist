# When something is broken

Start with `/doctor` (or `kvist --doctor`): it checks the environment, core modules,
packages, prompts, `config.json`, `api_settings`, `permissions.json`, provider, models and
key presence (by name, never the value), and each interface's prerequisites. It writes
nothing. Only the optional last step touches the network: it asks, then sends the fast model
"Reply with the single word: pong". Exit code 1 means a check failed.

| Symptom | Likely cause and what to try |
| --- | --- |
| Starts in the plain terminal instead of the chosen look | Node.js, the built bundle, Bun, kitty graphics or a TTY is missing; Kvist printed the reason; `/doctor` lists the prerequisite |
| Asks for the key again | The key was not saved; run `/change-api` and watch for a rejected key; `ANYMODEL_API_KEY` set in the environment skips the question |
| A request is rejected as too long | Kvist lowers the budget and resends by itself; `/context` shows usage; `/new` frees it |
| Requests are slow or rate-limited | `/settings-api`: interval, timeout, retries; a cheaper fast model with `/models` |
| The model ignores tools or the JSON protocol | The run falls back by itself; weak models may need `/models` to a smarter one or `/effort` higher |
| A command was refused | `/permissions <command>` explains the decision; hard blocks (secrets, forbidden syntax, outside the workspace) cannot be allowed by a rule |
| A command cannot reach the network | The sandbox denies it by default; the command must ask for `network: true`, or set `AGENT_SANDBOX_NET=allow` |
| A tool is "not found" in a command | Only project toolchains (`.venv/bin`, `node_modules/.bin`) and system directories are on the child PATH; extra directories go in `AGENT_TOOL_PATHS` |
| A run stopped at the step limit | It asked whether to continue; `AGENT_MAX_STEPS` raises the default (150) |
| Work was interrupted | `/resume`, or just send "continue" |
| The agent changed something unwanted | `/changes`, then `/revert` |
| A new skill does not attach | A freshly installed skill works only after Kvist restarts |
| The status bar shows nothing | `tray_enabled` in the config; the bar must be Waybar or Polybar for the module |

For a bug report: `/bug <text>` saves a local report (never sent). Run logs appear in
`<workspace>/.kvist/logs/` when `AGENT_PERSIST_LOGS=1`. Request logs for a provider's
support: `KVIST_REQUEST_LOG=<file>` (redacted).
