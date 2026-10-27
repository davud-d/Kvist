# Kvist Docs

Use this when the person asks how Kvist itself works or how to do something with it:
"how do I switch the model", "why was my command refused", "where are my sessions",
"how do I undo what you did", "which interface should I use". For questions about
the person's own project, this skill is not needed.

## How to answer

1. Find the topic in the table below and read that page with `skills` op `read`
   (one page is usually enough; do not read all of them).
2. Answer the question that was asked, with the command or setting by name. Do not
   recite the page and do not give an unsolicited tour.
3. If the workspace is Kvist's own repository, `README.md` and `docs/*.md`
   (`configuration.md`, `SKILLS.md`, `UI_API.md`) are newer than these pages. A
   page here is a summary; when the two disagree, the repository wins, and when
   exactness matters (a flag, a default, a protocol field) look there.
4. Say what you do not know. If a command, flag or setting is not on the page and
   not in the repository, do not invent one from another tool's conventions
   (Claude Code's `/config`, Codex's flags). Offer `/commands` or `/help`, which list
   what this build really has.

## Pages

| Topic | Page |
| --- | --- |
| Installing, first launch, upgrading, platforms | `references/install.md` |
| Slash commands, keys, command-line flags | `references/commands.md` |
| Providers, models, reasoning effort, context size, environment variables | `references/providers-models.md` |
| Sandbox, approvals, permission rules, secrets, project instruction files | `references/safety.md` |
| Sessions, `/resume`, `/changes` and `/revert`, memory, timers and background jobs | `references/sessions-memory.md` |
| The interfaces (terminals, desktop app, browser, IDEs, Zed, voice), the protocol | `references/interfaces.md` |
| Skills, MCP servers, helpers | `references/skills-extensions.md` |
| Something is broken: `/doctor`, common failures and what to try | `references/troubleshooting.md` |

## Facts that hold everywhere

- Kvist works inside one workspace directory and keeps its state in `<workspace>/.kvist/`
  (sessions, change journal, logs, permission rules) and global settings in
  `~/.kvist/config.json`. Neither belongs in version control.
- The model is the person's choice. Each provider has a fast and a smart model; a
  run starts on fast and moves to smart once when it struggles.
- Every file write and shell command passes one executor. It is a supervised tool,
  not a security boundary: the person should read the diff and the command output.
- It is a beta. If something behaves differently from a page here, trust what the
  running program says (`/status`, `/doctor`) over the page.
