#!/usr/bin/env node
import { render } from "ink"
import path from "node:path"
import { parseArgs } from "node:util"

import { resolveCoreCommand } from "./core.js"
import { sessionOp } from "./session.js"
import { App } from "./ui/app.js"

const { values } = parseArgs({
  options: {
    workspace: { type: "string" },
    role: { type: "string", default: "auto" },
    provider: { type: "string" },
    "fast-model": { type: "string" },
    "smart-model": { type: "string" },
    "planner-model": { type: "string" },
    "approval-mode": { type: "string", default: "ask" },
  },
})

if (!process.stdin.isTTY || !process.stdout.isTTY) {
  console.error("kvist-tui needs an interactive terminal.")
  process.exit(2)
}

const workspace = path.resolve(values.workspace ?? process.cwd())
const command = resolveCoreCommand()
const models =
  values["fast-model"] && values["smart-model"] && values["planner-model"]
    ? { fast: values["fast-model"], smart: values["smart-model"], planner: values["planner-model"] }
    : undefined

// A launch always opens its own session (UI_API.md §8a).
const launched = await sessionOp(command, workspace, "launch")
if (!launched.ok) console.error(`session launch failed: ${launched.error ?? "unknown error"}`)

const app = render(
  <App
    command={command}
    workspace={workspace}
    provider={values.provider}
    models={models}
    role={values.role ?? "auto"}
    approvalMode={values["approval-mode"] ?? "ask"}
  />,
  { exitOnCtrlC: false },
)
await app.waitUntilExit()
// exitCode is set by /tui so the launcher can tell a switch from a normal quit.
process.exit()
