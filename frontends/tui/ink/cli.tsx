#!/usr/bin/env node
import { render } from "ink"
import path from "node:path"
import { parseArgs } from "node:util"

import { resolveCoreCommand } from "./core.js"
import { farewellText } from "./farewell.js"
import { kittyGraphics } from "./kitty.js"
import { probeTerminal } from "./probe.js"
import { Pointer } from "./pointer.js"
import { sessionOp } from "./session.js"
import { routeWarningsToLog } from "./warnings.js"
import { App } from "./ui/app.js"
import { SKINS, applySkin } from "./ui/theme.js"

const { values } = parseArgs({
  options: {
    workspace: { type: "string" },
    resume: { type: "string" },
    role: { type: "string", default: "auto" },
    provider: { type: "string" },
    "fast-model": { type: "string" },
    "smart-model": { type: "string" },
    "approval-mode": { type: "string", default: "ask" },
    skin: { type: "string", default: "ink" },
  },
})

const skin = SKINS.find((name) => name === values.skin)
if (!skin) {
  console.error(`Unknown skin '${values.skin}'; expected one of: ${SKINS.join(", ")}.`)
  process.exit(2)
}
applySkin(skin)

if (!process.stdin.isTTY || !process.stdout.isTTY) {
  console.error("kvist-tui needs an interactive terminal.")
  process.exit(2)
}

const workspace = path.resolve(values.workspace ?? process.cwd())
routeWarningsToLog(path.join(workspace, ".kvist", "logs", "tui.log"))
const command = resolveCoreCommand()
const models =
  values["fast-model"] && values["smart-model"]
    ? { fast: values["fast-model"], smart: values["smart-model"] }
    : undefined

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

// The claude skin takes the whole screen and the mouse (see pointer.ts); the ink
// skin keeps the terminal's scrollback and native selection.
const fullscreen = skin === "claude"
// kitty's protocol is recognised from the environment; Sixel has to be asked for, before Ink owns the input.
const probe = fullscreen && !kittyGraphics(process.env) ? await probeTerminal(process.stdin, process.stdout) : null
const pictures = kittyGraphics(process.env) ? "kitty" : probe?.sixel ? "sixel" : null
const pointer = fullscreen ? new Pointer(process.stdin, process.stdout, { pictures, cell: probe?.cell ?? null }) : null
let lastSession = ""

const app = render(
  <App
    command={command}
    workspace={workspace}
    resume={values.resume}
    provider={values.provider}
    models={models}
    role={values.role ?? "auto"}
    approvalMode={values["approval-mode"] ?? "ask"}
    skin={skin}
    pointer={pointer}
    onSession={(name) => (lastSession = name)}
  />,
  pointer
    ? { exitOnCtrlC: false, alternateScreen: true, stdin: pointer.stdin, stdout: pointer.stdout, kittyKeyboard: { mode: "auto" } }
    : { exitOnCtrlC: false, kittyKeyboard: { mode: "auto" } },
)
pointer?.start()
await app.waitUntilExit()
pointer?.stop()
if (fullscreen) {
  // The alternate screen is gone; leave a way back. A session nothing happened in has
  // nothing to come back to: the core reports an empty journal and no checkpoint for it.
  const listed = lastSession ? await sessionOp(command, workspace, "list") : null
  const used = listed?.session_items?.find((item) => item.name === lastSession)
  const resumable = used && (used.journal_bytes > 0 || used.has_checkpoint) ? lastSession : null
  process.stdout.write(farewellText(resumable, process.stdout.columns ?? 80))
}
// exitCode is set by /tui so the launcher can tell a switch from a normal quit.
process.exit()
