#!/usr/bin/env bun
// OpenCode-style terminal interface for Kvist. Run by the launcher (`kvist --frontend opencode`)
// with the core's own command in KVIST_CORE_CMD, like the Ink interface.

import { createCliRenderer } from "@opentui/core"
import { render } from "@opentui/solid"
import path from "node:path"
import { parseArgs } from "node:util"

import { resolveCoreCommand } from "../../ink/core.js"
import { farewellText } from "../../ink/farewell.js"
import { sessionOp } from "../../ink/session.js"
import { routeWarningsToLog } from "../../ink/warnings.js"
import { App } from "./app.js"

const { values } = parseArgs({
  options: {
    workspace: { type: "string" },
    resume: { type: "string" },
    role: { type: "string", default: "auto" },
    provider: { type: "string" },
    "fast-model": { type: "string" },
    "smart-model": { type: "string" },
    "approval-mode": { type: "string", default: "ask" },
  },
})

if (!process.stdin.isTTY || !process.stdout.isTTY) {
  console.error("kvist-opencode needs an interactive terminal.")
  process.exit(2)
}

const workspace = path.resolve(values.workspace ?? process.cwd())
routeWarningsToLog(path.join(workspace, ".kvist", "logs", "opencode-tui.log"))
const command = resolveCoreCommand()
const models = values["fast-model"] && values["smart-model"] ? { fast: values["fast-model"], smart: values["smart-model"] } : undefined

// A launch always opens its own session (UI_API.md §8a).
const launched = values.resume === undefined
  ? await sessionOp(command, workspace, "launch")
  : values.resume
    ? await sessionOp(command, workspace, "switch", values.resume)
    : { ok: true }
if (!launched.ok) {
  console.error(`session launch failed: ${launched.error ?? "unknown error"}`)
  process.exit(2)
}

const renderer = await createCliRenderer({
  exitOnCtrlC: false,
  targetFps: 60,
  useMouse: true,
  useKittyKeyboard: {},
  autoFocus: false,
  openConsoleOnError: false,
})
renderer.setTerminalTitle("Kvist")
const mode = (await renderer.waitForThemeMode(1000)) ?? "dark"

let exitCode = 0
let lastSession = ""
const done = new Promise<void>((resolve) => {
  renderer.once("destroy", () => resolve())
})

await render(
  () => (
    <App
      terminalMode={mode}
      options={{
        command,
        frontend: "opencode",
        core: {
          workspace,
          role: values.role ?? "auto",
          approvalMode: values["approval-mode"] ?? "ask",
          resume: values.resume,
          provider: values.provider,
          models,
        },
      }}
      onQuit={(code) => {
        exitCode = code
        renderer.destroy()
      }}
      onSession={(name) => (lastSession = name)}
    />
  ),
  renderer,
)

await done
await renderer.closed.catch(() => {})

// The alternate screen is gone; leave a way back. A session nothing happened in has nothing
// to come back to: the core reports an empty journal and no checkpoint for it.
const listed = lastSession ? await sessionOp(command, workspace, "list") : null
const used = listed?.session_items?.find((item) => item.name === lastSession)
const resumable = used && (used.journal_bytes > 0 || used.has_checkpoint) ? lastSession : null
process.stdout.write(farewellText(resumable, process.stdout.columns ?? 80))
process.exit(exitCode)
