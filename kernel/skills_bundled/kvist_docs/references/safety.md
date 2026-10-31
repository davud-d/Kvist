# Sandbox, approvals, secrets, project instructions

## What is guarded

Every file write and shell command goes through one executor. Writes and commands stay
in the workspace; read-only actions may use extra directories from `--allow-read DIR`.
`.env` files are never read or written by the agent.

## Shell sandbox

- **Linux**: commands run in Bubblewrap with a private empty HOME and `/tmp`, `/home` and
  `/root` hidden, protected secret paths masked (`.env*`, `.ssh`, `.aws`, `.gnupg`, `.kube`,
  keys and certificates such as `*.pem`, `*.key`), and **no network** by default. Each
  command has its own network namespace, so a server started by one command is not reachable
  from another. `AGENT_SANDBOX_MODE=auto|workspace|bubblewrap`; `workspace` is weaker (no
  secret masking). `AGENT_SANDBOX_NET=allow` opens the network.
- **Without Bubblewrap** (Windows, Linux lacking it): there is no OS boundary for the
  network. Commands still run, and Kvist warns at start and in every approval.
  Arbitrary-code commands on Linux without a trusted Bubblewrap need an approval each time.
- A command may ask for two separate one-time permissions, `network: true` and
  `sandbox: "host"`; each needs a reason and its own confirmation and cannot be remembered.

## Approvals

- A dangerous command, an overwrite of an existing file, or a command outside the rules
  opens an approval: run once, "don't ask again" (saves a rule), or refuse. "Don't ask
  again" is never offered for an intrinsically dangerous command.
- With no terminal or interface to answer (headless, `--approval-mode deny`) approvals are refused.
- `fetch_url` opens well-known documentation hosts without asking. Any other host asks once:
  yes, always for this host (saved as `web_domains`), or no. Local and private addresses
  are never fetched. `web_search` results are not filtered; fetching a link is what asks.

## Permission rules

`<workspace>/.kvist/permissions.json` holds `allow`, `deny`, `ask`, `host` patterns and `web_domains`.
Deny rules and built-in hard blocks always win over allow. A rule is the start of a command:
`npm run` also covers `npm run build`, `npm run *` every command that begins with `npm run`;
for `allow` and `host` the name must be written out, a lone `*` never matches.
Only the person edits the rules, with `/permissions allow|host|ask|deny|remove <command>`;
`host` commands always run outside the sandbox with the real HOME and no secret masking
(the person confirms the rule once), network stays a separate permission.
`/permissions check <command>` explains the decision for a command without running it.

## Auto mode

`/permissions auto on` (the person confirms a warning) lets a second model, the session's fast
model, answer the approval questions: commands no rule decides, file overwrites, host and network
access, fetching from unknown hosts, skill and MCP permissions. It says allow, deny or unsure.
Unsure asks the person, who has 2 minutes; no answer is no. It is **not safe**: it can be wrong
or be talked into a mistake by text in files, pages and output. It never overrides deny rules or
hard blocks, never approves an intrinsically dangerous command, never saves a rule. If an action
is refused by it, the refusal covers the effect: do not look for another way to the same result,
tell the person what is needed and let them decide. `/permissions auto off` turns it off.

## Undo

Agent edits are journaled. `/changes` lists them, `/diff` shows the session's, `/revert`
restores files; it overwrites only content the agent itself wrote, so the person's own
later edits are not clobbered. Pictures from image generation and files in the scratchpad
are not in the journal.

## Project instruction files

`KVIST.md` (coder, reader, devops) and `KVIST-CHAT.md` (chat) are created in the workspace
on the first launch. Put build commands, layout and conventions there; the agent reads
them every step. They are the project's files, so Kvist quotes them as untrusted data that
can add conventions but can never lift a safety rule.

## Honest limits

Kvist is a supervised tool, not a security boundary: transitive imports and helpers
spawned by trusted system tools are not individually checked, and `workspace` sandbox mode
has no OS isolation. Tell the person to read the diff and the command before accepting.
