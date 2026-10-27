# Commands, keys and flags

Type `/` in the input to see the list; `/commands` prints it. Only the commands below
exist; do not invent subcommands. Some interfaces offer a few more of their own.

## Slash commands

| Command | Effect |
| --- | --- |
| `/help` (`/about`, `/info`) | Information and platform support (keys in Ink interfaces) |
| `/commands` (`/cmds`) | All commands |
| `/new` | Start a new session; the old one stays reachable through `/session` |
| `/session` | Create, switch or delete saved sessions |
| `/resume` | Continue the latest interrupted run from its checkpoint |
| `/export` (`/import`) | Import sessions from Claude Code, Codex, OpenCode, Cursor, Qwen Code, Continue, Aider |
| `/provider` | Switch or configure the provider |
| `/change-api` (`/apikey`) | Replace the key of the current provider (reauthorizes ChatGPT) |
| `/models` | Choose the fast and smart models |
| `/settings-api` | Request pacing, timeouts, retries, context window, fallbacks, catalog loading |
| `/del-provider` | Delete the active custom provider |
| `/full-reset` | Wipe all saved providers and keys |
| `/role` | `auto`, `coder`, `reader`, `devops`, `chat` (and `universal`) |
| `/effort` | Reasoning effort: `false`, `low`, `medium`, `high`, `xhigh`, `max` |
| `/style` | Agent style (the chat style follows it) |
| `/web-search-provider` | Tavily, Brave or SearXNG for `web_search`, and its key |
| `/strime` | Toggle streaming for the active custom provider |
| `/verbose true|false` | Verbose runtime output |
| `/tui` | Choose the terminal interface for the next launch |
| `/changes` | What the agent changed, across sessions |
| `/diff` | Changes recorded in this session |
| `/revert` | Restore agent-changed files to their baselines |
| `/context` | Context size of the model and how much is used |
| `/status` | Session, role, provider, models, execution mode |
| `/permissions <command>` | Show project permission rules and explain a command's decision (does not run it) |
| `/memory` | `status`, `pause`, `resume`, `inspect <words>`, `why <id>`, `forget <id>`, `remember <text>`, `remember-global <text>`, `process`, `processing on|off` |
| `/doctor` | Check modules, interfaces, provider, keys, settings; offers a test request |
| `/bug <text>` | Save a local bug report (never sent anywhere) |
| `/bye` (`/exit`, `/quit`) | Save the session and exit |

Ink interfaces add `/rename <name>`, `/copy`, `/debug`, `/clear`.

## Keys (terminal)

- Enter sends. Multiline: Alt+Enter or Esc then Enter. Images: Ctrl+V or `/image PATH`.
- **Esc twice pauses** a running task and keeps what it owns (servers, helpers); the
  next message continues it. **Ctrl+C stops everything** and cleans up. Both keep the
  checkpoint, so `/resume` works. At an idle prompt Ctrl+C exits.
- Ctrl+D quits. In the Claude-style skin PageUp/PageDown, Ctrl+Home/End and the wheel
  scroll; Ctrl+O expands folded command output.
- An approval screen: arrows or 1/2/3, Esc refuses.

## Command line

- `kvist --workspace DIR` choose the folder. `--frontend tui|claude|terminal|glass|voice|opencode|stdio|stdio-multi`
  choose an interface for one launch.
- Per run: `--provider`, `--fast-model` and `--smart-model` (give both), `--role`,
  `--approval-mode ask|deny`, `--resume [SESSION]`, repeatable `--allow-read DIR`.
- `--doctor` (with `--yes` to answer the model test), `--setup`, `--config-info`,
  `--session-op`, `--journal-op`, `--memory-op`, `--skill-op`, `--mcp-op`, `--connection-op`,
  `--commit-message` (reads a JSON diff on stdin), `--acp` (Zed).
