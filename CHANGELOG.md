# Changelog

[Русская версия](CHANGELOG.ru.md)

Everything since the tag `v0.1.0b1` (25 September 2026), summarised from
`git log v0.1.0b1..HEAD`: 754 commits, 289 of them `feat` and 173 `fix`, the rest
docs, tests, CI and style. Kvist is in beta and has no newer tag yet.

## Unreleased

### Added

- **Agent loop.** Batches of actions with several shell commands side by side,
  background shell commands (`job_output`, `job_stop`), waiting and scheduling
  that wake the agent by time, model-offered plan mode, ten helpers, best-of and
  council runs in their own worktrees, a live context meter and `/context`.
- **Memory and notes.** Session recaps and background reading so Kvist remembers
  past sessions; `KVIST.md` as Kvist's own project notebook; skill storage on
  SQLite.
- **Providers.** Sign in with a ChatGPT Plus/Pro plan, waiting out a spent plan
  window, native tool calling through the Anthropic adapter, Kimi Code, Inception,
  extra HTTP headers for custom providers (`/headers`), a bundled per-model price
  table.
- **Sandbox and approvals.** Container mode for benchmark tasks, auto mode that
  remembers allows and pre-approves skills, and a net broker for skill network access.
- **Skills.** A skills platform in every frontend, bundled browser, design,
  visualize, desktop, documents, Gmail, Telegram and `kvist_docs` skills,
  `app_screen` for graphical programs on the agent's own hidden screen, and the
  `/website` command.
- **Interfaces.** OpenCode's terminal interface (`--frontend opencode`), Glass
  (a Rust terminal interface over a photo), a voice frontend, Kvist Desktop,
  a tablet layout for the phone page, an Android companion app, a first-run setup
  screen and an installer screen for `curl | bash`, a web client, a Zed
  integration, inline completion and commit message generation in the IDE plugins.
- **Remote and platforms.** A core on a server that outlives its frontends over
  ssh (PuTTY's `plink` on Windows), an experimental FreeBSD, OpenBSD and NetBSD
  run, a Termux build.
- **Feedback.** `/feedback` drafts a GitHub issue, shows it as a letter and sends
  it only after you agree.
- **Kvist Cloud.** A hosted service with plans, a trial, server-side ChatGPT
  sign-in, per-machine provisioning and an admin panel (AGPL, under `cloud/`).

### Fixed

- **Providers.** Reasoning-effort and `tool_choice` rejections from custom gateways
  and DeepSeek no longer break a run; the model is reselected after a provider
  switch; a stream that loses a call's arguments is asked again without streaming.
- **Sandbox.** Memory limits for child processes, root-owned system binaries are
  trusted when running as root, `sandbox=host` commands get your real `HOME`,
  `plink` never reaches a host you have not confirmed, shell works from the home
  directory.
- **Windows.** Child processes no longer inherit the core's stdin (a stall in
  best-of), paths in imported Cursor and Continue sessions, uninstalling skills.
- **Sessions.** A typed follow-up stays in the journal of an unfinished task.
- **Interfaces.** Plan dialog shows the proposed plan, scrollbars for dark IDE
  themes, the update notice is a toast and not a reply, and many smaller screen
  and input fixes in the terminal, Glass, web and IDE frontends.
- **Installers and CI.** VS Code extension install retries without `--force`,
  Termux build dependencies and timeouts, platform-dependent tests.
